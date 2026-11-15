from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.registry_smoke import (
    DEFAULT_REGISTRY_RETRY_ATTEMPTS,
    DEFAULT_REGISTRY_RETRY_DELAY_SECONDS,
    RegistrySmokeError,
    RegistrySmokeSource,
    assert_exact_version_output,
    assert_missing_host_guidance,
    build_pipx_install_argv,
    build_pipx_upgrade_argv,
    checkout_root,
    learner_sentinel_path,
    npm_exec_argv,
    prepare_subprocess_argv,
    production_run_command,
    run_local_artifact_smoke,
    run_registry_smoke,
    wait_for_registry,
)


class RegistrySmokeCommandTests(unittest.TestCase):
    def test_build_pipx_install_argv_pins_exact_registry_version(self) -> None:
        argv = build_pipx_install_argv(
            "1.2.3",
            RegistrySmokeSource.registry(),
        )
        self.assertEqual(argv[-1], "socratic-learn==1.2.3")
        self.assertEqual(argv[-2], "install")
        self.assertEqual(argv[-3], "pipx")
        self.assertEqual(argv[-4], "-m")

    def test_build_pipx_install_argv_uses_local_wheel(self) -> None:
        wheel = Path("/tmp/socratic_learn-1.2.3-py3-none-any.whl")
        argv = build_pipx_install_argv(
            "1.2.3",
            RegistrySmokeSource.local(wheel=wheel, npm_tarball=Path("/tmp/pkg.tgz")),
        )
        self.assertEqual(argv[-2:], ["--force", str(wheel.resolve())])
        self.assertIn("install", argv)

    def test_build_pipx_upgrade_argv_targets_distribution_name(self) -> None:
        argv = build_pipx_upgrade_argv()
        self.assertEqual(argv[-2:], ["upgrade", "socratic-learn"])

    def test_npm_exec_argv_pins_exact_registry_version(self) -> None:
        argv = npm_exec_argv(
            "2.0.0",
            ["socratic", "--version"],
            RegistrySmokeSource.registry(),
        )
        self.assertIn("@socraticlearn/cli@2.0.0", argv)
        self.assertEqual(argv[-2:], ["socratic", "--version"])
        self.assertIn("--", argv)
        package_index = argv.index("--package")
        self.assertEqual(argv[package_index + 1], "@socraticlearn/cli@2.0.0")
        self.assertEqual(argv[package_index + 2], "--")

    def test_npm_exec_argv_uses_local_tarball(self) -> None:
        tarball = Path("/artifacts/socraticlearn-cli-2.0.0.tgz")
        argv = npm_exec_argv(
            "2.0.0",
            ["socratic", "--version"],
            RegistrySmokeSource.local(
                wheel=Path("/artifacts/wheel.whl"),
                npm_tarball=tarball,
            ),
        )
        package_index = argv.index("--package")
        self.assertEqual(argv[package_index + 1], str(tarball.resolve()))
        self.assertIn("--", argv)
        self.assertEqual(argv[-2:], ["socratic", "--version"])

    def test_assert_exact_version_output_accepts_canonical_line(self) -> None:
        assert_exact_version_output("socratic-learn 4.5.6\n", "4.5.6")

    def test_assert_exact_version_output_rejects_mismatch(self) -> None:
        with self.assertRaisesRegex(RegistrySmokeError, "expected version '4.5.6'"):
            assert_exact_version_output("socratic-learn 4.5.5\n", "4.5.6")

    def test_assert_missing_host_guidance_requires_remediation(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["socratic", "goal"],
            returncode=2,
            stdout="",
            stderr=(
                "error: no compatible agent was found\n"
                "install Pi, Claude Code, or Antigravity and retry\n"
            ),
        )
        assert_missing_host_guidance(completed)

    def test_assert_missing_host_guidance_rejects_traceback(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["socratic", "goal"],
            returncode=1,
            stdout="",
            stderr="Traceback (most recent call last):\n  File ...\n",
        )
        with self.assertRaisesRegex(RegistrySmokeError, "traceback"):
            assert_missing_host_guidance(completed)

    def test_learner_sentinel_path_lives_under_home(self) -> None:
        home = Path("/tmp/smoke-home")
        self.assertEqual(
            learner_sentinel_path(home),
            home
            / ".socratic-learn"
            / "learning"
            / ".socratic-learn"
            / "workspace"
            / "registry-smoke-sentinel.txt",
        )

    def test_prepare_subprocess_argv_wraps_windows_batch_launchers(self) -> None:
        npx_cmd = r"C:\Program Files\nodejs\npx.cmd"
        argv = [npx_cmd, "--yes", "--package", "@socraticlearn/cli@1.0.0", "--", "socratic", "--version"]
        with mock.patch("tests.registry_smoke.os.name", "nt"), mock.patch(
            "tests.registry_smoke._windows_comspec",
            return_value=r"C:\Windows\System32\cmd.exe",
        ):
            prepared = prepare_subprocess_argv(argv)
        self.assertEqual(prepared[0].lower(), r"c:\windows\system32\cmd.exe")
        self.assertEqual(prepared[1:4], ["/d", "/c", "call"])
        self.assertEqual(prepared[4:], argv)

    def test_prepare_subprocess_argv_keeps_native_windows_executables(self) -> None:
        argv = [r"C:\Program Files\nodejs\npx.exe", "--yes"]
        with mock.patch("tests.registry_smoke.os.name", "nt"):
            self.assertEqual(prepare_subprocess_argv(argv), argv)

    def test_production_run_command_wraps_windows_npm_smoke_argv(self) -> None:
        npx_cmd = r"C:\Tools\npx.cmd"
        cwd = Path("/tmp/isolated-smoke-cwd")
        with mock.patch("tests.registry_smoke.os.name", "nt"), mock.patch(
            "tests.registry_smoke._windows_comspec",
            return_value=r"C:\Windows\System32\cmd.exe",
        ), mock.patch(
            "tests.registry_smoke.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run:
            production_run_command([npx_cmd, "--yes", "socratic", "--version"], {}, cwd)
        argv = run.call_args.args[0]
        self.assertTrue(Path(argv[0]).suffix.lower() == ".exe")
        self.assertEqual(argv[1:4], ["/d", "/c", "call"])


class RegistrySmokeRetryTests(unittest.TestCase):
    def test_wait_for_registry_succeeds_when_both_resolve(self) -> None:
        calls = {"pypi": 0, "npm": 0}

        def probe_pypi(version: str) -> bool:
            calls["pypi"] += 1
            return version == "0.3.0"

        def probe_npm(version: str) -> bool:
            calls["npm"] += 1
            return version == "0.3.0"

        wait_for_registry(
            "0.3.0",
            probe_pypi=probe_pypi,
            probe_npm=probe_npm,
            sleep=lambda _: None,
            max_attempts=3,
            delay_seconds=1.0,
        )
        self.assertEqual(calls, {"pypi": 1, "npm": 1})

    def test_wait_for_registry_retries_until_both_resolve(self) -> None:
        attempts = {"pypi": 0, "npm": 0}

        def probe_pypi(version: str) -> bool:
            attempts["pypi"] += 1
            return attempts["pypi"] >= 2

        def probe_npm(version: str) -> bool:
            attempts["npm"] += 1
            return attempts["npm"] >= 2

        sleep_calls: list[float] = []

        wait_for_registry(
            "0.3.0",
            probe_pypi=probe_pypi,
            probe_npm=probe_npm,
            sleep=sleep_calls.append,
            max_attempts=5,
            delay_seconds=2.5,
        )
        self.assertEqual(attempts, {"pypi": 2, "npm": 2})
        self.assertEqual(sleep_calls, [2.5])

    def test_wait_for_registry_raises_terminal_error_after_bound(self) -> None:
        with self.assertRaisesRegex(
            RegistrySmokeError,
            "registry entries did not resolve",
        ):
            wait_for_registry(
                "9.9.9",
                probe_pypi=lambda _: False,
                probe_npm=lambda _: False,
                sleep=lambda _: None,
                max_attempts=2,
                delay_seconds=0.0,
            )


class RegistrySmokeProductionRunnerTests(unittest.TestCase):
    def test_production_run_command_sets_safe_path_and_cwd(self) -> None:
        cwd = Path("/tmp/isolated-smoke-cwd")
        with mock.patch(
            "tests.registry_smoke.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run:
            production_run_command(["socratic", "--version"], {}, cwd)
        _, kwargs = run.call_args
        self.assertEqual(kwargs["cwd"], cwd)
        self.assertEqual(kwargs["env"]["PYTHONSAFEPATH"], "1")
        self.assertNotIn("PYTHONPATH", kwargs["env"])

    def test_run_registry_smoke_uses_isolated_cwd_for_every_subprocess(self) -> None:
        version = "0.4.1"
        isolated = Path("/tmp/outside-checkout-smoke")
        checkout = checkout_root()
        recorded: list[Path] = []

        def run(
            argv: list[str],
            env: dict[str, str],
            cwd: Path,
        ) -> subprocess.CompletedProcess[str]:
            recorded.append(cwd.resolve())
            if len(argv) >= 4 and argv[2] == "pipx":
                return subprocess.CompletedProcess([], 0, "", "")
            if argv[:2] == ["socratic", "--version"]:
                return subprocess.CompletedProcess(
                    [],
                    0,
                    f"socratic-learn {version}\n",
                    "",
                )
            if argv[:2] == ["socratic", "registry-smoke-goal"]:
                return subprocess.CompletedProcess(
                    [],
                    2,
                    "",
                    "error: no compatible agent was found\n"
                    "install Pi, Claude Code, or Antigravity and retry\n",
                )
            if Path(argv[0]).name.lower().startswith(("npx", "npm")):
                return subprocess.CompletedProcess(
                    [],
                    0,
                    f"socratic-learn {version}\n",
                    "",
                )
            return subprocess.CompletedProcess([], 0, "", "")

        with mock.patch("tests.registry_smoke.wait_for_registry"):
            run_registry_smoke(
                version,
                run_command=run,
                home_root=Path("/tmp/registry-smoke-home"),
                work_cwd=isolated,
                checkout=checkout,
            )

        self.assertTrue(recorded)
        self.assertTrue(all(path == isolated.resolve() for path in recorded))
        self.assertNotIn(checkout.resolve(), recorded)
        for path in recorded:
            checkout_resolved = checkout.resolve()
            self.assertFalse(
                checkout_resolved in path.parents or path == checkout_resolved
            )


class RegistrySmokeRunnerTests(unittest.TestCase):
    def _runner(
        self,
        *,
        commands: dict[tuple[str, ...], subprocess.CompletedProcess[str]],
    ):
        def _key(argv: list[str]) -> tuple[str, ...]:
            if len(argv) >= 4 and argv[1] == "-m" and argv[2] == "pipx":
                return tuple(["pipx", *argv[3:]])
            if Path(argv[0]).name.lower().startswith(("npx", "npm")):
                return tuple(["npx", *argv[1:]])
            return tuple(argv)

        def run(
            argv: list[str],
            env: dict[str, str],
            cwd: Path,
        ) -> subprocess.CompletedProcess[str]:
            key = _key(argv)
            if key not in commands:
                raise AssertionError(f"unexpected command: {argv!r} ({key!r})")
            return commands[key]

        return run

    def test_run_registry_smoke_executes_registry_sequence(self) -> None:
        version = "0.4.1"
        commands: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {
            ("pipx", "install", f"socratic-learn=={version}"): subprocess.CompletedProcess(
                [],
                0,
                "",
                "",
            ),
            ("socratic", "--version"): subprocess.CompletedProcess(
                [],
                0,
                f"socratic-learn {version}\n",
                "",
            ),
            ("socratic", "registry-smoke-goal"): subprocess.CompletedProcess(
                [],
                2,
                "",
                (
                    "error: no compatible agent was found\n"
                    "install Pi, Claude Code, or Antigravity and retry\n"
                ),
            ),
            ("pipx", "upgrade", "socratic-learn"): subprocess.CompletedProcess(
                [],
                0,
                "",
                "",
            ),
            ("pipx", "uninstall", "socratic-learn"): subprocess.CompletedProcess(
                [],
                0,
                "",
                "",
            ),
            (
                "npx",
                "--yes",
                "--package",
                f"@socraticlearn/cli@{version}",
                "--",
                "socratic",
                "--version",
            ): subprocess.CompletedProcess(
                [],
                0,
                f"socratic-learn {version}\n",
                "",
            ),
        }

        sentinel_writes: list[Path] = []

        def before_uninstall(home: Path) -> None:
            path = learner_sentinel_path(home)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "registry-smoke-preservation-check\n",
                encoding="utf-8",
            )
            sentinel_writes.append(path)

        with mock.patch(
            "tests.registry_smoke.wait_for_registry",
        ) as wait, mock.patch(
            "tests.registry_smoke._sentinel_survives_uninstall",
            side_effect=before_uninstall,
        ):
            run_registry_smoke(
                version,
                run_command=self._runner(commands=commands),
                home_root=Path("/tmp/registry-smoke"),
                work_cwd=Path("/tmp/registry-smoke-work"),
            )
            wait.assert_called_once()
            self.assertTrue(sentinel_writes)

    def test_run_registry_smoke_preserves_sentinel_data(self) -> None:
        version = "0.4.1"
        sentinel = Path(
            "/tmp/registry-smoke/.socratic-learn/learning/.socratic-learn/workspace/sentinel.txt"
        )
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(
            "registry-smoke-preservation-check\n",
            encoding="utf-8",
        )

        def run(
            argv: list[str],
            env: dict[str, str],
            cwd: Path,
        ) -> subprocess.CompletedProcess[str]:
            if argv == ["socratic", "--version"]:
                return subprocess.CompletedProcess([], 0, f"socratic-learn {version}\n", "")
            if argv == ["socratic", "registry-smoke-goal"]:
                return subprocess.CompletedProcess(
                    [],
                    2,
                    "",
                    (
                        "error: no compatible agent was found\n"
                        "install Pi, Claude Code, or Antigravity and retry\n"
                    ),
                )
            if Path(argv[0]).name.lower().startswith(("npx", "npm")):
                return subprocess.CompletedProcess([], 0, f"socratic-learn {version}\n", "")
            return subprocess.CompletedProcess([], 0, "", "")

        with mock.patch("tests.registry_smoke.wait_for_registry"), mock.patch(
            "tests.registry_smoke.learner_sentinel_path",
            return_value=sentinel,
        ):
            run_registry_smoke(
                version,
                run_command=run,
                home_root=Path("/tmp/registry-smoke"),
                work_cwd=Path("/tmp/registry-smoke-work"),
            )
        self.assertEqual(
            sentinel.read_text(encoding="utf-8"),
            "registry-smoke-preservation-check\n",
        )

    def test_run_registry_smoke_default_retry_bounds(self) -> None:
        self.assertEqual(DEFAULT_REGISTRY_RETRY_ATTEMPTS, 12)
        self.assertEqual(DEFAULT_REGISTRY_RETRY_DELAY_SECONDS, 15.0)


class LocalArtifactSmokeTests(unittest.TestCase):
    def test_local_source_rejects_registry_probes(self) -> None:
        source = RegistrySmokeSource.local(
            wheel=Path("wheel.whl"),
            npm_tarball=Path("pkg.tgz"),
        )
        self.assertFalse(source.uses_registry())
        from tests.registry_smoke import _forbidden_registry_probe

        with self.assertRaisesRegex(RegistrySmokeError, "must not query the PyPI"):
            _forbidden_registry_probe("PyPI")("0.1.0")

    def test_run_local_artifact_smoke_uses_wheel_install_and_reinstall(self) -> None:
        version = "0.1.0"
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            wheel = artifact_root / "socratic_learn-0.1.0-py3-none-any.whl"
            tarball = artifact_root / "socraticlearn-cli-0.1.0.tgz"
            wheel.write_bytes(b"wheel")
            tarball.write_bytes(b"tarball")
            self._assert_local_wheel_smoke(version, wheel, tarball)

    def _assert_local_wheel_smoke(
        self,
        version: str,
        wheel: Path,
        tarball: Path,
    ) -> None:
        pipx_installs: list[list[str]] = []

        def run(
            argv: list[str],
            env: dict[str, str],
            cwd: Path,
        ) -> subprocess.CompletedProcess[str]:
            if len(argv) >= 4 and argv[2] == "pipx" and argv[3] == "install":
                pipx_installs.append(argv)
                return subprocess.CompletedProcess([], 0, "", "")
            if argv[:2] == ["socratic", "--version"]:
                return subprocess.CompletedProcess(
                    [],
                    0,
                    f"socratic-learn {version}\n",
                    "",
                )
            if argv[:2] == ["socratic", "registry-smoke-goal"]:
                return subprocess.CompletedProcess(
                    [],
                    2,
                    "",
                    "error: no compatible agent was found\n"
                    "install Pi, Claude Code, or Antigravity and retry\n",
                )
            if Path(argv[0]).name.lower().startswith(("npx", "npm")):
                return subprocess.CompletedProcess(
                    [],
                    0,
                    f"socratic-learn {version}\n",
                    "",
                )
            if len(argv) >= 4 and argv[2] == "pipx" and argv[3] == "uninstall":
                return subprocess.CompletedProcess([], 0, "", "")
            return subprocess.CompletedProcess([], 0, "", "")

        with mock.patch("tests.registry_smoke.wait_for_registry") as wait:
            run_local_artifact_smoke(
                version,
                wheel=wheel,
                npm_tarball=tarball,
                run_command=run,
                home_root=Path("/tmp/local-smoke-home"),
                work_cwd=Path("/tmp/local-smoke-work"),
                checkout=Path("/tmp/local-smoke-checkout"),
            )
            wait.assert_not_called()

        self.assertEqual(len(pipx_installs), 2)
        for install in pipx_installs:
            self.assertIn("--force", install)
            self.assertEqual(install[-1], str(wheel.resolve()))
        self.assertNotIn(
            "upgrade",
            [token for install in pipx_installs for token in install],
        )


class VerifyArtifactWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.release = (cls.root / ".github/workflows/release.yml").read_text(
            encoding="utf-8",
        )

    def _verify_artifact_job(self) -> str:
        from tests.validate_repository import workflow_job

        return workflow_job(self.release, "verify-artifact")

    def test_verify_artifact_matrix_runs_on_three_platforms(self) -> None:
        job = self._verify_artifact_job()
        self.assertIn("windows-latest", job)
        self.assertIn("ubuntu-latest", job)
        self.assertIn("macos-latest", job)
        self.assertIn("matrix:", job)
        self.assertIn("fail-fast: false", job)

    def test_verify_artifact_depends_on_npm_package_only(self) -> None:
        job = self._verify_artifact_job()
        self.assertIn("npm-package", job)
        needs_section = job.split("needs:", 1)[1].split("strategy:", 1)[0]
        self.assertNotIn("publish-github", needs_section)
        self.assertNotIn("publish-pypi", needs_section)

    def test_verify_artifact_uses_downloaded_artifacts_without_source_import(self) -> None:
        step_section = self._verify_artifact_job()
        self.assertIn("run_local_artifact_smoke", step_section)
        self.assertIn("name: python-package", step_section)
        self.assertIn("name: npm-package", step_section)
        self.assertNotIn("from socratic_learn import", step_section)
        self.assertNotIn("sys.path.insert", step_section)

    def test_release_graph_lists_verify_artifact_before_publish_github(self) -> None:
        self.assertIn("  verify-artifact:", self.release)
        self.assertLess(
            self.release.index("  npm-package:"),
            self.release.index("  verify-artifact:"),
        )
        self.assertLess(
            self.release.index("  verify-artifact:"),
            self.release.index("  publish-github:"),
        )
        github = self.release.split("  publish-github:", 1)[1].split(
            "  publish-pypi:", 1
        )[0]
        self.assertIn("verify-artifact", github)


class RegistrySmokeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.release = (cls.root / ".github/workflows/release.yml").read_text(
            encoding="utf-8",
        )

    def _verify_registry_job(self) -> str:
        from tests.validate_repository import workflow_job

        return workflow_job(self.release, "verify-registry")

    def test_verify_registry_matrix_runs_on_three_platforms(self) -> None:
        job = self._verify_registry_job()
        self.assertIn("windows-latest", job)
        self.assertIn("ubuntu-latest", job)
        self.assertIn("macos-latest", job)
        self.assertIn("matrix:", job)
        self.assertIn("fail-fast: false", job)

    def test_verify_registry_depends_on_both_publish_jobs(self) -> None:
        job = self._verify_registry_job()
        self.assertIn("publish-pypi", job)
        self.assertIn("publish-npm", job)
        needs_section = job.split("needs:", 1)[1].split("strategy:", 1)[0]
        self.assertNotIn("publish-github", needs_section)

    def test_verify_registry_uses_tag_version_without_socratic_import(self) -> None:
        step_section = self._verify_registry_job()
        self.assertIn('python-version: "3.11"', step_section)
        self.assertIn("pipx", step_section)
        self.assertIn('node-version: "24"', step_section)
        self.assertIn("registry_smoke", step_section)
        self.assertIn("GITHUB_REF_NAME", step_section)
        self.assertNotIn("from socratic_learn import", step_section)
        self.assertNotIn("sys.path.insert", step_section)

    def test_release_graph_lists_verify_registry_after_publish(self) -> None:
        self.assertIn("  verify-registry:", self.release)
        self.assertLess(
            self.release.index("  publish-pypi:"),
            self.release.index("  verify-registry:"),
        )
        self.assertLess(
            self.release.index("  publish-npm:"),
            self.release.index("  verify-registry:"),
        )


if __name__ == "__main__":
    unittest.main()
