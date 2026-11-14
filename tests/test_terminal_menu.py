from __future__ import annotations

import io
import re
import unittest
from collections.abc import Iterator

from socratic_learn.errors import UsageError
from socratic_learn.terminal_menu import (
    KeyEvent,
    MenuChoice,
    choose_menu_item,
    decode_posix_escape_sequence,
    decode_windows_scan_code,
)


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

    def test_rejects_control_characters_in_choice_text(self) -> None:
        with self.assertRaisesRegex(UsageError, "control character"):
            MenuChoice("x", "bad\nlabel")
        with self.assertRaisesRegex(UsageError, "control character"):
            MenuChoice("bad\r", "Label")
        with self.assertRaisesRegex(UsageError, "control character"):
            MenuChoice("ok", "Label", "detail\x1fhere")

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
        answers = iter(["01", "2x", "0", "3", " 2", "2 ", "2"])
        output = io.StringIO()
        prompts: list[str] = []

        result = choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: False,
            input_fn=lambda prompt: prompts.append(prompt) or next(answers),
        )
        self.assertEqual("b", result)
        self.assertEqual(7, len(prompts))
        self.assertIn("digits only", output.getvalue())

    def test_numbered_fallback_writes_prompt_to_output_stream(self) -> None:
        choices = (MenuChoice("only", "Only"),)
        output = io.StringIO()
        stdin = io.StringIO("1\n")

        result = choose_menu_item(
            choices,
            output=output,
            input_stream=stdin,
            is_interactive=lambda: False,
        )
        self.assertEqual("only", result)
        self.assertIn("Enter the number of your choice:", output.getvalue())

    def test_numbered_fallback_eof_raises_after_newline_flush(self) -> None:
        choices = (MenuChoice("only", "Only"),)
        output = io.StringIO()
        stdin = io.StringIO("")

        with self.assertRaises(EOFError):
            choose_menu_item(
                choices,
                output=output,
                input_stream=stdin,
                is_interactive=lambda: False,
            )
        self.assertTrue(output.getvalue().endswith("\n"))

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

    def test_interactive_unknown_key_does_not_move_selection(self) -> None:
        choices = (
            MenuChoice("stay", "Stay here"),
            MenuChoice("move", "Would move"),
        )
        keys: Iterator[KeyEvent] = iter(
            [KeyEvent.UNKNOWN, KeyEvent.UNKNOWN, KeyEvent.ENTER]
        )
        output = io.StringIO()
        result = choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: True,
            read_key=lambda: next(keys),
        )
        self.assertEqual("stay", result)
        self.assertTrue(output.getvalue().splitlines()[-1].startswith("> Stay here"))

    def test_interactive_eof_raises_after_newline_flush(self) -> None:
        choices = (MenuChoice("only", "Only"),)
        output = io.StringIO()
        with self.assertRaises(EOFError):
            choose_menu_item(
                choices,
                output=output,
                is_interactive=lambda: True,
                read_key=lambda: KeyEvent.EOF,
            )
        self.assertTrue(output.getvalue().endswith("\n"))

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

    def test_interactive_redraw_erases_longer_previous_row(self) -> None:
        choices = (
            MenuChoice(
                "long",
                "A very long menu label",
                "with extended trailing detail",
            ),
            MenuChoice("short", "Tiny"),
        )
        keys: Iterator[KeyEvent] = iter([KeyEvent.DOWN, KeyEvent.ENTER])
        output = io.StringIO()
        choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: True,
            read_key=lambda: next(keys),
        )
        raw = output.getvalue()
        self.assertIn("> Tiny", raw)
        final_row = [part for part in raw.replace("\r", "\n").split("\n") if part.strip()][
            -1
        ]
        self.assertGreater(len(final_row), len("> Tiny"))
        self.assertTrue(final_row.rstrip().endswith("Tiny"))
        self.assertNotIn("\x1b", raw)

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


class NativeKeyParsingTests(unittest.TestCase):
    def test_windows_unknown_scan_code_maps_to_unknown(self) -> None:
        self.assertEqual(KeyEvent.UNKNOWN, decode_windows_scan_code(75))

    def test_windows_arrow_scan_codes(self) -> None:
        self.assertEqual(KeyEvent.UP, decode_windows_scan_code(72))
        self.assertEqual(KeyEvent.DOWN, decode_windows_scan_code(80))

    def test_posix_bare_escape_is_unknown(self) -> None:
        self.assertEqual(
            KeyEvent.UNKNOWN,
            decode_posix_escape_sequence("\x1b", None, None),
        )

    def test_posix_partial_escape_is_unknown(self) -> None:
        self.assertEqual(
            KeyEvent.UNKNOWN,
            decode_posix_escape_sequence("\x1b", "[", None),
        )

    def test_posix_arrow_sequences(self) -> None:
        self.assertEqual(
            KeyEvent.UP,
            decode_posix_escape_sequence("\x1b", "[", "A"),
        )
        self.assertEqual(
            KeyEvent.DOWN,
            decode_posix_escape_sequence("\x1b", "[", "B"),
        )

    def test_posix_unknown_sequence_tail(self) -> None:
        self.assertEqual(
            KeyEvent.UNKNOWN,
            decode_posix_escape_sequence("\x1b", "[", "C"),
        )


if __name__ == "__main__":
    unittest.main()