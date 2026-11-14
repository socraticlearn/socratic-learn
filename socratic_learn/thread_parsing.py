"""Strict session record parsing."""

from __future__ import annotations

import re

from .manifest import SESSION_HEADINGS
from .thread_schema import (
    ParsedSessionRecord,
    integrity,
    validate_calendar_date,
)

_EM_DASH = " — "
_SESSION_HEADING_RE = re.compile(
    r"^# Session: (\d{4}-\d{2}-\d{2})" + re.escape(_EM_DASH) + r"(.+)\s*$"
)


def _subject_focus_title(topic: str) -> str:
    cleaned = topic.strip()
    if ": " not in cleaned:
        raise integrity("session record title must use '<Subject>: <Specific focus>'")
    subject, focus = cleaned.split(": ", 1)
    if not subject.strip() or not focus.strip():
        raise integrity("session record title must use '<Subject>: <Specific focus>'")
    return cleaned


def _ordered_section_map(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines:
        raise integrity("session record is empty")

    heading_match = _SESSION_HEADING_RE.match(lines[0])
    if heading_match is None:
        raise integrity("session record heading is not valid")

    session_date = validate_calendar_date(heading_match.group(1))
    if session_date != heading_match.group(1):
        raise integrity("session record heading is not valid")

    sections: dict[str, list[str]] = {}
    expected_index = 0
    current: str | None = None

    for line in lines[1:]:
        if line.startswith("## "):
            if line in sections:
                raise integrity("session record has duplicate headings")
            if line not in SESSION_HEADINGS:
                raise integrity("session record has an unexpected heading")
            if expected_index >= len(SESSION_HEADINGS):
                raise integrity("session record has an unexpected heading")
            if line != SESSION_HEADINGS[expected_index]:
                raise integrity("session record headings are out of order")
            if line in sections:
                raise integrity("session record has duplicate headings")
            current = line
            sections[current] = []
            expected_index += 1
            continue
        if current is not None:
            sections[current].append(line)

    if expected_index != len(SESSION_HEADINGS):
        raise integrity("session record is missing required headings")

    result: dict[str, str] = {}
    for heading in SESSION_HEADINGS:
        body = "\n".join(sections[heading]).strip()
        if not body:
            raise integrity(f"session record section {heading} must not be empty")
        result[heading] = body
    return result


def parse_session_heading(text: str) -> tuple[str, str]:
    first_line = text.splitlines()[0] if text else ""
    match = _SESSION_HEADING_RE.match(first_line)
    if match is None:
        raise integrity("session record heading is not valid")
    session_date = validate_calendar_date(match.group(1))
    return session_date, match.group(2).strip()


def parse_session_record(text: str) -> ParsedSessionRecord:
    """Parse a validated session record heading and next-step section."""

    session_date, topic = parse_session_heading(text)
    sections = _ordered_section_map(text)
    refined_title = _subject_focus_title(topic)
    return ParsedSessionRecord(
        session_date=session_date,
        refined_title=refined_title,
        next_step=sections["## Next step"],
    )


def validate_legacy_session_content(text: str) -> tuple[str, str, str | None]:
    """Validate one legacy session before import."""

    _session_date, display_title = parse_session_heading(text)
    sections = _ordered_section_map(text)
    refined_title: str | None = None
    if ": " in display_title:
        refined_title = _subject_focus_title(display_title)
    return display_title, sections["## Next step"], refined_title