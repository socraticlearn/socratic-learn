from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path


class NpmPackageBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.npm_cli = self.root / "packages" / "npm-cli"
        self.builder = self.root / "tools" / "build_npm_package.py"

    def test_builder_module_exists(self) -> None:
        self.assertTrue(self.builder.is_file(), "tools/build_npm_package.py is required")

    def test_lock_matches_pyproject_runtime_constraints(self) -> None:
        lock = (self.npm_cli / "vendor-requirements.lock").read_text("utf-8")
        project = tomllib.loads((self.root / "pyproject.toml").read_text("utf-8"))
        self.assertEqual(
            ["packaging>=26.1,<27", "pypdf>=7.0,<8"],
            project["project"]["dependencies"],
        )
        match = re.search(r"^pypdf==(7\.\d+\.\d+)", lock, re.MULTILINE)
        self.assertIsNotNone(match)
        version = tuple(int(part) for part in match.group(1).split("."))
        self.assertGreaterEqual(version, (7, 0, 0))
        self.assertLess(version[0], 8)
        self.assertIn("--hash=sha256:", lock)

    def test_lock_rejects_platform_specific_wheels(self) -> None:
        lock = (self.npm_cli / "vendor-requirements.lock").read_text("utf-8")
        self.assertNotRegex(lock, r"win_amd64|manylinux|macosx|aarch64")

    def test_version_mismatch_is_rejected(self) -> None:
        from tools.build_npm_package import build_npm_package

        with tempfile.TemporaryDirectory() as workspace:
            work = Path(workspace)
            wheel = work / "socratic_learn-9.9.9-py3-none-any.whl"
            _write_empty_wheel(wheel, version="9.9.9")
            out = work / "out"
            npm_dir = work / "npm"
            _write_minimal_npm_package(npm_dir, version="0.1.0")
            with self.assertRaises(ValueError):
                build_npm_package(
                    wheel,
                    output_dir=out,
                    npm_cli_dir=npm_dir,
                    run_commands=False,
                )

    def test_clean_vendor_replacement_and_inventory(self) -> None:
        from tools.build_npm_package import build_npm_package, vendor_inventory

        wheel = self._build_release_wheel()
        with tempfile.TemporaryDirectory() as workspace:
            work = Path(workspace)
            out = work / "dist"
            npm_dir = work / "npm-cli"
            shutil.copytree(
                self.npm_cli,
                npm_dir,
                ignore=shutil.ignore_patterns("vendor"),
            )
            stale = npm_dir / "vendor" / "stale.txt"
            stale.parent.mkdir(parents=True)
            stale.write_text("old", encoding="utf-8")

            build_npm_package(
                wheel,
                output_dir=out,
                npm_cli_dir=npm_dir,
                run_commands=True,
            )

            self.assertFalse(stale.exists())
            inventory = vendor_inventory(npm_dir / "vendor")
            self.assertIn("socratic_learn/__init__.py", inventory)
            self.assertIn("pypdf/__init__.py", inventory)
            for relative in inventory:
                self.assertFalse(relative.endswith(".pyc"))
                self.assertNotIn("__pycache__", relative)

    def test_rejects_unlocked_vendor_dependency_line(self) -> None:
        from tools.build_npm_package import parse_vendor_lock

        with self.assertRaises(ValueError):
            parse_vendor_lock("requests==2.32.0\n")

    def test_rejects_platform_specific_lock_entry(self) -> None:
        from tools.build_npm_package import parse_vendor_lock

        with self.assertRaises(ValueError):
            parse_vendor_lock(
                "pypdf==7.1.0 "
                "--hash=sha256:47cca6b24bdcf4c4d65b5f7b8b972535c61710a061261e737688777cfd9a7bfb "
                "# via cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
            )

    def test_rejects_multiple_hashes_for_the_same_requirement(self) -> None:
        from tools.build_npm_package import parse_vendor_lock

        with self.assertRaises(ValueError):
            parse_vendor_lock(
                "pypdf==7.1.0 "
                "--hash=sha256:47cca6b24bdcf4c4d65b5f7b8b972535c61710a061261e737688777cfd9a7bfb "
                "--hash=sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
            )

    def test_npm_pack_produces_single_tarball_with_required_files(self) -> None:
        from tools.build_npm_package import build_npm_package, validate_packed_tarball

        wheel = self._build_release_wheel()
        with tempfile.TemporaryDirectory() as workspace:
            work = Path(workspace)
            out = work / "tarballs"
            npm_dir = work / "npm-cli"
            shutil.copytree(
                self.npm_cli,
                npm_dir,
                ignore=shutil.ignore_patterns("vendor"),
            )

            build_npm_package(
                wheel,
                output_dir=out,
                npm_cli_dir=npm_dir,
                run_commands=True,
            )

            tarballs = sorted(out.glob("*.tgz"))
            self.assertEqual(len(tarballs), 1)
            validate_packed_tarball(tarballs[0], npm_dir)
            names = _tarball_member_names(tarballs[0])
            self.assertIn("package/bin/socratic.js", names)
            self.assertIn("package/lib/python-runtime.js", names)
            self.assertIn("package/vendor/socratic_learn/__init__.py", names)
            self.assertIn("package/vendor/pypdf/__init__.py", names)
            self.assertFalse(any("/vendor/bin/" in name for name in names))
            self.assertFalse(any("/vendor/Scripts/" in name for name in names))
            self.assertFalse(any(name.endswith(".exe") for name in names))
            self.assertFalse(any(name.endswith(".pyc") for name in names))
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any("/test/" in name for name in names))
            self.assertFalse(any(name.endswith(".test.js") for name in names))

    def _build_release_wheel(self) -> Path:
        temp_dist = Path(tempfile.mkdtemp(prefix="npm-wheel-"))
        self.addCleanup(shutil.rmtree, temp_dist, ignore_errors=True)
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(temp_dist)],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = sorted(temp_dist.glob("socratic_learn-*.whl"))
        self.assertTrue(wheels)
        return wheels[-1]


def _write_empty_wheel(path: Path, *, version: str) -> None:
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: socratic-learn\n"
        f"Version: {version}\n"
        "Summary: test\n"
        "Author: test\n"
        "License: MIT\n"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"socratic_learn-{version}.dist-info/METADATA",
            metadata,
        )
        archive.writestr(
            f"socratic_learn-{version}.dist-info/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        archive.writestr("socratic_learn/__init__.py", f'__version__ = "{version}"\n')


def _write_minimal_npm_package(path: Path, *, version: str) -> None:
    path.mkdir(parents=True)
    (path / "package.json").write_text(
        json.dumps({"name": "@socraticlearn/cli", "version": version}),
        encoding="utf-8",
    )
    (path / "vendor-requirements.lock").write_text(
        "pypdf==7.1.0 \\\n"
        "    --hash=sha256:47cca6b24bdcf4c4d65b5f7b8b972535c61710a061261e737688777cfd9a7bfb\n",
        encoding="utf-8",
    )


def _tarball_member_names(path: Path) -> set[str]:
    with tarfile.open(path, "r:gz") as archive:
        return {member.name.replace("\\", "/") for member in archive.getmembers()}


if __name__ == "__main__":
    unittest.main()