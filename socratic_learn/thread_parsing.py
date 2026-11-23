"""Strict session record parsing."""

from __future__ import annotations

import re

from .manifest import (
    SESSION_HEADINGS,
    SESSION_RECORD_CANONICAL_SEPARATOR,
    SESSION_RECORD_LEGACY_SEPARATOR,
    SESSION_RECORD_VERSION,
    SESSION_RECORD_VERSION_MARKER,
)
from .thread_schema import (
    ParsedSessionRecord,
    integrity,
    validate_calendar_date,
)

_SESSION_HEADING_RE = re.compile(
    r"^# Session: (\d{4}-\d{2}-\d{2})(?:"
    + re.escape(SESSION_RECORD_CANONICAL_SEPARATOR)
    + "|"
    + re.escape(SESSION_RECORD_LEGACY_SEPARATOR)
    + r")(.+)\s*$"
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_RECORD_VERSION_SUBSTRING = "socratic-learn-record-version"
_POSITIVE_ID_RE = r"[1-9][0-9]*"
_E_BULLET_RE = re.compile(rf"^- \[E({_POSITIVE_ID_RE})\] .+")
_P_BULLET_RE = re.compile(rf"^- \[P({_POSITIVE_ID_RE})\] .+")
_G_BULLET_RE = re.compile(rf"^- \[G({_POSITIVE_ID_RE})\] .+")
_APP_BULLET_RE = re.compile(
    rf"^- \[(E|P)({_POSITIVE_ID_RE})\] "
    r"Task: (.+) \| Conditions: (.+) \| Result: (.+) \| Support: (.+) \| Limits: (.+)$"
)
_CONCEPTS_NONE = "- None."
_APP_NONE = "- None collected."
_GAPS_NONE = "- None."


def _subject_focus_title(topic: str) -> str:
    cleaned = topic.strip()
    if cleaned == "Subject: Specific focus":
        raise integrity("session record title must replace the literal placeholder")
    if ": " not in cleaned:
        raise integrity("session record title must use '<Subject>: <Specific focus>'")
    subject, focus = cleaned.split(": ", 1)
    if not subject.strip() or not focus.strip():
        raise integrity("session record title must use '<Subject>: <Specific focus>'")
    return cleaned


def _version_comments(text: str) -> list[str]:
    return [
        comment
        for comment in _HTML_COMMENT_RE.findall(text)
        if _RECORD_VERSION_SUBSTRING in comment
    ]


def _detect_record_version(text: str, preamble: str) -> int | None:
    version_comments = _version_comments(text)
    if not version_comments:
        return None
    if text.count(SESSION_RECORD_VERSION_MARKER) != 1:
        raise integrity("session record version marker is malformed")
    if SESSION_RECORD_VERSION_MARKER not in preamble:
        raise integrity("session record version marker must appear in the preamble")
    for comment in version_comments:
        if comment != SESSION_RECORD_VERSION_MARKER:
            raise integrity("session record has unsupported record version")
    return SESSION_RECORD_VERSION


def _reject_sentinel_mixed(
    lines: list[str],
    sentinel: str,
    label: str,
) -> None:
    if sentinel in lines and len(lines) > 1:
        raise integrity(f"session record {label} mixes sentinel with structured bullets")


def _nonblank_lines(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if line.strip()]


def _unique_identifiers(lines: list[str], pattern: re.Pattern[str], label: str) -> set[str]:
    seen: set[str] = set()
    for line in lines:
        match = pattern.match(line)
        if match is None:
            raise integrity(f"session record {label} bullet is malformed")
        identifier = match.group(1)
        if identifier in seen:
            raise integrity(f"session record {label} has duplicate identifiers")
        seen.add(identifier)
    return seen


def _validate_v2_sections(sections: dict[str, str]) -> None:
    concepts_lines = _nonblank_lines(sections["## Concepts established"])
    _reject_sentinel_mixed(concepts_lines, _CONCEPTS_NONE, "concepts established")
    if len(concepts_lines) == 1 and concepts_lines[0] == _CONCEPTS_NONE:
        concept_ids: set[str] = set()
    else:
        concept_ids = set()
        for line in concepts_lines:
            if _P_BULLET_RE.match(line) is not None:
                raise integrity(
                    "session record progress identifiers are only allowed in application evidence"
                )
            match = _E_BULLET_RE.match(line)
            if match is None:
                raise integrity("session record concepts established bullet is malformed")
            identifier = match.group(1)
            if identifier in concept_ids:
                raise integrity("session record concepts established has duplicate identifiers")
            concept_ids.add(identifier)

    app_lines = _nonblank_lines(sections["## Application evidence"])
    _reject_sentinel_mixed(app_lines, _APP_NONE, "application evidence")
    if len(app_lines) == 1 and app_lines[0] == _APP_NONE:
        app_evidence_ids: set[str] = set()
        app_progress_ids: set[str] = set()
    else:
        app_evidence_ids = set()
        app_progress_ids = set()
        for line in app_lines:
            match = _APP_BULLET_RE.match(line)
            if match is None:
                raise integrity("session record application evidence bullet is malformed")
            kind, identifier, task, conditions, result, support, limits = match.groups()
            for field_name, value in (
                ("Task", task),
                ("Conditions", conditions),
                ("Result", result),
                ("Support", support),
                ("Limits", limits),
            ):
                if not value.strip():
                    raise integrity(
                        f"session record application evidence {field_name} must not be empty"
                    )
            target = app_evidence_ids if kind == "E" else app_progress_ids
            label = "application evidence" if kind == "E" else "application progress"
            if identifier in target:
                raise integrity(f"session record {label} has duplicate identifiers")
            target.add(identifier)

    if concept_ids != app_evidence_ids:
        raise integrity("session record evidence identifiers do not match between sections")

    gap_lines = _nonblank_lines(sections["## Remaining gaps"])
    _reject_sentinel_mixed(gap_lines, _GAPS_NONE, "remaining gaps")
    if len(gap_lines) == 1 and gap_lines[0] == _GAPS_NONE:
        gap_ids: set[str] = set()
    else:
        gap_ids = _unique_identifiers(gap_lines, _G_BULLET_RE, "remaining gaps")

    next_lines = _nonblank_lines(sections["## Next step"])
    _reject_sentinel_mixed(next_lines, _GAPS_NONE, "next step")
    if not gap_ids:
        if gap_lines != [_GAPS_NONE] or next_lines != [_GAPS_NONE]:
            raise integrity("session record gap sections must use - None. when no gaps remain")
        return

    next_ids = _unique_identifiers(next_lines, _G_BULLET_RE, "next step")
    if not next_ids:
        raise integrity("session record next step must reference at least one gap identifier")
    if not next_ids.issubset(gap_ids):
        raise integrity("session record next step references a stale gap identifier")


def _ordered_section_map(text: str) -> tuple[dict[str, str], int | None]:
    lines = text.splitlines()
    if not lines:
        raise integrity("session record is empty")

    heading_match = _SESSION_HEADING_RE.match(lines[0])
    if heading_match is None:
        raise integrity("session record heading is not valid")

    session_date = validate_calendar_date(heading_match.group(1))
    if session_date != heading_match.group(1):
        raise integrity("session record heading is not valid")

    index = 1
    while index < len(lines) and not lines[index].startswith("## "):
        index += 1
    if index >= len(lines):
        raise integrity("session record is missing required headings")
    preamble_text = "\n".join(lines[1:index])
    record_version = _detect_record_version(text, preamble_text)
    preamble = _HTML_COMMENT_RE.sub("", preamble_text).strip()
    if preamble:
        raise integrity("session record has unexpected content before required headings")

    sections: dict[str, list[str]] = {}
    expected_index = 0
    current: str | None = None

    for line in lines[index:]:
        if line.startswith("## "):
            if line in sections:
                raise integrity("session record has duplicate headings")
            if line not in SESSION_HEADINGS:
                raise integrity("session record has an unexpected heading")
            if expected_index >= len(SESSION_HEADINGS):
                raise integrity("session record has an unexpected heading")
            if line != SESSION_HEADINGS[expected_index]:
                raise integrity("session record headings are out of order")
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
        body = _HTML_COMMENT_RE.sub("", "\n".join(sections[heading])).strip()
        if not body:
            raise integrity(f"session record section {heading} must not be empty")
        result[heading] = body
    return result, record_version


def parse_session_heading(text: str) -> tuple[str, str]:
    first_line = text.splitlines()[0] if text else ""
    match = _SESSION_HEADING_RE.match(first_line)
    if match is None:
        raise integrity("session record heading is not valid")
    session_date = validate_calendar_date(match.group(1))
    display_title = match.group(2).strip()
    if not display_title:
        raise integrity("session record heading is not valid")
    return session_date, display_title


def parse_session_record(
    text: str,
    *,
    require_current_record: bool = False,
) -> ParsedSessionRecord:
    """Parse a validated session record heading and next-step section."""

    session_date, topic = parse_session_heading(text)
    sections, record_version = _ordered_section_map(text)
    if require_current_record and record_version != SESSION_RECORD_VERSION:
        raise integrity(
            "session record version marker is required for new checkpoints"
        )
    if record_version == SESSION_RECORD_VERSION:
        _validate_v2_sections(sections)
    refined_title = _subject_focus_title(topic)
    return ParsedSessionRecord(
        session_date=session_date,
        refined_title=refined_title,
        next_step=sections["## Next step"],
    )


def validate_legacy_session_content(text: str) -> tuple[str, str, str | None]:
    """Validate one legacy session before import."""

    _session_date, display_title = parse_session_heading(text)
    sections, record_version = _ordered_section_map(text)
    if record_version == SESSION_RECORD_VERSION:
        _validate_v2_sections(sections)
    refined_title: str | None = None
    if ": " in display_title:
        refined_title = _subject_focus_title(display_title)
    return display_title, sections["## Next step"], refined_title
