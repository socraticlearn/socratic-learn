from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from socratic_learn.config import LauncherConfig, load_config
from socratic_learn.errors import IntegrityError, UsageError
from socratic_learn.host_cli import parse_host_arguments, run_host_cli


class ParseHostArgumentsTests(unittest.TestCase):
    def test_list_after_host_token(self) -> None:
        self.assertEqual("list", parse_host_arguments(["host", "list"]))

    def test_clear_after_host_token(self) -> None:
        self.assertEqual("clear", parse_host_arguments(["host", "clear"]))

    def test_set_parses_supported_host_id_without_detection(self) -> None:
        self.assertEqual(("set", "pi"), parse_host_arguments(["host", "set", "pi"]))

    def test_options_before_host_are_ignored(self) -> None:
        self.assertEqual("list", parse_host_arguments(["--agent", "pi", "host", "list"]))

    def test_unknown_host_name_on_set_is_usage_error(self) -> None:
        with self.assertRaises(UsageError):
            parse_host_arguments(["host", "set", "unknown"])

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
                ),
            )
            saved = json.loads((home / "config.json").read_text(encoding="utf-8"))
            self.assertIsNone(saved["default_agent"])


if __name__ == "__main__":
    unittest.main()