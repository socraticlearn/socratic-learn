from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess

from socratic_learn.errors import FilesystemError, UsageError
from socratic_learn.launcher import LaunchOptions, run_learning
from socratic_learn.manifest import SESSION_HEADINGS
from socratic_learn.threads import (
    create_pending_encounter,
    create_thread,
    finalize_thread_encounter,
    list_threads,
    load_thread,
    threads_root,
)
from socratic_learn.workspace import prepare_here_workspace, prepare_personal_workspace


def _valid_session_body(*, title: str, next_step: str) -> str:
    sections = {
        "## Outcome": "Completed one observable result.",
        "## Diagnostic evidence": "Recorded one learner attempt.",
        "## Learning route": "1. Foundation -> application.",
        "## Concepts established": "One concept was established.",
        "## Application evidence": "Applied it to a changed case.",
        "## Remaining gaps": "One gap remains.",
        "## Next step": next_step,
        "## Sources": "No external sources were used.",
    }
    lines = [f"# Session: {datetime.now(UTC):%Y-%m-%d} — {title}", ""]
    for heading in SESSION_HEADINGS:
        lines.extend([heading, sections[heading], ""])
    return "\n".join(lines)


def _checkpoint_from_argv(argv: list[str]) -> Path:
    match = re.search(r"(?m)^Checkpoint path: (.+)$", argv[-1])
    if match is None:
        raise AssertionError("checkpoint path missing from launch payload")
    return Path(match.group(1))


class LauncherTests(unittest.TestCase):
    def test_first_run_prompts_goal_and_agent_then_saves_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            answers = iter(["1", "probability", "1"])
            calls: list[tuple[list[str], Path]] = []

            def fake_run(
                argv: list[str], *, cwd: Path, check: bool
            ) -> CompletedProcess[str]:
                calls.append((argv, cwd))
                return CompletedProcess(argv, 0)

            result = run_learning(
                LaunchOptions(goal=None, agent=None, here=False, cwd=Path.cwd()),
                input_fn=lambda prompt: next(answers),
                output=io.StringIO(),
                which={"pi": "C:/bin/pi.exe"}.get,
                run_process=fake_run,
                environ={"SOCRATIC_LEARN_HOME": temporary},
            )

            self.assertEqual(0, result)
            self.assertIn("probability", calls[0][0][-1])
            self.assertIn(
                '"default_agent": "pi"',
                (Path(temporary) / "config.json").read_text("utf-8"),
            )

    def test_child_exit_code_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_learning(
                LaunchOptions("logic", "pi", False, Path.cwd()),
                input_fn=lambda prompt: "",
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: CompletedProcess(argv, 23),
                environ={"SOCRATIC_LEARN_HOME": temporary},
            )
            self.assertEqual(23, result)

    def test_no_agent_fails_before_goal_prompt_with_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prompts: list[str] = []

            with self.assertRaisesRegex(UsageError, "compatible agent") as raised:
                run_learning(
                    LaunchOptions(None, None, False, Path.cwd()),
                    input_fn=lambda prompt: prompts.append(prompt) or "logic",
                    output=io.StringIO(),
                    which=lambda name: None,
                    environ={"SOCRATIC_LEARN_HOME": temporary},
                )

            self.assertEqual([], prompts)
            self.assertIsNotNone(raised.exception.remediation)

    def test_empty_prompted_goal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(UsageError, "goal"):
                run_learning(
                    LaunchOptions(None, "pi", False, Path.cwd()),
                    input_fn=lambda prompt: " \t ",
                    output=io.StringIO(),
                    which={"pi": "pi"}.get,
                    environ={"SOCRATIC_LEARN_HOME": temporary},
                    choose_item=lambda choices, **kwargs: choices[0].value,
                )

    def test_explicit_agent_must_be_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(UsageError, "claude"):
                run_learning(
                    LaunchOptions("logic", "claude", False, Path.cwd()),
                    input_fn=lambda prompt: self.fail("must not prompt"),
                    output=io.StringIO(),
                    which={"pi": "pi"}.get,
                    environ={"SOCRATIC_LEARN_HOME": temporary},
                )

    def test_saved_missing_host_prompts_once_and_rewrites_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "config.json").write_text(
                '{"schema_version": 1, "default_agent": "claude"}\n',
                encoding="utf-8",
            )
            prompts: list[str] = []
            output = io.StringIO()

            result = run_learning(
                LaunchOptions("logic", None, False, Path.cwd()),
                input_fn=lambda prompt: prompts.append(prompt) or "1",
                output=output,
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
                environ={"SOCRATIC_LEARN_HOME": temporary},
            )

            self.assertEqual(0, result)
            self.assertEqual(1, len(prompts))
            self.assertIn("claude", output.getvalue())
            self.assertIn(
                '"default_agent": "pi"',
                (home / "config.json").read_text(encoding="utf-8"),
            )

    def test_agent_selection_accepts_only_a_listed_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            answers = iter(["claude", "0", "3", "2"])
            prompts: list[str] = []
            calls: list[list[str]] = []

            result = run_learning(
                LaunchOptions("logic", None, False, Path.cwd()),
                input_fn=lambda prompt: prompts.append(prompt) or next(answers),
                output=io.StringIO(),
                which={"pi": "pi", "claude": "claude"}.get,
                run_process=lambda argv, **kwargs: (
                    calls.append(argv) or CompletedProcess(argv, 0)
                ),
                environ={"SOCRATIC_LEARN_HOME": temporary},
            )

            self.assertEqual(0, result)
            self.assertEqual(4, len(prompts))
            self.assertEqual("claude", calls[0][0])

    def test_here_uses_supplied_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            working_directories: list[Path] = []

            result = run_learning(
                LaunchOptions("logic", "pi", True, project),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, *, cwd, check: (
                    working_directories.append(cwd) or CompletedProcess(argv, 0)
                ),
                environ={"SOCRATIC_LEARN_HOME": str(Path(temporary) / "home")},
            )

            self.assertEqual(0, result)
            self.assertEqual([project.resolve()], working_directories)

    def test_goal_with_spaces_and_unicode_remains_one_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            calls: list[list[str]] = []
            goal = "Bayes cafés"

            run_learning(
                LaunchOptions(goal, "pi", False, Path.cwd()),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: (
                    calls.append(argv) or CompletedProcess(argv, 0)
                ),
                environ={"SOCRATIC_LEARN_HOME": temporary},
            )

            self.assertIn(goal, calls[0][-1])

    @unittest.skipUnless(os.name == "nt", "Windows shim acceptance runs on Windows")
    def test_windows_batch_shim_uses_safe_powershell_counterpart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shim = root / "pi.cmd"
            counterpart = root / "pi.ps1"
            receipt = root / "received.txt"
            side_effect = root / "injected.txt"
            shim.write_text(
                f'@echo off\r\necho batch-ran> "{side_effect}"\r\n',
                encoding="utf-8",
            )
            counterpart.write_text(
                "[IO.File]::WriteAllText("
                "$env:SOCRATIC_TEST_RECEIPT, $args[2], "
                "(New-Object Text.UTF8Encoding($false)))\n",
                encoding="utf-8",
            )
            goal = r"""learn & | < > ^ % ! " ' $() exactly"""
            environment = os.environ.copy()
            environment.update(
                {
                    "SOCRATIC_LEARN_HOME": str(root / "home"),
                    "SOCRATIC_TEST_RECEIPT": str(receipt),
                }
            )

            result = run_learning(
                LaunchOptions(goal, "pi", False, root),
                output=io.StringIO(),
                which={"pi": str(shim)}.get,
                run_process=lambda argv, **kwargs: subprocess.run(
                    argv,
                    env=environment,
                    **kwargs,
                ),
                environ=environment,
            )

            self.assertEqual(0, result)
            self.assertIn(goal, receipt.read_text(encoding="utf-8"))
            self.assertFalse(side_effect.exists())

    @unittest.skipUnless(os.name == "nt", "Windows shim policy runs on Windows")
    def test_windows_batch_shim_without_safe_counterpart_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shim = root / "pi.cmd"
            shim.write_text("@echo off\r\n", encoding="utf-8")

            with self.assertRaisesRegex(UsageError, "PowerShell counterpart") as raised:
                run_learning(
                    LaunchOptions("logic", "pi", False, root),
                    output=io.StringIO(),
                    which={"pi": str(shim)}.get,
                    run_process=lambda argv, **kwargs: self.fail(
                        "unsafe batch host must not run"
                    ),
                    environ={"SOCRATIC_LEARN_HOME": str(root / "home")},
                )

            self.assertIsNotNone(raised.exception.remediation)

    @unittest.skipUnless(os.name == "nt", "Windows shim policy runs on Windows")
    def test_windows_batch_shim_rejects_symlinked_counterpart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shim = root / "pi.cmd"
            shim.write_text("@echo off\r\n", encoding="utf-8")
            real = root / "real.ps1"
            real.write_text("exit 0\n", encoding="utf-8")
            counterpart = root / "pi.ps1"
            try:
                counterpart.symlink_to(real)
            except OSError:
                self.skipTest("symbolic links are unavailable")

            with self.assertRaisesRegex(UsageError, "safe regular file"):
                run_learning(
                    LaunchOptions("logic", "pi", False, root),
                    output=io.StringIO(),
                    which={"pi": str(shim)}.get,
                    run_process=lambda argv, **kwargs: self.fail(
                        "unsafe counterpart must not run"
                    ),
                    environ={"SOCRATIC_LEARN_HOME": str(root / "home")},
                )

    def test_child_inherits_stdio_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            keyword_arguments: list[dict[str, object]] = []

            def fake_run(argv: list[str], **kwargs: object) -> CompletedProcess[str]:
                keyword_arguments.append(kwargs)
                return CompletedProcess(argv, 0)

            run_learning(
                LaunchOptions("logic", "pi", False, Path.cwd()),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=fake_run,
                environ={
                    "SOCRATIC_LEARN_HOME": temporary,
                    "ANTHROPIC_API_KEY": "must-not-appear",
                },
            )

            self.assertEqual(
                {"cwd", "check"},
                set(keyword_arguments[0]),
            )
            self.assertFalse(keyword_arguments[0]["check"])

    def test_credentials_are_not_written_or_printed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            secret = "ANTHROPIC_API_KEY=must-not-appear"

            run_learning(
                LaunchOptions("logic", "pi", False, Path.cwd()),
                output=output,
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
                environ={
                    "SOCRATIC_LEARN_HOME": temporary,
                    "ANTHROPIC_API_KEY": secret,
                },
            )

            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(
                secret,
                (Path(temporary) / "config.json").read_text(encoding="utf-8")
                if (Path(temporary) / "config.json").exists()
                else "",
            )

    def test_keyboard_interrupt_from_child_returns_130(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            def interrupt(argv: list[str], **kwargs: object) -> CompletedProcess[str]:
                raise KeyboardInterrupt

            result = run_learning(
                LaunchOptions("logic", "pi", False, Path.cwd()),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=interrupt,
                environ={"SOCRATIC_LEARN_HOME": temporary},
            )

            self.assertEqual(130, result)

    def test_keyboard_interrupt_finalizes_valid_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            def interrupt(argv: list[str], **kwargs: object) -> CompletedProcess[str]:
                _checkpoint_from_argv(argv).write_text(
                    _valid_session_body(
                        title="Logic: Interrupted but saved",
                        next_step="Resume from the saved checkpoint.",
                    ),
                    encoding="utf-8",
                )
                raise KeyboardInterrupt

            result = run_learning(
                LaunchOptions("logic", "pi", False, Path.cwd()),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=interrupt,
                environ={"SOCRATIC_LEARN_HOME": temporary},
            )

            self.assertEqual(130, result)
            records = Path(temporary) / "learning/.socratic-learn/workspace"
            stored = list_threads(records)
            self.assertEqual("complete", stored[0].status)
            self.assertEqual("Logic: Interrupted but saved", stored[0].display_title)

    def test_keyboard_interrupt_while_prompting_is_not_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(KeyboardInterrupt):
                run_learning(
                    LaunchOptions(None, None, False, Path.cwd()),
                    input_fn=lambda prompt: (_ for _ in ()).throw(KeyboardInterrupt),
                    output=io.StringIO(),
                    which={"pi": "pi"}.get,
                    environ={"SOCRATIC_LEARN_HOME": temporary},
                )

    def test_executable_disappearance_is_a_filesystem_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            def disappeared(
                argv: list[str], **kwargs: object
            ) -> CompletedProcess[str]:
                raise FileNotFoundError(argv[0])

            with self.assertRaises(FilesystemError) as raised:
                run_learning(
                    LaunchOptions("logic", "pi", False, Path.cwd()),
                    output=io.StringIO(),
                    which={"pi": "C:/bin/pi.exe"}.get,
                    run_process=disappeared,
                    environ={"SOCRATIC_LEARN_HOME": temporary},
                )

            self.assertIn("pi", str(raised.exception))
            self.assertIn("C:/bin/pi.exe", str(raised.exception))

    def test_bare_launch_shows_new_session_before_goal_and_creates_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            menus: list[tuple[object, ...]] = []
            prompts: list[str] = []
            calls: list[list[str]] = []

            def choose(choices, **_kwargs):
                menus.append(tuple(choices))
                return choices[0].value

            result = run_learning(
                LaunchOptions(None, "pi", False, Path.cwd()),
                input_fn=lambda prompt: prompts.append(prompt) or "learn logic",
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: (
                    calls.append(argv) or CompletedProcess(argv, 0)
                ),
                environ={"SOCRATIC_LEARN_HOME": temporary},
                choose_item=choose,
                menu_is_interactive=lambda: False,
            )

            self.assertEqual(0, result)
            self.assertEqual(["New session"], [choice.label for choice in menus[0]])
            self.assertEqual(["What do you want to learn? "], prompts)
            checkpoint = _checkpoint_from_argv(calls[0])
            self.assertTrue(checkpoint.is_absolute())
            self.assertFalse(checkpoint.exists())
            records = Path(temporary) / "learning/.socratic-learn/workspace"
            stored = list_threads(records)
            self.assertEqual(1, len(stored))
            self.assertEqual("incomplete", stored[0].status)
            self.assertIn("learn logic", calls[0][-1])

    def test_recent_menu_shows_five_then_view_all_with_every_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = prepare_personal_workspace(Path(temporary))
            for number in range(6):
                create_thread(workspace.records, goal=f"Thread {number}")
            legacy = workspace.records / "sessions/legacy.md"
            legacy.write_text(
                _valid_session_body(
                    title="Legacy newest",
                    next_step="Resume the legacy task.",
                ),
                encoding="utf-8",
            )
            future = datetime.now(UTC).timestamp() + 60
            os.utime(legacy, (future, future))
            menus: list[tuple[object, ...]] = []

            def choose(choices, **_kwargs):
                menus.append(tuple(choices))
                if len(menus) == 1:
                    return next(choice.value for choice in choices if choice.label == "View all")
                return next(choice.value for choice in choices if choice.label == "New session")

            run_learning(
                LaunchOptions(None, "pi", False, Path.cwd()),
                input_fn=lambda prompt: "another topic",
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
                environ={"SOCRATIC_LEARN_HOME": temporary},
                choose_item=choose,
                menu_is_interactive=lambda: False,
            )

            self.assertEqual(7, len(menus[0]))
            self.assertEqual("New session", menus[0][0].label)
            self.assertEqual("Legacy newest", menus[0][1].label)
            self.assertEqual("View all", menus[0][-1].label)
            self.assertEqual(8, len(menus[1]))
            self.assertNotIn("View all", [choice.label for choice in menus[1]])
            self.assertIn("Resume the legacy task.", menus[0][1].detail)

    def test_resume_uses_latest_complete_checkpoint_and_updates_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = prepare_personal_workspace(Path(temporary))
            thread = create_thread(workspace.records, goal="original goal")
            previous_pending = create_pending_encounter(workspace.records, thread.id)
            previous_pending.write_text(
                _valid_session_body(
                    title="Logic: Prior title",
                    next_step="Test a counterexample.",
                ),
                encoding="utf-8",
            )
            previous = finalize_thread_encounter(
                workspace.records,
                thread.id,
                previous_pending,
            )
            previous_path = (
                threads_root(workspace.records)
                / thread.id
                / "sessions"
                / str(previous.latest_encounter)
            ).resolve()
            payloads: list[str] = []

            def choose(choices, **_kwargs):
                return next(
                    choice.value
                    for choice in choices
                    if choice.label == "Logic: Prior title"
                )

            def run(argv, **_kwargs):
                payloads.append(argv[-1])
                _checkpoint_from_argv(argv).write_text(
                    _valid_session_body(
                        title="Logic: Refined title",
                        next_step="Apply the refined rule.",
                    ),
                    encoding="utf-8",
                )
                return CompletedProcess(argv, 0)

            run_learning(
                LaunchOptions(None, "pi", False, Path.cwd()),
                input_fn=lambda prompt: self.fail("resume must not ask for a goal"),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=run,
                environ={"SOCRATIC_LEARN_HOME": temporary},
                choose_item=choose,
                menu_is_interactive=lambda: False,
            )

            payload = payloads[0]
            self.assertIn("Selected thread title: Logic: Prior title", payload)
            self.assertIn(f"Prior complete encounter: {previous_path}", payload)
            self.assertIn("Saved next step: Test a counterexample.", payload)
            updated = load_thread(workspace.records, thread.id)
            self.assertEqual("Logic: Refined title", updated.refined_title)
            self.assertEqual("complete", updated.status)

    def test_incomplete_resume_requires_prior_evidence_to_be_reestablished(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = prepare_personal_workspace(Path(temporary))
            create_thread(workspace.records, goal="unfinished topic")
            payloads: list[str] = []

            run_learning(
                LaunchOptions(None, "pi", False, Path.cwd()),
                input_fn=lambda prompt: self.fail("resume must not prompt for goal"),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: (
                    payloads.append(argv[-1]) or CompletedProcess(argv, 0)
                ),
                environ={"SOCRATIC_LEARN_HOME": temporary},
                choose_item=lambda choices, **kwargs: choices[1].value,
                menu_is_interactive=lambda: False,
            )

            self.assertIn("No complete checkpoint exists", payloads[0])
            self.assertIn("prior evidence must be re-established", payloads[0])

    def test_legacy_selection_imports_without_removing_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = prepare_personal_workspace(Path(temporary))
            legacy = workspace.records / "sessions/legacy.md"
            legacy.write_text(
                _valid_session_body(
                    title="History: Legacy route",
                    next_step="Compare one more route.",
                ),
                encoding="utf-8",
            )
            payloads: list[str] = []

            run_learning(
                LaunchOptions(None, "pi", False, Path.cwd()),
                input_fn=lambda prompt: self.fail("legacy resume must not prompt"),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: (
                    payloads.append(argv[-1]) or CompletedProcess(argv, 0)
                ),
                environ={"SOCRATIC_LEARN_HOME": temporary},
                choose_item=lambda choices, **kwargs: choices[1].value,
                menu_is_interactive=lambda: False,
            )

            self.assertTrue(legacy.is_file())
            self.assertEqual(1, len(list_threads(workspace.records)))
            self.assertIn("Selected thread title: History: Legacy route", payloads[0])
            self.assertIn("Prior complete encounter:", payloads[0])

    def test_explicit_goal_bypasses_chooser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_learning(
                LaunchOptions("explicit goal", "pi", False, Path.cwd()),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
                environ={"SOCRATIC_LEARN_HOME": temporary},
                choose_item=lambda choices, **kwargs: self.fail("chooser called"),
            )

    def test_malformed_checkpoint_warns_discards_raw_text_and_preserves_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            raw = "ANTHROPIC_API_KEY=secret raw transcript"

            def malformed(argv, **_kwargs):
                _checkpoint_from_argv(argv).write_text(raw, encoding="utf-8")
                return CompletedProcess(argv, 19)

            result = run_learning(
                LaunchOptions("goal", "pi", False, Path.cwd()),
                output=output,
                which={"pi": "pi"}.get,
                run_process=malformed,
                environ={"SOCRATIC_LEARN_HOME": temporary},
            )

            self.assertEqual(19, result)
            self.assertIn("warning:", output.getvalue())
            self.assertNotIn(raw, output.getvalue())
            records = Path(temporary) / "learning/.socratic-learn/workspace"
            thread = load_thread(records, list_threads(records)[0].id)
            self.assertEqual("incomplete", thread.status)
            sessions = threads_root(records) / thread.id / "sessions"
            self.assertEqual([], list(sessions.iterdir()))
            self.assertNotIn(
                raw,
                (threads_root(records) / thread.id / "thread.json").read_text("utf-8"),
            )

    def test_control_characters_are_sanitized_only_in_menu_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            goal = "line one\nline two\tkept"
            run_learning(
                LaunchOptions(goal, "pi", False, Path.cwd()),
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
                environ={"SOCRATIC_LEARN_HOME": temporary},
            )
            seen: list[tuple[object, ...]] = []

            def choose(choices, **_kwargs):
                seen.append(tuple(choices))
                return choices[0].value

            run_learning(
                LaunchOptions(None, "pi", False, Path.cwd()),
                input_fn=lambda prompt: "new",
                output=io.StringIO(),
                which={"pi": "pi"}.get,
                run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
                environ={"SOCRATIC_LEARN_HOME": temporary},
                choose_item=choose,
                menu_is_interactive=lambda: False,
            )

            labels = [choice.label for choice in seen[0]]
            self.assertTrue(all("\n" not in label and "\t" not in label for label in labels))
            records = Path(temporary) / "learning/.socratic-learn/workspace"
            stored_goals = [load_thread(records, item.id).original_goal for item in list_threads(records)]
            self.assertIn(goal, stored_goals)

    def test_personal_and_here_launches_use_isolated_choosers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            project = root / "project"
            project.mkdir()
            personal = prepare_personal_workspace(home)
            local = prepare_here_workspace(project)
            create_thread(personal.records, goal="personal-only")
            create_thread(local.records, goal="here-only")
            seen: list[list[str]] = []

            def choose(choices, **_kwargs):
                seen.append([choice.label for choice in choices])
                return choices[0].value

            for here, cwd, goal in (
                (False, root, "new personal"),
                (True, project, "new here"),
            ):
                run_learning(
                    LaunchOptions(None, "pi", here, cwd),
                    input_fn=lambda prompt, answer=goal: answer,
                    output=io.StringIO(),
                    which={"pi": "pi"}.get,
                    run_process=lambda argv, **kwargs: CompletedProcess(argv, 0),
                    environ={"SOCRATIC_LEARN_HOME": str(home)},
                    choose_item=choose,
                    menu_is_interactive=lambda: False,
                )

            self.assertIn("personal-only", seen[0])
            self.assertNotIn("here-only", seen[0])
            self.assertIn("here-only", seen[1])
            self.assertNotIn("personal-only", seen[1])
