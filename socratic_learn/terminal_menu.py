"""Dependency-free interactive and numbered terminal menus."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
import os
import sys
from typing import TextIO

from .errors import UsageError

__all__ = (
    "KeyEvent",
    "MenuChoice",
    "choose_menu_item",
    "format_menu_row",
    "read_interactive_key",
)


class KeyEvent(Enum):
    """Normalized navigation keys for interactive menus."""

    UP = "up"
    DOWN = "down"
    ENTER = "enter"
    INTERRUPT = "interrupt"


@dataclass(frozen=True, slots=True)
class MenuChoice:
    """One selectable menu entry with a stable value and human-readable text."""

    value: str
    label: str
    detail: str | None = None


def format_menu_row(choice: MenuChoice, *, selected: bool = False) -> str:
    """Return a compact single-line row suitable for thread picker output."""
    marker = ">" if selected else " "
    if choice.detail:
        return f"{marker} {choice.label} — {choice.detail}"
    return f"{marker} {choice.label}"


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


def _choose_numbered(
    choices: Sequence[MenuChoice],
    *,
    output: TextIO,
    input_fn: Callable[[str], str],
) -> str:
    output.write(_format_numbered_list(choices))
    output.flush()
    prompt = "Enter the number of your choice: "
    while True:
        answer = input_fn(prompt)
        try:
            index = int(answer)
        except ValueError:
            continue
        if 1 <= index <= len(choices) and answer.strip() == str(index):
            return choices[index - 1].value


def _redraw_interactive_row(output: TextIO, row: str) -> None:
    output.write("\r" + row)
    output.flush()


def _choose_interactive(
    choices: Sequence[MenuChoice],
    *,
    output: TextIO,
    read_key: Callable[[], KeyEvent],
) -> str:
    index = 0
    while True:
        row = format_menu_row(choices[index], selected=True)
        _redraw_interactive_row(output, row)
        event = read_key()
        if event is KeyEvent.UP:
            index = (index - 1) % len(choices)
        elif event is KeyEvent.DOWN:
            index = (index + 1) % len(choices)
        elif event is KeyEvent.ENTER:
            final = format_menu_row(choices[index], selected=True)
            output.write("\r" + final + "\n")
            output.flush()
            return choices[index].value
        elif event is KeyEvent.INTERRUPT:
            output.write("\n")
            output.flush()
            raise KeyboardInterrupt


def read_interactive_key() -> KeyEvent:
    """Read one navigation key using the native console input for this OS."""
    if os.name == "nt":
        import msvcrt

        char = msvcrt.getwch()
        if char == "\x03":
            return KeyEvent.INTERRUPT
        if char in {"\r", "\n"}:
            return KeyEvent.ENTER
        if char in {"\x00", "\xe0"}:
            code = ord(msvcrt.getwch())
            if code == 72:
                return KeyEvent.UP
            if code == 80:
                return KeyEvent.DOWN
        return KeyEvent.DOWN

    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        first = sys.stdin.read(1)
        if first == "\x03":
            return KeyEvent.INTERRUPT
        if first in {"\r", "\n"}:
            return KeyEvent.ENTER
        if first == "\x1b":
            second = sys.stdin.read(1)
            third = sys.stdin.read(1) if second == "[" else ""
            if second == "[" and third == "A":
                return KeyEvent.UP
            if second == "[" and third == "B":
                return KeyEvent.DOWN
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    return KeyEvent.DOWN


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

    prompt_input = input if input_fn is None else input_fn
    return _choose_numbered(choices, output=resolved_output, input_fn=prompt_input)