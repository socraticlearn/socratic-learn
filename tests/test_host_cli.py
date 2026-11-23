from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from socratic_learn.config import LauncherConfig, load_config
from socratic_learn.cli import main
from socratic_learn.errors import IntegrityError, UsageError
from socratic_learn.host_cli import parse_host_arguments, run_host_cli
from socratic_learn.hosts import ADAPTERS


class ParseHostArgumentsTests(unittest.TestCase):
    def test_list_after_host_token(self) -> None:
        self.assertEqual("list", parse_host_arguments(["host", "list"]))

    def test_clear_after_host_token(self) -> None:
        self.assertEqual("clear", parse_host_arguments(["host", "clear"]))

    def test_set_parses_supported_host_id_without_detection(self) -> None:
        self.assertEqual(("set", "pi"), parse_host_arguments(["host", "set", "pi"]))

    def test_launcher_only_options_are_rejected_in_host_mode(self) -> None:
        for arguments, option in (
            (["--agent", "pi", "host", "list"], "--agent"),
            (["host", "list", "--agent", "pi"], "--agent"),
            (["--agent=pi", "host", "list"], "--agent"),
            (["host", "set", "pi", "--agent=pi"], "--agent"),
            (["--here", "host", "list"], "--here"),
            (["host", "clear", "--here"], "--here"),
            (["--allow-private-network", "host", "list"], "--allow-private-network"),
            (["host", "list", "--allow-private-network"], "--allow-private-network"),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(UsageError) as raised:
                    parse_host_arguments(arguments)
                self.assertIn(option, raised.exception.message)
                self.assertIn("host", raised.exception.message)
                remediation = raised.exception.remediation or ""
                self.assertIn("socratic host list|set NAME|clear", remediation)
                self.assertIn("socratic learn host", remediation)

    def test_unknown_host_name_on_set_is_usage_error(self) -> None:
        with self.assertRaises(UsageError) as raised:
            parse_host_arguments(["host", "set", "unknown"])
        for name in ADAPTERS:
            self.assertIn(name, raised.exception.remediation or "")

    def test_unknown_subcommand_remediation_offers_learn_host_escape(self) -> None:
        with self.assertRaises(UsageError) as raised:
            parse_host_arguments(["host", "typo"])
        self.assertIn("unknown host subcommand", raised.exception.message)
        remediation = raised.exception.remediation or ""
        self.assertIn("socratic host list|set NAME|clear", remediation)
        self.assertIn("socratic learn host", remediation)

    def test_set_missing_name_and_extra_arguments_are_distinct(self) -> None:
        with self.assertRaises(UsageError) as missing:
            parse_host_arguments(["host", "set"])
        with self.assertRaises(UsageError) as extra:
            parse_host_arguments(["host", "set", "pi", "extra"])
        self.assertIn("requires a host name", missing.exception.message)
        self.assertNotIn("too many", missing.exception.message)
        self.assertIn("too many arguments for 'host set'", extra.exception.message)
        self.assertNotIn("requires a host name", extra.exception.message)
        self.assertIn("socratic host set NAME", extra.exception.remediation or "")

    def test_missing_subcommand_is_usage_error(self) -> None:
        with self.assertRaises(UsageError):
            parse_host_arguments(["host"])


class HostCliBehaviorTests(unittest.TestCase):
    def _home_environ(self, home: Path) -> dict[str, str]:
        return {**os.environ, "SOCRATIC_LEARN_HOME": str(home)}

    def test_list_shows_every_adapter_in_canonical_order(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                0,
                run_host_cli(
                    ["host", "list"],
                    which=lambda name: None,
                    environ=self._home_environ(Path(temporary)),
                    stdout=stdout,
                ),
            )
        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(["pi: missing", "claude: missing", "antigravity: missing"], lines)

    def test_list_marks_installed_paths_and_saved_default(self) -> None:
        stdout = io.StringIO()
        mapping = {
            "pi": "C:/bin/pi.exe",
            "claude": "C:/bin/claude.exe",
            "agy": "C:/bin/agy.exe",
        }
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            save_path = home / "config.json"
            from socratic_learn.config import save_config

            save_config(save_path, LauncherConfig(default_agent="claude"))
            self.assertEqual(
                0,
                run_host_cli(
                    ["host", "list"],
                    which=mapping.get,
                    environ=self._home_environ(home),
                    stdout=stdout,
                ),
            )
        lines = stdout.getvalue().splitlines()
        self.assertIn("pi: installed (C:/bin/pi.exe)", lines)
        self.assertIn("claude: installed (C:/bin/claude.exe) [default]", lines)
        self.assertIn("antigravity: installed (C:/bin/agy.exe)", lines)

    def test_list_corrupt_config_raises_integrity_error(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "config.json").write_text("{broken", encoding="utf-8")
            with contextlib.redirect_stderr(stderr):
                exit_code = run_host_cli(
                    ["host", "list"],
                    which=lambda name: None,
                    environ=self._home_environ(home),
                )
        self.assertEqual(IntegrityError.exit_code, exit_code)
        self.assertIn("not valid JSON", stderr.getvalue())

    def test_list_marks_saved_missing_default(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "config.json").write_text(
                '{"schema_version": 1, "default_agent": "claude"}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                0,
                run_host_cli(
                    ["host", "list"],
                    which=lambda name: None,
                    environ=self._home_environ(home),
                    stdout=stdout,
                ),
            )
        self.assertIn("claude: missing [default]", stdout.getvalue().splitlines())

    def test_set_corrupt_config_raises_integrity_error_without_mutation(
        self,
    ) -> None:
        stderr = io.StringIO()
        corrupt = "{broken"
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / "config.json"
            path.write_text(corrupt, encoding="utf-8")
            with contextlib.redirect_stderr(stderr):
                exit_code = run_host_cli(
                    ["host", "set", "pi"],
                    which={"pi": "pi"}.get,
                    environ=self._home_environ(home),
                )
            self.assertEqual(IntegrityError.exit_code, exit_code)
            self.assertEqual(corrupt, path.read_text(encoding="utf-8"))
        self.assertIn("not valid JSON", stderr.getvalue())

    def test_clear_corrupt_config_raises_integrity_error_without_mutation(
        self,
    ) -> None:
        stderr = io.StringIO()
        corrupt = "{broken"
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / "config.json"
            path.write_text(corrupt, encoding="utf-8")
            with contextlib.redirect_stderr(stderr):
                exit_code = run_host_cli(
                    ["host", "clear"],
                    which=lambda name: None,
                    environ=self._home_environ(home),
                )
            self.assertEqual(IntegrityError.exit_code, exit_code)
            self.assertEqual(corrupt, path.read_text(encoding="utf-8"))
        self.assertIn("not valid JSON", stderr.getvalue())

    def test_set_persists_default_when_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.assertEqual(
                0,
                run_host_cli(
                    ["host", "set", "pi"],
                    which={"pi": "pi"}.get,
                    environ=self._home_environ(home),
                    stdout=io.StringIO(),
                ),
            )
            saved = json.loads((home / "config.json").read_text(encoding="utf-8"))
            self.assertEqual("pi", saved["default_agent"])

    def test_set_undetected_host_does_not_alter_config(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "config.json").write_text(
                '{"schema_version": 1, "default_agent": "pi"}\n',
                encoding="utf-8",
            )
            with contextlib.redirect_stderr(stderr):
                exit_code = run_host_cli(
                    ["host", "set", "claude"],
                    which={"pi": "pi"}.get,
                    environ=self._home_environ(home),
                )
            self.assertEqual(UsageError.exit_code, exit_code)
            self.assertIn("claude", stderr.getvalue())
            self.assertEqual(
                LauncherConfig(default_agent="pi"),
                load_config(home / "config.json"),
            )

    def test_clear_persists_null_default_without_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "config.json").write_text(
                '{"schema_version": 1, "default_agent": "pi"}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                0,
                run_host_cli(
                    ["host", "clear"],
                    which=lambda name: None,
                    environ=self._home_environ(home),
                    stdout=io.StringIO(),
                ),
            )
            saved = json.loads((home / "config.json").read_text(encoding="utf-8"))
            self.assertIsNone(saved["default_agent"])

    def test_set_prints_one_concise_confirmation(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                0,
                run_host_cli(
                    ["host", "set", "pi"],
                    which={"pi": "pi"}.get,
                    environ=self._home_environ(Path(temporary)),
                    stdout=stdout,
                    stderr=stderr,
                ),
            )
        self.assertEqual("Default host set to pi.\n", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_clear_prints_one_concise_confirmation_and_writes_null_config(
        self,
    ) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.assertEqual(
                0,
                run_host_cli(
                    ["host", "clear"],
                    which=lambda name: None,
                    environ=self._home_environ(home),
                    stdout=stdout,
                ),
            )
            saved = json.loads((home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual("Default host cleared.\n", stdout.getvalue())
        self.assertIsNone(saved["default_agent"])

    def test_failed_set_prints_no_confirmation(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            exit_code = run_host_cli(
                ["host", "set", "claude"],
                which=lambda name: None,
                environ=self._home_environ(Path(temporary)),
                stdout=stdout,
                stderr=stderr,
            )
        self.assertEqual(UsageError.exit_code, exit_code)
        self.assertEqual("", stdout.getvalue())

    def test_launcher_option_in_host_mode_exits_with_usage_error(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            exit_code = run_host_cli(
                ["host", "list", "--here"],
                which=lambda name: None,
                environ=self._home_environ(home),
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual([], list(home.iterdir()))
        self.assertEqual(UsageError.exit_code, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("--here", stderr.getvalue())


class HostMainIntegrationTests(unittest.TestCase):
    """Drive the real console entry point; nothing in the CLI is mocked."""

    def _fake_pi_directory(self, parent: Path) -> Path:
        bin_dir = parent / "bin"
        bin_dir.mkdir()
        if os.name == "nt":
            (bin_dir / "pi.cmd").write_text("@echo off\r\n", encoding="utf-8")
        else:
            executable = bin_dir / "pi"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
        return bin_dir

    def _run_main(
        self,
        arguments: list[str],
        *,
        home: Path,
        path: str,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {"SOCRATIC_LEARN_HOME": str(home), "PATH": path}
        with (
            mock.patch.dict(os.environ, environment),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_host_commands_create_no_learning_workspace_threads_or_update_cache(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "launcher-home"
            home.mkdir()
            path = str(self._fake_pi_directory(root))

            code, stdout, stderr = self._run_main(
                ["host", "list"], home=home, path=path
            )
            self.assertEqual(0, code, stderr)
            self.assertIn("pi: installed", stdout)
            self.assertEqual([], list(home.iterdir()))

            code, stdout, stderr = self._run_main(
                ["host", "set", "pi"], home=home, path=path
            )
            self.assertEqual(0, code, stderr)
            self.assertEqual("Default host set to pi.\n", stdout)
            self.assertEqual(["config.json"], sorted(p.name for p in home.iterdir()))

            code, stdout, stderr = self._run_main(
                ["host", "clear"], home=home, path=path
            )
            self.assertEqual(0, code, stderr)
            self.assertEqual("Default host cleared.\n", stdout)
            self.assertEqual(["config.json"], sorted(p.name for p in home.iterdir()))
            self.assertIsNone(load_config(home / "config.json").default_agent)
            self.assertFalse((home / "learning").exists())
            self.assertFalse((home / "update-check.json").exists())

    def test_launcher_option_in_host_mode_is_rejected_by_main(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            code, stdout, stderr = self._run_main(
                ["--agent", "pi", "host", "list"], home=home, path=""
            )
            self.assertEqual([], list(home.iterdir()))
        self.assertEqual(UsageError.exit_code, code)
        self.assertEqual("", stdout)
        self.assertIn("--agent", stderr)


if __name__ == "__main__":
    unittest.main()