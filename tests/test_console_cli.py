from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from socratic_learn.cli import main
from socratic_learn.errors import IntegrityError
from socratic_learn.launcher import LaunchOptions


class ConsoleCliTests(unittest.TestCase):
    def setUp(self) -> None:
        banner_patcher = mock.patch("socratic_learn.cli.write_banner")
        self.write_banner = banner_patcher.start()
        self.addCleanup(banner_patcher.stop)
        update_patcher = mock.patch(
            "socratic_learn.cli.check_for_update",
            return_value=None,
        )
        self.check_for_update = update_patcher.start()
        self.addCleanup(update_patcher.stop)

    def assert_banner_written_once(self) -> None:
        self.write_banner.assert_called_once_with(sys.stdout, os.environ)

    @mock.patch("socratic_learn.cli.run_learning", return_value=0)
    def test_no_arguments_launches_with_prompt(self, run: mock.Mock) -> None:
        events: list[str] = []
        self.write_banner.side_effect = lambda *_: events.append("banner")
        run.side_effect = lambda *_: events.append("run") or 0

        self.assertEqual(0, main([]))

        self.assert_banner_written_once()
        run.assert_called_once_with(
            LaunchOptions(None, None, False, Path.cwd())
        )
        self.assertEqual(["banner", "run"], events)

    @mock.patch("socratic_learn.cli.run_learning", return_value=0)
    def test_learning_launch_prints_update_notice(
        self,
        run: mock.Mock,
    ) -> None:
        self.check_for_update.return_value = (
            "Update available: Socratic Learn 0.2.0 (installed: 0.1.0)\n"
            "Run: pipx upgrade socratic-learn"
        )
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            self.assertEqual(0, main([]))

        self.assertIn("Update available: Socratic Learn 0.2.0", stderr.getvalue())
        self.check_for_update.assert_called_once()
        run.assert_called_once()

    @mock.patch("socratic_learn.cli.run_learning", return_value=0)
    def test_direct_goal_and_options_launch(self, run: mock.Mock) -> None:
        events: list[str] = []
        self.write_banner.side_effect = lambda *_: events.append("banner")
        run.side_effect = lambda *_: events.append("run") or 0

        self.assertEqual(
            0,
            main(["--agent", "pi", "--here", "linear", "algebra"]),
        )

        self.assert_banner_written_once()
        run.assert_called_once_with(
            LaunchOptions("linear algebra", "pi", True, Path.cwd())
        )
        self.assertEqual(["banner", "run"], events)

    @mock.patch("socratic_learn.cli.run_learning", return_value=0)
    def test_explicit_learn_allows_reserved_goal(self, run: mock.Mock) -> None:
        self.assertEqual(0, main(["learn", "doctor"]))
        self.assert_banner_written_once()
        run.assert_called_once_with(
            LaunchOptions("doctor", None, False, Path.cwd())
        )

    @mock.patch("socratic_learn.cli.administrative_main", return_value=7)
    def test_project_adapter_install_is_forwarded_unchanged(
        self,
        admin: mock.Mock,
    ) -> None:
        arguments = ["install", "TARGET", "PROJECT", "--update"]

        self.assertEqual(7, main(arguments))

        admin.assert_called_once_with(arguments)
        self.write_banner.assert_not_called()

    @mock.patch("socratic_learn.cli.run_learning", return_value=0)
    @mock.patch("socratic_learn.cli.administrative_main")
    def test_admin_word_after_flag_is_a_goal(
        self,
        admin: mock.Mock,
        run: mock.Mock,
    ) -> None:
        self.assertEqual(0, main(["--here", "doctor", "project"]))
        self.assert_banner_written_once()
        admin.assert_not_called()
        run.assert_called_once_with(
            LaunchOptions("doctor project", None, True, Path.cwd())
        )

    @mock.patch("socratic_learn.cli.run_learning", return_value=0)
    def test_launcher_options_can_follow_learn(
        self,
        run: mock.Mock,
    ) -> None:
        self.assertEqual(
            0,
            main(["learn", "--agent", "claude", "matrix", "proofs"]),
        )
        self.assert_banner_written_once()
        run.assert_called_once_with(
            LaunchOptions("matrix proofs", "claude", False, Path.cwd())
        )

    @mock.patch("socratic_learn.cli.run_learning", return_value=0)
    def test_bare_update_is_a_learning_goal(
        self,
        run: mock.Mock,
    ) -> None:
        self.assertEqual(0, main(["update"]))
        self.assert_banner_written_once()
        run.assert_called_once_with(
            LaunchOptions("update", None, False, Path.cwd())
        )

    @mock.patch("socratic_learn.cli.run_learning", return_value=0)
    def test_bare_uninstall_is_a_learning_goal(
        self,
        run: mock.Mock,
    ) -> None:
        self.assertEqual(0, main(["uninstall"]))
        self.assert_banner_written_once()
        run.assert_called_once_with(
            LaunchOptions("uninstall", None, False, Path.cwd())
        )

    @mock.patch("socratic_learn.cli.run_learning", return_value=0)
    def test_explicit_learn_allows_retired_self_management_words_as_goals(
        self,
        run: mock.Mock,
    ) -> None:
        for goal in ("update", "uninstall"):
            with self.subTest(goal=goal):
                self.assertEqual(0, main(["learn", goal]))
                run.assert_called_once_with(
                    LaunchOptions(goal, None, False, Path.cwd())
                )
                run.reset_mock()
                self.write_banner.reset_mock()

    @mock.patch("socratic_learn.cli.run_learning")
    def test_launcher_rejects_abbreviated_agent_option(
        self,
        run: mock.Mock,
    ) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = main(["--ag", "pi", "algebra"])

        self.assertEqual(2, exit_code)
        self.assertIn("--ag", stderr.getvalue())
        run.assert_not_called()
        self.write_banner.assert_not_called()

    def test_help_names_launcher_options_admin_commands_and_examples(
        self,
    ) -> None:
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                main(["--help"])

        self.assertEqual(0, raised.exception.code)
        help_text = stdout.getvalue()
        for expected in (
            "--agent",
            "--here",
            "build",
            "export",
            "install",
            "doctor",
            "Examples:",
        ):
            self.assertIn(expected, help_text)
        self.assertNotIn("Self-management commands:", help_text)
        self.assertNotIn("--delete-data", help_text)
        self.write_banner.assert_not_called()

    def test_version_prints_distribution_version(self) -> None:
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                main(["--version"])

        self.assertEqual(0, raised.exception.code)
        self.assertEqual("socratic-learn 0.1.0\n", stdout.getvalue())
        self.write_banner.assert_not_called()

    @mock.patch(
        "socratic_learn.cli.run_learning",
        side_effect=IntegrityError(
            "launcher data unavailable",
            remediation="reinstall the package",
        ),
    )
    def test_known_errors_are_actionable_without_traceback(
        self,
        run: mock.Mock,
    ) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = main(["algebra"])

        message = stderr.getvalue()
        self.assertEqual(4, exit_code)
        self.assertIn("launcher data unavailable", message)
        self.assertIn("reinstall the package", message)
        self.assertNotIn("Traceback", message)
        self.assert_banner_written_once()

    def test_chooser_eof_exits_cleanly_without_traceback(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch(
                "socratic_learn.cli.run_learning",
                side_effect=EOFError("terminal menu input ended"),
            ),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main([])
        self.assertEqual(130, exit_code)
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_chooser_ctrl_c_exits_cleanly_without_traceback(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch(
                "socratic_learn.cli.run_learning",
                side_effect=KeyboardInterrupt,
            ),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main([])
        self.assertEqual(130, exit_code)
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
