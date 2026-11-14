from __future__ import annotations

import io
import re
import sys
import unittest
from collections.abc import Iterator
from contextlib import redirect_stdout
from unittest.mock import Mock

import socratic_learn.terminal_menu as terminal_menu
from socratic_learn.errors import UsageError
from socratic_learn.terminal_menu import (
    KeyEvent,
    MenuChoice,
    choose_menu_item,
    format_menu_row,
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

    def test_rejects_unicode_cc_including_c1_csi(self) -> None:
        with self.assertRaisesRegex(UsageError, "control character"):
            MenuChoice("ok", "label\u009bCSIn")
        with self.assertRaisesRegex(UsageError, "control character"):
            MenuChoice("ok", "Label", "detail\u0080tail")

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

    def test_numbered_fallback_builtin_input_prints_prompt_once(self) -> None:
        output = io.StringIO()
        with (
            unittest.mock.patch.object(sys, "stdin", io.StringIO("1\n")),
            redirect_stdout(output),
        ):
            result = choose_menu_item(
                (MenuChoice("only", "Only"),),
                output=output,
                input_fn=input,
                is_interactive=lambda: False,
            )
        self.assertEqual("only", result)
        self.assertEqual(
            1,
            output.getvalue().count("Enter the number of your choice:"),
        )

    def test_numbered_fallback_rejects_oversized_digit_strings(self) -> None:
        choices = (
            MenuChoice("a", "Alpha"),
            MenuChoice("b", "Beta"),
        )
        answers = iter(["9999999999999999999999", "2"])
        output = io.StringIO()

        result = choose_menu_item(
            choices,
            output=output,
            is_interactive=lambda: False,
            input_fn=lambda prompt: next(answers),
        )
        self.assertEqual("b", result)
        self.assertIn("digits only", output.getvalue())

    def test_numbered_input_fn_eof_flushes_before_re_raise(self) -> None:
        choices = (MenuChoice("only", "Only"),)
        output = io.StringIO()

        def raise_eof(prompt: str) -> str:
            raise EOFError("injected eof")

        with self.assertRaises(EOFError):
            choose_menu_item(
                choices,
                output=output,
                is_interactive=lambda: False,
                input_fn=raise_eof,
            )
        self.assertTrue(output.getvalue().endswith("\n"))

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
        self.assertIn("New session", final)
        self.assertIn("↑/↓ move · Enter select", final)
        self.assertIn("\x1b", final)

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
        self.assertIn("> Stay here", output.getvalue())

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

    def test_interactive_redraw_uses_display_width_for_wide_characters(self) -> None:
        choices = (
            MenuChoice("wide", "数学数学", "次の一歩"),
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
        wide_width = terminal_menu._terminal_display_width(
            format_menu_row(choices[0], selected=True)
        )
        tiny_width = terminal_menu._terminal_display_width(
            format_menu_row(choices[1], selected=True)
        )
        self.assertGreater(wide_width, tiny_width)
        self.assertIn("数学数学", output.getvalue())
        self.assertIn("> Tiny", output.getvalue())

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
        self.assertIn("\x1b[2K", raw)

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
        self.assertIn("> New session", output.getvalue())
        self.assertIn("  Resume", output.getvalue())

    def test_interactive_rows_truncate_to_terminal_display_width(self) -> None:
        choices = (
            MenuChoice("long", "A very long menu label", "with long detail"),
            MenuChoice("short", "Short"),
        )
        lines = terminal_menu._interactive_lines(
            choices,
            selected_index=0,
            columns=18,
        )
        self.assertTrue(all(terminal_menu._terminal_display_width(line) <= 18 for line in lines))
        self.assertTrue(any(line.endswith("…") for line in lines))

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


class PublicApiTests(unittest.TestCase):
    def test_exports_only_menu_facing_symbols(self) -> None:
        self.assertEqual(
            (
                "KeyEvent",
                "MenuChoice",
                "choose_menu_item",
                "format_menu_row",
                "read_interactive_key",
            ),
            terminal_menu.__all__,
        )


class DisplayWidthTests(unittest.TestCase):
    def test_counts_east_asian_wide_characters(self) -> None:
        self.assertEqual(4, terminal_menu._terminal_display_width("数学"))
        self.assertGreater(
            terminal_menu._terminal_display_width("> 数学 🎓"),
            terminal_menu._terminal_display_width("> ab"),
        )

    def test_ignores_combining_and_format_characters(self) -> None:
        self.assertEqual(1, terminal_menu._terminal_display_width("e\u0301"))
        self.assertEqual(1, terminal_menu._terminal_display_width("a\u200d"))

    def test_conservative_width_for_ambiguous_and_keycap_sequences(self) -> None:
        self.assertEqual(2, terminal_menu._terminal_display_width("§"))
        keycap = "1\uFE0F\u20E3"
        self.assertGreaterEqual(terminal_menu._terminal_display_width(keycap), 2)

    def test_ascii_width_stays_compact(self) -> None:
        self.assertEqual(len("Tiny"), terminal_menu._terminal_display_width("Tiny"))
        self.assertEqual(len("> Plain label"), terminal_menu._terminal_display_width("> Plain label"))


class NativeKeyParsingTests(unittest.TestCase):
    def test_windows_unknown_scan_code_maps_to_unknown(self) -> None:
        self.assertEqual(KeyEvent.UNKNOWN, terminal_menu._decode_windows_scan_code(75))

    def test_windows_arrow_scan_codes(self) -> None:
        self.assertEqual(KeyEvent.UP, terminal_menu._decode_windows_scan_code(72))
        self.assertEqual(KeyEvent.DOWN, terminal_menu._decode_windows_scan_code(80))

    def test_posix_bare_escape_is_unknown(self) -> None:
        self.assertEqual(
            KeyEvent.UNKNOWN,
            terminal_menu._decode_posix_escape_sequence("\x1b", None, None),
        )

    def test_posix_partial_escape_is_unknown(self) -> None:
        self.assertEqual(
            KeyEvent.UNKNOWN,
            terminal_menu._decode_posix_escape_sequence("\x1b", "[", None),
        )

    def test_posix_arrow_sequences(self) -> None:
        self.assertEqual(
            KeyEvent.UP,
            terminal_menu._decode_posix_escape_sequence("\x1b", "[", "A"),
        )
        self.assertEqual(
            KeyEvent.DOWN,
            terminal_menu._decode_posix_escape_sequence("\x1b", "[", "B"),
        )

    def test_posix_unknown_sequence_tail(self) -> None:
        self.assertEqual(
            KeyEvent.UNKNOWN,
            terminal_menu._decode_posix_escape_sequence("\x1b", "[", "C"),
        )

    def test_posix_resolver_eof_on_missing_first_byte(self) -> None:
        self.assertEqual(KeyEvent.EOF, terminal_menu._resolve_posix_menu_key(None))

    def test_posix_resolver_timeout_after_escape_is_unknown_not_eof(self) -> None:
        self.assertEqual(
            KeyEvent.UNKNOWN,
            terminal_menu._resolve_posix_menu_key("\x1b", second="timeout"),
        )

    def test_posix_resolver_eof_on_continuation_after_escape_is_unknown(self) -> None:
        self.assertEqual(
            KeyEvent.UNKNOWN,
            terminal_menu._resolve_posix_menu_key("\x1b", second="eof"),
        )

    def test_posix_first_byte_blocks_without_timed_read(self) -> None:
        timed = Mock(side_effect=AssertionError("timed read"))
        blocking = Mock(return_value="\n")
        self.assertEqual(
            KeyEvent.ENTER,
            terminal_menu._read_posix_menu_key(
                0,
                read_blocking=blocking,
                read_timed=timed,
            ),
        )
        blocking.assert_called_once()
        timed.assert_not_called()

    def test_posix_escape_uses_timed_reads_only_for_continuations(self) -> None:
        timed = Mock(side_effect=["timeout"])
        blocking = Mock(return_value="\x1b")
        self.assertEqual(
            KeyEvent.UNKNOWN,
            terminal_menu._read_posix_menu_key(
                0,
                read_blocking=blocking,
                read_timed=timed,
            ),
        )
        blocking.assert_called_once()
        timed.assert_called_once()

    def test_posix_raw_mode_spans_menu_and_restores_after_selection(self) -> None:
        choices = (
            MenuChoice("a", "Alpha"),
            MenuChoice("b", "Beta"),
        )
        events = iter([KeyEvent.DOWN, KeyEvent.ENTER])
        setraw = Mock()
        restore = Mock()
        with (
            unittest.mock.patch.object(terminal_menu.os, "name", "posix"),
            unittest.mock.patch.object(
                terminal_menu,
                "_read_posix_menu_key",
                side_effect=lambda fd: next(events),
            ),
            unittest.mock.patch.object(
                terminal_menu,
                "_enter_posix_raw_mode",
                return_value=(9, ("saved",), setraw, restore),
            ),
        ):
            result = choose_menu_item(
                choices,
                output=io.StringIO(),
                input_stream=Mock(fileno=Mock(return_value=9)),
                is_interactive=lambda: True,
            )
        self.assertEqual("b", result)
        setraw.assert_called_once_with(9)
        restore.assert_called_once_with(9, ("saved",))


if __name__ == "__main__":
    unittest.main()