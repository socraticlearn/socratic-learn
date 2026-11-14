from __future__ import annotations

import io
import re
import unittest
from collections.abc import Iterator

from socratic_learn.errors import UsageError
from socratic_learn.terminal_menu import KeyEvent, MenuChoice, choose_menu_item


ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class TerminalMenuTests(unittest.TestCase):
    def test_empty_choices_raise_usage_error(self) -> None:
        with self.assertRaisesRegex(UsageError, "at least one"):
            choose_menu_item(
                (),
                output=io.StringIO(),
                is_interactive=lambda: False,
                input_fn=lambda prompt: "1",
            )

    def test_duplicate_values_raise_usage_error(self) -> None:
        choices = (
            MenuChoice("same", "First"),
            MenuChoice("same", "Second"),
        )
        with self.assertRaisesRegex(UsageError, "duplicate"):
            choose_menu_item(
                choices,
                output=io.StringIO(),
                is_interactive=lambda: False,
                input_fn=lambda prompt: "1",
            )

    def test_preserves_unicode_labels_and_details(self) -> None:
        choices = (MenuChoice("jp", "数学", "次の一歩: 証明"),)
        output = io.StringIO()
        result = choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: False,
            input_fn=lambda prompt: "1",
        )
        self.assertEqual("jp", result)
        text = output.getvalue()
        self.assertIn("数学", text)
        self.assertIn("次の一歩: 証明", text)

    def test_numbered_fallback_lists_choices_in_order(self) -> None:
        choices = (
            MenuChoice("new", "New session"),
            MenuChoice("all", "View all", "older threads"),
        )
        output = io.StringIO()
        choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: False,
            input_fn=lambda prompt: "2",
        )
        text = output.getvalue()
        self.assertIn("1. New session\n", text)
        self.assertIn("2. View all — older threads\n", text)

    def test_numbered_fallback_rejects_non_canonical_integers(self) -> None:
        choices = (
            MenuChoice("a", "Alpha"),
            MenuChoice("b", "Beta"),
        )
        answers = iter(["01", "2x", "0", "3", "2"])
        output = io.StringIO()
        prompts: list[str] = []

        result = choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: False,
            input_fn=lambda prompt: prompts.append(prompt) or next(answers),
        )
        self.assertEqual("b", result)
        self.assertEqual(5, len(prompts))

    def test_numbered_fallback_emits_no_ansi_control_sequences(self) -> None:
        choices = (MenuChoice("one", "Only"),)
        output = io.StringIO()
        choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: False,
            input_fn=lambda prompt: "1",
        )
        self.assertIsNone(ANSI_ESCAPE.search(output.getvalue()))

    def test_interactive_enter_returns_stable_value(self) -> None:
        choices = (
            MenuChoice("new", "New session"),
            MenuChoice("t1", "Logic", "2025-11-01 · review sets"),
        )
        keys: Iterator[KeyEvent] = iter([KeyEvent.DOWN, KeyEvent.ENTER])
        output = io.StringIO()

        result = choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: True,
            read_key=lambda: next(keys),
        )
        self.assertEqual("t1", result)
        final = output.getvalue()
        self.assertIn("Logic", final)
        self.assertIn("2025-11-01 · review sets", final)
        self.assertNotIn("\x1b", final)

    def test_interactive_wraps_down_and_up(self) -> None:
        choices = (
            MenuChoice("a", "Alpha"),
            MenuChoice("b", "Beta"),
            MenuChoice("c", "Gamma"),
        )

        down_keys: Iterator[KeyEvent] = iter(
            [KeyEvent.DOWN, KeyEvent.DOWN, KeyEvent.ENTER]
        )
        down_result = choose_menu_item(
            choices,
            output=io.StringIO(),
            is_interactive=lambda: True,
            read_key=lambda: next(down_keys),
        )
        self.assertEqual("c", down_result)

        up_keys: Iterator[KeyEvent] = iter([KeyEvent.UP, KeyEvent.ENTER])
        up_result = choose_menu_item(
            choices,
            output=io.StringIO(),
            is_interactive=lambda: True,
            read_key=lambda: next(up_keys),
        )
        self.assertEqual("c", up_result)

    def test_interactive_ctrl_c_raises_keyboard_interrupt(self) -> None:
        choices = (MenuChoice("only", "Only"),)
        output = io.StringIO()
        with self.assertRaises(KeyboardInterrupt):
            choose_menu_item(
                choices,
                output=output,
                is_interactive=lambda: True,
                read_key=lambda: KeyEvent.INTERRUPT,
            )
        self.assertTrue(output.getvalue().endswith("\n"))

    def test_interactive_output_remains_readable_after_selection(self) -> None:
        choices = (
            MenuChoice("new", "New session"),
            MenuChoice("resume", "Resume", "practice problem 3"),
        )
        output = io.StringIO()
        choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: True,
            read_key=lambda: KeyEvent.ENTER,
        )
        lines = [line for line in output.getvalue().splitlines() if line.strip()]
        self.assertTrue(lines[-1].startswith("> New session"))
        self.assertNotIn("\x1b", output.getvalue())

    def test_omits_detail_when_absent(self) -> None:
        choices = (MenuChoice("x", "Plain label"),)
        output = io.StringIO()
        choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: False,
            input_fn=lambda prompt: "1",
        )
        self.assertIn("1. Plain label\n", output.getvalue())
        self.assertNotIn("—", output.getvalue())

    def test_uses_default_is_interactive_when_not_overridden(self) -> None:
        class _Stdin(io.StringIO):
            def isatty(self) -> bool:
                return True

        class _Stdout(io.StringIO):
            def isatty(self) -> bool:
                return True

        keys: Iterator[KeyEvent] = iter([KeyEvent.ENTER])
        choose_menu_item(
            (MenuChoice("ok", "OK"),),
            output=_Stdout(),
            input_stream=_Stdin(),
            read_key=lambda: next(keys),
        )


if __name__ == "__main__":
    unittest.main()