"""Dependency-free interactive and numbered terminal menus."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
import os
import sys
import unicodedata
from typing import TextIO

from .errors import UsageError

__all__ = (
    "KeyEvent",
    "MenuChoice",
    "choose_menu_item",
    "format_menu_row",
    "read_interactive_key",
)

_POSIX_ESCAPE_TIMEOUT_SECONDS = 0.05
_NUMBERED_INVALID_HINT = "Enter a number from 1 to {count} using digits only."
_MENU_EOF_MESSAGE = "terminal menu input ended"
_NOT_PROVIDED = object()


class _TimedOut:
    """Sentinel returned when a timed POSIX read finds no input."""


_TIMED_OUT = _TimedOut()


class KeyEvent(Enum):
    """Normalized navigation keys for interactive menus."""

    UP = "up"
    DOWN = "down"
    ENTER = "enter"
    INTERRUPT = "interrupt"
    UNKNOWN = "unknown"
    EOF = "eof"


@dataclass(frozen=True, slots=True)
class MenuChoice:
    """One selectable menu entry with a stable value and human-readable text."""

    value: str
    label: str
    detail: str | None = None

    def __post_init__(self) -> None:
        _reject_control_text(self.value, "value")
        _reject_control_text(self.label, "label")
        if self.detail is not None:
            _reject_control_text(self.detail, "detail")


def _reject_control_text(text: str, field: str) -> None:
    for character in text:
        if unicodedata.category(character) == "Cc":
            raise UsageError(
                f"menu choice {field} contains a control character",
                remediation="remove CR, LF, CSI, and other terminal control characters",
            )


def _terminal_display_width(text: str) -> int:
    """Return a conservative terminal column width for interactive row erase."""
    width = 0
    for character in text:
        category = unicodedata.category(character)
        if category in {"Mn", "Mc", "Cf"}:
            continue
        if category == "Me":
            width += 2
            continue
        east_asian = unicodedata.east_asian_width(character)
        if east_asian in {"W", "F", "A"}:
            width += 2
        elif category in {"So", "Sk"}:
            width += 2
        else:
            width += 1
    return width


def format_menu_row(choice: MenuChoice, *, selected: bool = False) -> str:
    """Return a compact single-line row suitable for thread picker output."""
    marker = ">" if selected else " "
    if choice.detail:
        return f"{marker} {choice.label} — {choice.detail}"
    return f"{marker} {choice.label}"


def _decode_windows_scan_code(code: int) -> KeyEvent:
    """Map a Windows extended-key scan code to a normalized event."""
    if code == 72:
        return KeyEvent.UP
    if code == 80:
        return KeyEvent.DOWN
    return KeyEvent.UNKNOWN


def _decode_posix_escape_sequence(
    first: str,
    second: str | None,
    third: str | None,
) -> KeyEvent:
    """Map POSIX escape bytes to a normalized event without blocking reads."""
    if first != "\x1b":
        return KeyEvent.UNKNOWN
    if second is None:
        return KeyEvent.UNKNOWN
    if second != "[":
        return KeyEvent.UNKNOWN
    if third is None:
        return KeyEvent.UNKNOWN
    if third == "A":
        return KeyEvent.UP
    if third == "B":
        return KeyEvent.DOWN
    return KeyEvent.UNKNOWN


def _resolve_posix_menu_key(
    first: str | None,
    second: str | object = _NOT_PROVIDED,
    third: str | object = _NOT_PROVIDED,
) -> KeyEvent:
    """Resolve POSIX key bytes, distinguishing timeout, EOF, and arrow sequences."""
    if first is None:
        return KeyEvent.EOF
    if first == "\x03":
        return KeyEvent.INTERRUPT
    if first in {"\r", "\n"}:
        return KeyEvent.ENTER
    if first != "\x1b":
        return KeyEvent.UNKNOWN

    if second is _NOT_PROVIDED:
        return KeyEvent.UNKNOWN
    if second in {"timeout", "eof"}:
        return KeyEvent.UNKNOWN
    if second != "[":
        return KeyEvent.UNKNOWN
    if third is _NOT_PROVIDED:
        return KeyEvent.UNKNOWN
    if third in {"timeout", "eof"}:
        return KeyEvent.UNKNOWN
    return _decode_posix_escape_sequence("\x1b", "[", str(third))


def _validate_choices(choices: Sequence[MenuChoice]) -> None:
    if not choices:
        raise UsageError(
            "menu requires at least one choice",
            remediation="provide one or more MenuChoice items",
        )
    seen: set[str] = set()
    for choice in choices:
        if choice.value in seen:
            raise UsageError(
                "menu contains duplicate choice values",
                remediation="use a unique stable value for every MenuChoice",
            )
        seen.add(choice.value)


def _default_is_interactive(output: TextIO, input_stream: TextIO) -> bool:
    return output.isatty() and input_stream.isatty()


def _format_numbered_list(choices: Sequence[MenuChoice]) -> str:
    lines: list[str] = []
    for index, choice in enumerate(choices, start=1):
        if choice.detail:
            lines.append(f"{index}. {choice.label} — {choice.detail}")
        else:
            lines.append(f"{index}. {choice.label}")
    return "\n".join(lines) + "\n"


def _parse_canonical_choice_number(answer: str, maximum: int) -> int | None:
    if not answer.isascii() or any(character.isspace() for character in answer):
        return None
    if not answer.isdigit():
        return None
    if len(answer) > len(str(maximum)):
        return None
    if len(answer) > 1 and answer[0] == "0":
        return None
    index = int(answer)
    if 1 <= index <= maximum:
        return index
    return None


def _terminate_on_eof(output: TextIO) -> None:
    output.write("\n")
    output.flush()
    raise EOFError(_MENU_EOF_MESSAGE)


def _read_numbered_answer(
    *,
    output: TextIO,
    input_stream: TextIO,
    input_fn: Callable[[str], str] | None,
    prompt: str,
) -> str:
    output.write(prompt)
    output.flush()
    if input_fn is not None:
        try:
            return input_fn(prompt)
        except EOFError:
            _terminate_on_eof(output)
    line = input_stream.readline()
    if line == "":
        _terminate_on_eof(output)
    return line.rstrip("\r\n")


def _choose_numbered(
    choices: Sequence[MenuChoice],
    *,
    output: TextIO,
    input_stream: TextIO,
    input_fn: Callable[[str], str] | None,
) -> str:
    output.write(_format_numbered_list(choices))
    output.flush()
    prompt = "Enter the number of your choice: "
    hint = _NUMBERED_INVALID_HINT.format(count=len(choices))
    while True:
        answer = _read_numbered_answer(
            output=output,
            input_stream=input_stream,
            input_fn=input_fn,
            prompt=prompt,
        )
        index = _parse_canonical_choice_number(answer, len(choices))
        if index is None:
            output.write(hint + "\n")
            output.flush()
            continue
        return choices[index - 1].value


def _redraw_interactive_row(
    output: TextIO,
    row: str,
    *,
    previous_width: int,
) -> int:
    row_width = _terminal_display_width(row)
    padding = max(0, previous_width - row_width)
    output.write("\r" + row + (" " * padding))
    output.flush()
    return row_width + padding


def _finalize_interactive_row(
    output: TextIO,
    row: str,
    *,
    previous_width: int,
) -> None:
    row_width = _terminal_display_width(row)
    padding = max(0, previous_width - row_width)
    output.write("\r" + row + (" " * padding) + "\n")
    output.flush()


def _choose_interactive(
    choices: Sequence[MenuChoice],
    *,
    output: TextIO,
    read_key: Callable[[], KeyEvent],
) -> str:
    index = 0
    row_width = 0
    while True:
        row = format_menu_row(choices[index], selected=True)
        row_width = _redraw_interactive_row(output, row, previous_width=row_width)
        event = read_key()
        if event is KeyEvent.UP:
            index = (index - 1) % len(choices)
        elif event is KeyEvent.DOWN:
            index = (index + 1) % len(choices)
        elif event is KeyEvent.UNKNOWN:
            continue
        elif event is KeyEvent.ENTER:
            final = format_menu_row(choices[index], selected=True)
            _finalize_interactive_row(output, final, previous_width=row_width)
            return choices[index].value
        elif event is KeyEvent.INTERRUPT:
            output.write("\n")
            output.flush()
            raise KeyboardInterrupt
        elif event is KeyEvent.EOF:
            _terminate_on_eof(output)


def _posix_read_byte_blocking(fd: int) -> str | None:
    chunk = os.read(fd, 1)
    if not chunk:
        return None
    return chunk.decode("utf-8", errors="replace")


def _posix_read_byte_timed(fd: int, *, timeout_seconds: float) -> object:
    import select

    ready, _, _ = select.select([fd], [], [], timeout_seconds)
    if not ready:
        return _TIMED_OUT
    chunk = os.read(fd, 1)
    if not chunk:
        return None
    return chunk.decode("utf-8", errors="replace")


def _continuation_marker(result: object) -> str:
    if result is _TIMED_OUT:
        return "timeout"
    if result is None:
        return "eof"
    return result


def _read_posix_menu_key(
    fd: int,
    *,
    read_blocking: Callable[[int], str | None] | None = None,
    read_timed: Callable[[int, float], object] | None = None,
) -> KeyEvent:
    """Read one POSIX menu key; first byte blocks, escape continuations time out."""
    blocking = read_blocking or _posix_read_byte_blocking
    timed = read_timed or (
        lambda descriptor, timeout: _posix_read_byte_timed(
            descriptor,
            timeout_seconds=timeout,
        )
    )
    first = blocking(fd)
    if first is None:
        return _resolve_posix_menu_key(None)
    if first != "\x1b":
        return _resolve_posix_menu_key(first)
    second = timed(fd, _POSIX_ESCAPE_TIMEOUT_SECONDS)
    if not isinstance(second, str):
        return _resolve_posix_menu_key("\x1b", second=_continuation_marker(second))
    if second != "[":
        return _resolve_posix_menu_key("\x1b", second=second)
    third = timed(fd, _POSIX_ESCAPE_TIMEOUT_SECONDS)
    if not isinstance(third, str):
        return _resolve_posix_menu_key("\x1b", second="[", third=_continuation_marker(third))
    return _resolve_posix_menu_key("\x1b", second="[", third=third)


def read_interactive_key() -> KeyEvent:
    """Read one navigation key using the native console input for this OS."""
    if os.name == "nt":
        import msvcrt

        char = msvcrt.getwch()
        if char == "":
            return KeyEvent.EOF
        if char == "\x03":
            return KeyEvent.INTERRUPT
        if char in {"\r", "\n"}:
            return KeyEvent.ENTER
        if char in {"\x00", "\xe0"}:
            second = msvcrt.getwch()
            if second == "":
                return KeyEvent.EOF
            return _decode_windows_scan_code(ord(second))
        return KeyEvent.UNKNOWN

    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return _read_posix_menu_key(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def choose_menu_item(
    choices: Sequence[MenuChoice],
    *,
    output: TextIO | None = None,
    input_stream: TextIO | None = None,
    is_interactive: Callable[[], bool] | None = None,
    input_fn: Callable[[str], str] | None = None,
    read_key: Callable[[], KeyEvent] | None = None,
) -> str:
    """Prompt for one menu choice and return its stable value."""
    _validate_choices(choices)
    resolved_output = sys.stdout if output is None else output
    resolved_input = sys.stdin if input_stream is None else input_stream
    interactive = (
        _default_is_interactive(resolved_output, resolved_input)
        if is_interactive is None
        else is_interactive()
    )
    if interactive:
        reader = read_key or read_interactive_key
        return _choose_interactive(choices, output=resolved_output, read_key=reader)

    return _choose_numbered(
        choices,
        output=resolved_output,
        input_stream=resolved_input,
        input_fn=input_fn,
    )