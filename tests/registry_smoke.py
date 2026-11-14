"""Post-publication and local-artifact registry smoke helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_REGISTRY_RETRY_ATTEMPTS = 12
DEFAULT_REGISTRY_RETRY_DELAY_SECONDS = 15.0

_VERSION_OUTPUT_RE = re.compile(
    r"^socratic-learn\s+(?P<version>\S+)\s*$",
    re.MULTILINE,
)
_TRACEBACK_MARKERS = ("Traceback (most recent call last)", "Traceback (most recent call last):")


class RegistrySmokeError(RuntimeError):
    """Raised when registry smoke verification fails."""


@dataclass(frozen=True)
class RegistrySmokeSource:
    mode: Literal["registry", "local"]
    wheel_path: Path | None = None
    npm_tarball: Path | None = None

    @staticmethod
    def registry() -> RegistrySmokeSource:
        return RegistrySmokeSource("registry")

    @staticmethod
    def local(*, wheel: Path, npm_tarball: Path) -> RegistrySmokeSource:
        return RegistrySmokeSource(
            "local",
            wheel_path=wheel.resolve(),
            npm_tarball=npm_tarball.resolve(),
        )

    def uses_registry(self) -> bool:
        return self.mode == "registry"


CommandRunner = Callable[
    [list[str], dict[str, str], Path],
    subprocess.CompletedProcess[str],
]
RegistryProbe = Callable[[str], bool]
SleepFn = Callable[[float], None]


def checkout_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _pipx_invocation() -> list[str]:
    return [sys.executable, "-m", "pipx"]


def build_pipx_install_argv(version: str, source: RegistrySmokeSource) -> list[str]:
    if source.mode == "registry":
        return [
            *_pipx_invocation(),
            "install",
            f"socratic-learn=={version}",
        ]
    if source.wheel_path is None:
        raise RegistrySmokeError("local pipx install requires a wheel path")
    return [
        *_pipx_invocation(),
        "install",
        "--force",
        str(source.wheel_path),
    ]


def build_pipx_upgrade_argv() -> list[str]:
    return [*_pipx_invocation(), "upgrade", "socratic-learn"]


def build_pipx_uninstall_argv() -> list[str]:
    return [*_pipx_invocation(), "uninstall", "socratic-learn"]


def npm_exec_argv(
    version: str,
    command: Sequence[str],
    source: RegistrySmokeSource,
) -> list[str]:
    if source.mode == "registry":
        package = f"@socraticlearn/cli@{version}"
    else:
        if source.npm_tarball is None:
            raise RegistrySmokeError("local npm exec requires a tarball path")
        package = str(source.npm_tarball)
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "--package", package, "--", *command]
    npm = shutil.which("npm")
    if npm:
        return [npm, "exec", "--yes", f"--package={package}", "--", *command]
    raise RegistrySmokeError("npm or npx is required for registry smoke")


def learner_sentinel_path(home_root: Path) -> Path:
    return (
        home_root
        / ".socratic-learn"
        / "workspace"
        / "registry-smoke-sentinel.txt"
    )


def assert_exact_version_output(output: str, version: str) -> None:
    matches = list(_VERSION_OUTPUT_RE.finditer(output.strip()))
    if len(matches) != 1:
        raise RegistrySmokeError(
            f"expected one version line for {version!r}, got {output!r}"
        )
    found = matches[0].group("version")
    if found != version:
        raise RegistrySmokeError(f"expected version {version!r}, found {found!r}")


def assert_missing_host_guidance(
    completed: subprocess.CompletedProcess[str],
) -> None:
    combined = f"{completed.stdout}\n{completed.stderr}"
    lowered = combined.lower()
    if any(marker in combined for marker in _TRACEBACK_MARKERS):
        raise RegistrySmokeError("missing-host smoke raised a traceback")
    if completed.returncode == 0:
        raise RegistrySmokeError(
            "missing-host smoke must exit non-zero without installed hosts"
        )
    if "no compatible agent was found" not in lowered:
        raise RegistrySmokeError(
            "missing-host smoke must report that no compatible agent was found"
        )
    if "install pi, claude code, or antigravity" not in lowered:
        raise RegistrySmokeError(
            "missing-host smoke must include actionable host installation guidance"
        )


def _probe_pypi_registry(version: str) -> bool:
    url = "https://pypi.org/pypi/socratic-learn/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False
    releases = payload.get("releases") or {}
    return version in releases and bool(releases[version])


def _probe_npm_registry(version: str) -> bool:
    url = (
        "https://registry.npmjs.org/"
        f"@socraticlearn%2Fcli/{version}"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False
    return payload.get("version") == version


def _forbidden_registry_probe(label: str) -> RegistryProbe:
    def probe(version: str) -> bool:
        raise RegistrySmokeError(
            f"local artifact smoke must not query the {label} registry"
        )

    return probe


def wait_for_registry(
    version: str,
    *,
    probe_pypi: RegistryProbe = _probe_pypi_registry,
    probe_npm: RegistryProbe = _probe_npm_registry,
    sleep: SleepFn = time.sleep,
    max_attempts: int = DEFAULT_REGISTRY_RETRY_ATTEMPTS,
    delay_seconds: float = DEFAULT_REGISTRY_RETRY_DELAY_SECONDS,
) -> None:
    for attempt in range(1, max_attempts + 1):
        pypi_ready = probe_pypi(version)
        npm_ready = probe_npm(version)
        if pypi_ready and npm_ready:
            return
        if attempt == max_attempts:
            missing = []
            if not pypi_ready:
                missing.append(f"socratic-learn=={version} on PyPI")
            if not npm_ready:
                missing.append(f"@socraticlearn/cli@{version} on npm")
            raise RegistrySmokeError(
                "registry entries did not resolve before retry limit: "
                + ", ".join(missing)
            )
        sleep(delay_seconds)


def production_run_command(
    argv: list[str],
    env: dict[str, str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    safe_env = dict(env)
    safe_env.pop("PYTHONPATH", None)
    safe_env["PYTHONSAFEPATH"] = "1"
    return subprocess.run(
        argv,
        env=safe_env,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _minimal_path(*entries: Path) -> str:
    paths = [str(entry) for entry in entries]
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        paths.append(str(system_root / "System32"))
    return os.pathsep.join(paths)


def _smoke_environment(home_root: Path, pipx_home: Path, pipx_bin: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONIOENCODING", None)
    environment.pop("PYTHONUTF8", None)
    environment["PYTHONSAFEPATH"] = "1"
    learn_home = home_root / ".socratic-learn"
    learn_home.mkdir(parents=True, exist_ok=True)
    environment["SOCRATIC_LEARN_HOME"] = str(learn_home)
    environment["PIPX_HOME"] = str(pipx_home)
    environment["PIPX_BIN_DIR"] = str(pipx_bin)
    environment["PIPX_MAN_DIR"] = str(pipx_home / "man")
    environment["PATH"] = os.pathsep.join(
        [str(pipx_bin), environment.get("PATH", "")]
    )
    if os.name != "nt":
        environment["HOME"] = str(home_root)
        environment["USERPROFILE"] = str(home_root)
    return environment


def _isolated_work_cwd(*, checkout: Path, home_root: Path) -> Path:
    checkout_resolved = checkout.resolve()
    candidate = home_root.parent / "work"
    candidate.mkdir(parents=True, exist_ok=True)
    work_resolved = candidate.resolve()
    if (
        checkout_resolved == work_resolved
        or checkout_resolved in work_resolved.parents
        or work_resolved in checkout_resolved.parents
    ):
        candidate = Path(tempfile.mkdtemp(prefix="registry-smoke-cwd-"))
    else:
        for parent in (work_resolved, *work_resolved.parents):
            if parent == checkout_resolved:
                candidate = Path(tempfile.mkdtemp(prefix="registry-smoke-cwd-"))
                break
    candidate.mkdir(parents=True, exist_ok=True)
    work_resolved = candidate.resolve()
    if (
        checkout_resolved == work_resolved
        or checkout_resolved in work_resolved.parents
        or work_resolved in checkout_resolved.parents
    ):
        raise RegistrySmokeError("smoke subprocess cwd must stay outside checkout")
    return candidate


def _run_checked(
    *,
    argv: list[str],
    env: dict[str, str],
    cwd: Path,
    run_command: CommandRunner,
    context: str,
) -> subprocess.CompletedProcess[str]:
    completed = run_command(argv, env, cwd)
    if completed.returncode != 0:
        raise RegistrySmokeError(
            f"{context} failed ({completed.returncode}): "
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed


def _sentinel_survives_uninstall(home_root: Path) -> None:
    sentinel = learner_sentinel_path(home_root)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("registry-smoke-preservation-check\n", encoding="utf-8")


def _assert_sentinel_preserved(home_root: Path) -> None:
    sentinel = learner_sentinel_path(home_root)
    if not sentinel.is_file():
        raise RegistrySmokeError("learner-data sentinel was removed")
    if sentinel.read_text(encoding="utf-8") != "registry-smoke-preservation-check\n":
        raise RegistrySmokeError("learner-data sentinel contents changed")


def _python_smoke(
    version: str,
    *,
    source: RegistrySmokeSource,
    home_root: Path,
    pipx_home: Path,
    pipx_bin: Path,
    work_cwd: Path,
    run_command: CommandRunner,
) -> None:
    environment = _smoke_environment(home_root, pipx_home, pipx_bin)
    install_argv = build_pipx_install_argv(version, source)
    _run_checked(
        argv=install_argv,
        env=environment,
        cwd=work_cwd,
        run_command=run_command,
        context="pipx install",
    )
    version_result = run_command(["socratic", "--version"], environment, work_cwd)
    if version_result.returncode != 0:
        raise RegistrySmokeError(
            "pipx-installed socratic --version failed: "
            f"{version_result.stdout}\n{version_result.stderr}"
        )
    assert_exact_version_output(
        f"{version_result.stdout}\n{version_result.stderr}",
        version,
    )
    host_environment = dict(environment)
    host_environment["PATH"] = _minimal_path(pipx_bin)
    host_result = run_command(
        ["socratic", "registry-smoke-goal"],
        host_environment,
        work_cwd,
    )
    assert_missing_host_guidance(host_result)
    _sentinel_survives_uninstall(home_root)
    if source.uses_registry():
        _run_checked(
            argv=build_pipx_upgrade_argv(),
            env=environment,
            cwd=work_cwd,
            run_command=run_command,
            context="pipx upgrade",
        )
    else:
        _run_checked(
            argv=build_pipx_install_argv(version, source),
            env=environment,
            cwd=work_cwd,
            run_command=run_command,
            context="pipx reinstall",
        )
    _run_checked(
        argv=build_pipx_uninstall_argv(),
        env=environment,
        cwd=work_cwd,
        run_command=run_command,
        context="pipx uninstall",
    )
    _assert_sentinel_preserved(home_root)


def _npm_smoke(
    version: str,
    *,
    source: RegistrySmokeSource,
    home_root: Path,
    pipx_home: Path,
    pipx_bin: Path,
    work_cwd: Path,
    run_command: CommandRunner,
) -> None:
    environment = _smoke_environment(home_root, pipx_home, pipx_bin)
    argv = npm_exec_argv(version, ["socratic", "--version"], source)
    completed = run_command(argv, environment, work_cwd)
    if completed.returncode != 0:
        raise RegistrySmokeError(
            "npm exec socratic --version failed: "
            f"{completed.stdout}\n{completed.stderr}"
        )
    assert_exact_version_output(
        f"{completed.stdout}\n{completed.stderr}",
        version,
    )


def run_registry_smoke(
    version: str,
    *,
    run_command: CommandRunner = production_run_command,
    home_root: Path | None = None,
    work_cwd: Path | None = None,
    checkout: Path | None = None,
    probe_pypi: RegistryProbe = _probe_pypi_registry,
    probe_npm: RegistryProbe = _probe_npm_registry,
    sleep: SleepFn = time.sleep,
    max_attempts: int = DEFAULT_REGISTRY_RETRY_ATTEMPTS,
    delay_seconds: float = DEFAULT_REGISTRY_RETRY_DELAY_SECONDS,
    source: RegistrySmokeSource | None = None,
) -> None:
    source = source or RegistrySmokeSource.registry()
    repository = checkout or checkout_root()
    root = home_root or Path(tempfile.mkdtemp(prefix="registry-smoke-home-"))
    pipx_home = root / "pipx"
    pipx_bin = root / "pipx-bin"
    pipx_home.mkdir(parents=True, exist_ok=True)
    pipx_bin.mkdir(parents=True, exist_ok=True)
    cwd = work_cwd or _isolated_work_cwd(checkout=repository, home_root=root)

    if source.uses_registry():
        wait_for_registry(
            version,
            probe_pypi=probe_pypi,
            probe_npm=probe_npm,
            sleep=sleep,
            max_attempts=max_attempts,
            delay_seconds=delay_seconds,
        )

    _python_smoke(
        version,
        source=source,
        home_root=root,
        pipx_home=pipx_home,
        pipx_bin=pipx_bin,
        work_cwd=cwd,
        run_command=run_command,
    )
    _npm_smoke(
        version,
        source=source,
        home_root=root,
        pipx_home=pipx_home,
        pipx_bin=pipx_bin,
        work_cwd=cwd,
        run_command=run_command,
    )


def run_local_artifact_smoke(
    version: str,
    *,
    wheel: Path,
    npm_tarball: Path,
    run_command: CommandRunner = production_run_command,
    home_root: Path | None = None,
    work_cwd: Path | None = None,
    checkout: Path | None = None,
) -> None:
    resolved_wheel = wheel.resolve()
    resolved_tarball = npm_tarball.resolve()
    if not resolved_wheel.is_file():
        raise RegistrySmokeError(f"local wheel not found: {resolved_wheel}")
    if not resolved_tarball.is_file():
        raise RegistrySmokeError(f"local npm tarball not found: {resolved_tarball}")
    run_registry_smoke(
        version,
        run_command=run_command,
        home_root=home_root,
        work_cwd=work_cwd,
        checkout=checkout,
        probe_pypi=_forbidden_registry_probe("PyPI"),
        probe_npm=_forbidden_registry_probe("npm"),
        source=RegistrySmokeSource.local(
            wheel=resolved_wheel,
            npm_tarball=resolved_tarball,
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 1 and arguments[0] != "--help":
        version = arguments[0]
        run_registry_smoke(version)
        return 0
    if len(arguments) == 4 and arguments[0] == "--local":
        version = arguments[1]
        wheel = Path(arguments[2])
        tarball = Path(arguments[3])
        run_local_artifact_smoke(version, wheel=wheel, npm_tarball=tarball)
        return 0
    print(
        "usage: python -m tests.registry_smoke VERSION\n"
        "       python -m tests.registry_smoke --local VERSION WHEEL TARBALL",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())