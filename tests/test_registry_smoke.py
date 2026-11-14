from __future__ import annotations

import os
import subprocess
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
    learner_sentinel_path,
    npm_exec_argv,
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
        self.assertEqual(argv[-2:], ["--force", str(wheel)])
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
        self.assertTrue(
            Path(argv[0]).name.lower().startswith(("npx", "npm")),
            argv[0],
        )

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
        self.assertEqual(argv[-2:], ["socratic", "--version"])
        self.assertIn("--package", argv)
        self.assertTrue(any(tarball.name in entry for entry in argv), argv)

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
            home / ".socratic-learn" / "workspace" / "registry-smoke-sentinel.txt",
        )


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


class RegistrySmokeRunnerTests(unittest.TestCase):
    def _runner(
        self,
        *,
        commands: dict[tuple[str, ...], subprocess.CompletedProcess[str]],
    ):
        def _key(argv: list[str]) -> tuple[str, ...]:
            if len(argv) >= 4 and argv[1] == "-m" and argv[2] == "pipx":
                return tuple(["pipx", *argv[3:]])
            if Path(argv[0]).name.lower().startswith("npx"):
                return tuple(["npx", *argv[1:]])
            return tuple(argv)

        def run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
            key = _key(argv)
            if key not in commands:
                raise AssertionError(f"unexpected command: {argv!r} ({key!r})")
            return commands[key]

        return run

    def test_run_registry_smoke_executes_registry_sequence(self) -> None:
        version = "0.4.1"
        commands: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {
            ("pipx", "install", f"socratic-learn=={version}"): subprocess.CompletedProcess(  # noqa: duplicate key style
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
                f"@socraticlearn/cli@{version}",
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
            )
            wait.assert_called_once()
            self.assertTrue(sentinel_writes)

    def test_run_registry_smoke_preserves_sentinel_data(self) -> None:
        version = "0.4.1"
        sentinel = Path("/tmp/registry-smoke/.socratic-learn/workspace/sentinel.txt")
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(
            "registry-smoke-preservation-check\n",
            encoding="utf-8",
        )

        def run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
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
            if Path(argv[0]).name.lower().startswith("npx"):
                return subprocess.CompletedProcess([], 0, f"socratic-learn {version}\n", "")
            return subprocess.CompletedProcess([], 0, "", "")

        with mock.patch("tests.registry_smoke.wait_for_registry"), mock.patch(
            "tests.registry_smoke.learner_sentinel_path",
            return_value=sentinel,
        ):
            run_registry_smoke(
                version,
                run_command=run,
                home_root=sentinel.parents[2],
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