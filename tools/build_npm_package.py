from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

_LOCK_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>\S+)\s+\\?\s*"
    r"--hash=sha256:(?P<hash>[a-f0-9]{64})\s*$"
)
_PLATFORM_MARKERS = re.compile(
    r"win_amd64|manylinux|macosx|aarch64|linux_\w+|win32",
    re.IGNORECASE,
)


def _lock_requirement_lines(text: str) -> list[tuple[int, str]]:
    joined: list[tuple[int, str]] = []
    buffer = ""
    start_line = 0
    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not buffer:
            start_line = line_number
        buffer = f"{buffer} {stripped}" if buffer else stripped
        if buffer.endswith("\\"):
            buffer = buffer[:-1].strip()
            continue
        joined.append((start_line, buffer))
        buffer = ""
    if buffer:
        joined.append((start_line, buffer))
    return joined


def parse_vendor_lock(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line_number, raw in _lock_requirement_lines(text):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        normalized = stripped.replace("\\", " ").strip()
        if _PLATFORM_MARKERS.search(normalized):
            raise ValueError(
                f"platform-specific dependency entries are not allowed (line {line_number})"
            )
        if normalized.count("--hash=") > 1:
            raise ValueError(
                f"vendor lock line {line_number} must contain exactly one sha256 hash"
            )
        match = _LOCK_LINE.match(normalized)
        if not match:
            raise ValueError(
                f"vendor lock line {line_number} must pin one hash-locked package"
            )
        if stripped.count("--hash=") != 1:
            raise ValueError(
                f"vendor lock line {line_number} must contain exactly one sha256 hash"
            )
        entries.append(
            {
                "name": match.group("name"),
                "version": match.group("version"),
                "hash": match.group("hash"),
            }
        )
    if not entries:
        raise ValueError("vendor lock must list at least one dependency")
    return entries


def read_wheel_version(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        for line in archive.read(metadata_name).decode("utf-8").splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    raise ValueError(f"could not read version from wheel {wheel}")


def read_package_json_version(npm_cli_dir: Path) -> str:
    package = json.loads((npm_cli_dir / "package.json").read_text("utf-8"))
    return str(package["version"])


def vendor_inventory(vendor_dir: Path) -> list[str]:
    if not vendor_dir.is_dir():
        return []
    entries: list[str] = []
    for path in sorted(vendor_dir.rglob("*")):
        if path.is_dir():
            continue
        if path.suffix == ".pyc" or path.name == ".DS_Store":
            continue
        if "__pycache__" in path.parts:
            continue
        entries.append(path.relative_to(vendor_dir).as_posix())
    return entries


_FORBIDDEN_TARBALL_MARKERS = (
    "/vendor/bin/",
    "/vendor/Scripts/",
    "__pycache__/",
    "/test/",
)
_FORBIDDEN_TARBALL_SUFFIXES = (".exe", ".pyc", ".test.js")


def validate_packed_tarball(tarball: Path, npm_cli_dir: Path) -> None:
    package = json.loads((npm_cli_dir / "package.json").read_text("utf-8"))
    allowed_roots = {f"package/{entry.rstrip('/')}" for entry in package["files"]}
    allowed_roots.add("package/package.json")

    with tarfile.open(tarball, "r:gz") as archive:
        names = [
            member.name.replace("\\", "/")
            for member in archive.getmembers()
            if member.isfile()
        ]

    for name in names:
        if not any(name == root or name.startswith(f"{root}/") for root in allowed_roots):
            raise ValueError(f"unexpected tarball path: {name}")
        lowered = name.lower()
        for marker in _FORBIDDEN_TARBALL_MARKERS:
            if marker in lowered:
                raise ValueError(f"forbidden tarball path: {name}")
        if name.endswith(_FORBIDDEN_TARBALL_SUFFIXES):
            raise ValueError(f"forbidden tarball suffix: {name}")

    for entry in package["files"]:
        prefix = f"package/{entry.rstrip('/')}"
        if entry.endswith("/"):
            if not any(name.startswith(f"{prefix}/") for name in names):
                raise ValueError(f"missing packaged directory: {prefix}/")
        elif not any(name == prefix or name.startswith(f"{prefix}/") for name in names):
            raise ValueError(f"missing packaged path: {prefix}")


def remove_vendor_scripts(vendor_dir: Path) -> None:
    for directory in ("bin", "Scripts"):
        scripts_dir = vendor_dir / directory
        if scripts_dir.exists():
            shutil.rmtree(scripts_dir)


def remove_bytecode(vendor_dir: Path) -> None:
    for cache in vendor_dir.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for compiled in vendor_dir.rglob("*.py[co]"):
        compiled.unlink(missing_ok=True)


def _pip_install(args: list[str]) -> None:
    command = [sys.executable, "-m", "pip", "install", *args]
    subprocess.run(command, check=True)


def populate_vendor(
    wheel: Path,
    npm_cli_dir: Path,
    *,
    run_commands: bool,
) -> list[str]:
    vendor_dir = npm_cli_dir / "vendor"
    if vendor_dir.exists():
        shutil.rmtree(vendor_dir)
    vendor_dir.mkdir(parents=True)

    if not run_commands:
        return []

    lock_path = npm_cli_dir / "vendor-requirements.lock"
    parse_vendor_lock(lock_path.read_text("utf-8"))

    _pip_install(
        [
            "--disable-pip-version-check",
            "--no-input",
            "--no-deps",
            "--only-binary=:all:",
            "--target",
            str(vendor_dir),
            str(wheel),
        ]
    )
    _pip_install(
        [
            "--disable-pip-version-check",
            "--no-input",
            "--no-deps",
            "--require-hashes",
            "--only-binary=:all:",
            "--target",
            str(vendor_dir),
            "-r",
            str(lock_path),
        ]
    )
    remove_bytecode(vendor_dir)
    remove_vendor_scripts(vendor_dir)
    return vendor_inventory(vendor_dir)


def _npm_executable() -> str:
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm executable not found on PATH")
    return npm


def npm_pack(npm_cli_dir: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    before = set(output_dir.glob("*.tgz"))
    subprocess.run(
        [
            _npm_executable(),
            "pack",
            "--pack-destination",
            str(output_dir),
        ],
        cwd=npm_cli_dir,
        check=True,
    )
    created = set(output_dir.glob("*.tgz")) - before
    if len(created) != 1:
        raise RuntimeError("npm pack must create exactly one tarball")
    return next(iter(created))


def build_npm_package(
    wheel: Path,
    *,
    output_dir: Path,
    npm_cli_dir: Path,
    run_commands: bool = True,
) -> Path:
    wheel = wheel.resolve()
    npm_cli_dir = npm_cli_dir.resolve()
    output_dir = output_dir.resolve()

    if not wheel.is_file():
        raise FileNotFoundError(wheel)
    if "py3-none-any" not in wheel.name:
        raise ValueError(f"wheel must be pure Python: {wheel.name}")

    wheel_version = read_wheel_version(wheel)
    try:
        from socratic_learn import __version__ as package_version
    except ImportError:
        package_version = importlib.metadata.version("socratic-learn")
    npm_version = read_package_json_version(npm_cli_dir)

    if not (wheel_version == package_version == npm_version):
        raise ValueError(
            "version mismatch among wheel, socratic_learn.__version__, and package.json: "
            f"{wheel_version=} {package_version=} {npm_version=}"
        )

    inventory = populate_vendor(wheel, npm_cli_dir, run_commands=run_commands)
    if run_commands and not inventory:
        raise RuntimeError("vendor inventory must not be empty after install")

    if not run_commands:
        return output_dir

    tarball = npm_pack(npm_cli_dir, output_dir)
    validate_packed_tarball(tarball, npm_cli_dir)
    return tarball


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the @socraticlearn/cli npm package")
    parser.add_argument("wheel", type=Path, help="release wheel to vendor")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory that will receive the npm pack tarball",
    )
    parser.add_argument(
        "--npm-cli-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "packages" / "npm-cli",
        help="npm package directory",
    )
    args = parser.parse_args(argv)
    tarball = build_npm_package(
        args.wheel,
        output_dir=args.output_dir,
        npm_cli_dir=args.npm_cli_dir,
    )
    print(tarball)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())