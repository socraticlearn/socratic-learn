"""Local thread metadata and immutable encounter storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import re
import secrets
import stat
import uuid
from pathlib import Path

from .errors import IntegrityError
from .manifest import SESSION_HEADINGS

THREAD_SCHEMA_VERSION = 1
_THREAD_FILENAME = "thread.json"
_SESSIONS_DIRNAME = "sessions"
_LEGACY_SESSIONS_DIRNAME = "sessions"
_EM_DASH = " — "
_SESSION_HEADING_RE = re.compile(
    r"^# Session: (\d{4}-\d{2}-\d{2})" + re.escape(_EM_DASH) + r"(.+)\s*$"
)
_THREAD_KEYS = frozenset(
    {
        "schema_version",
        "id",
        "provisional_title",
        "refined_title",
        "original_goal",
        "status",
        "created_at",
        "updated_at",
        "latest_encounter",
        "next_step",
    }
)
_VALID_STATUSES = frozenset({"active", "incomplete", "complete"})
_REMEDIATION = "inspect the thread storage path and retry"


@dataclass(frozen=True)
class ThreadRecord:
    id: str
    provisional_title: str
    refined_title: str | None
    original_goal: str
    status: str
    created_at: str
    updated_at: str
    latest_encounter: str | None
    next_step: str


@dataclass(frozen=True)
class ThreadSummary:
    id: str
    display_title: str
    updated_at: str
    next_step: str
    status: str


@dataclass(frozen=True)
class LegacySessionSummary:
    path: Path
    display_title: str
    updated_at: str
    next_step: str


@dataclass(frozen=True)
class ParsedSessionRecord:
    session_date: str
    refined_title: str
    next_step: str


def threads_root(records: Path) -> Path:
    """Return the thread storage root for an active records directory."""

    return records / "threads"


def _integrity(message: str, *, path: Path | None = None) -> IntegrityError:
    return IntegrityError(message, path=path, remediation=_REMEDIATION)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reject_symlink(path: Path, description: str) -> None:
    if path.is_symlink():
        raise _integrity(f"{description} must not be a symbolic link", path=path)


def _require_directory(path: Path, description: str) -> Path:
    _reject_symlink(path, description)
    if not path.is_dir():
        raise _integrity(f"{description} is not a directory", path=path)
    return path.resolve()


def _require_regular_file(path: Path, description: str) -> None:
    _reject_symlink(path, description)
    try:
        status = path.stat(follow_symlinks=False)
    except OSError as error:
        raise _integrity(f"{description} could not be inspected", path=path) from error
    if not stat.S_ISREG(status.st_mode):
        raise _integrity(f"{description} must be a regular file", path=path)


def ensure_threads_root(records: Path) -> Path:
    """Prepare the thread storage root when it is missing or already valid."""

    root = threads_root(records)
    if root.exists() or root.is_symlink():
        return _require_directory(root, "thread records")
    try:
        root.mkdir(parents=True)
    except OSError as error:
        raise _integrity("thread records directory could not be created", path=root) from error
    return _require_directory(root, "thread records")


def _thread_dir(records: Path, thread_id: str) -> Path:
    if not thread_id or "/" in thread_id or "\\" in thread_id:
        raise _integrity("thread identifier is not valid")
    return threads_root(records) / thread_id


def _validated_status(value: object, path: Path) -> str:
    if type(value) is not str or value not in _VALID_STATUSES:
        raise _integrity("thread metadata has an invalid status", path=path)
    return value


def _validated_timestamp(value: object, path: Path, field: str) -> str:
    if type(value) is not str or not value:
        raise _integrity(f"thread metadata has an invalid {field}", path=path)
    return value


def _validated_title(value: object, path: Path, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise _integrity(f"thread metadata has an invalid {field}", path=path)
    return value.strip()


def _validated_optional_title(value: object, path: Path, field: str) -> str | None:
    if value is None:
        return None
    return _validated_title(value, path, field)


def _validated_optional_encounter(value: object, path: Path) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value.strip():
        raise _integrity("thread metadata has an invalid latest encounter", path=path)
    return value.strip()


def _document_to_record(document: dict[str, object], path: Path) -> ThreadRecord:
    if set(document) != _THREAD_KEYS:
        raise _integrity("thread metadata has unexpected fields", path=path)
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != THREAD_SCHEMA_VERSION
    ):
        raise _integrity("thread metadata schema is not supported", path=path)
    thread_id = _validated_title(document["id"], path, "id")
    return ThreadRecord(
        id=thread_id,
        provisional_title=_validated_title(document["provisional_title"], path, "provisional_title"),
        refined_title=_validated_optional_title(document["refined_title"], path, "refined_title"),
        original_goal=_validated_title(document["original_goal"], path, "original_goal"),
        status=_validated_status(document["status"], path),
        created_at=_validated_timestamp(document["created_at"], path, "created_at"),
        updated_at=_validated_timestamp(document["updated_at"], path, "updated_at"),
        latest_encounter=_validated_optional_encounter(document["latest_encounter"], path),
        next_step=document["next_step"] if type(document["next_step"]) is str else "",
    )


def _record_to_document(record: ThreadRecord) -> dict[str, object]:
    return {
        "schema_version": THREAD_SCHEMA_VERSION,
        "id": record.id,
        "provisional_title": record.provisional_title,
        "refined_title": record.refined_title,
        "original_goal": record.original_goal,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "latest_encounter": record.latest_encounter,
        "next_step": record.next_step,
    }


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    parent = path.parent
    _reject_symlink(parent, "thread metadata parent")
    _reject_symlink(path, "thread metadata destination")
    serialized = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    temporary: Path | None = None
    descriptor: int | None = None
    try:
        while temporary is None:
            candidate = parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
            try:
                descriptor = os.open(candidate, flags, 0o600)
            except FileExistsError:
                continue
            temporary = candidate

        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _save_thread_metadata(metadata_path: Path, record: ThreadRecord) -> None:
    _atomic_write_json(metadata_path, _record_to_document(record))


def load_thread(records: Path, thread_id: str) -> ThreadRecord:
    """Load one thread record from disk."""

    metadata_path = _thread_dir(records, thread_id) / _THREAD_FILENAME
    _require_regular_file(metadata_path, "thread metadata")
    try:
        document = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _integrity("thread metadata is not valid JSON", path=metadata_path) from error
    if type(document) is not dict:
        raise _integrity("thread metadata must contain an object", path=metadata_path)
    return _document_to_record(document, metadata_path)


def create_thread(
    records: Path,
    *,
    goal: str,
    provisional_title: str | None = None,
) -> ThreadRecord:
    """Create a new thread directory with exclusive metadata."""

    cleaned_goal = goal.strip()
    if not cleaned_goal:
        raise _integrity("thread goal must not be empty")

    ensure_threads_root(records)
    title = cleaned_goal if provisional_title is None else provisional_title.strip()
    if not title:
        raise _integrity("thread provisional title must not be empty")

    thread_id = str(uuid.uuid4())
    thread_dir = _thread_dir(records, thread_id)
    sessions_dir = thread_dir / _SESSIONS_DIRNAME
    if thread_dir.exists():
        raise _integrity("thread directory already exists", path=thread_dir)
    try:
        sessions_dir.mkdir(parents=True)
    except OSError as error:
        raise _integrity("thread directory could not be created", path=thread_dir) from error

    timestamp = _utc_now()
    record = ThreadRecord(
        id=thread_id,
        provisional_title=title,
        refined_title=None,
        original_goal=cleaned_goal,
        status="active",
        created_at=timestamp,
        updated_at=timestamp,
        latest_encounter=None,
        next_step="",
    )
    metadata_path = thread_dir / _THREAD_FILENAME
    try:
        descriptor = os.open(metadata_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise _integrity("thread metadata already exists", path=metadata_path) from error
    except OSError as error:
        raise _integrity("thread metadata could not be created", path=metadata_path) from error

    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(_record_to_document(record), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())
    return record


def _encounter_filename() -> str:
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{date}-{secrets.token_hex(4)}.md"


def create_pending_encounter(
    records: Path,
    thread_id: str,
    *,
    path: Path | None = None,
) -> Path:
    """Create one exclusive, initially empty encounter record."""

    thread_dir = _thread_dir(records, thread_id)
    _require_directory(thread_dir, "thread directory")
    sessions_dir = _require_directory(thread_dir / _SESSIONS_DIRNAME, "encounter records")

    destination = path if path is not None else sessions_dir / _encounter_filename()
    try:
        destination.relative_to(sessions_dir)
    except ValueError as error:
        raise _integrity("encounter path must stay under the thread sessions directory", path=destination) from error

    if destination.exists() or destination.is_symlink():
        raise _integrity("encounter record already exists", path=destination)

    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise _integrity("encounter record already exists", path=destination) from error
    except OSError as error:
        raise _integrity("encounter record could not be created", path=destination) from error

    with os.fdopen(descriptor, "wb") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    return destination


def _section_map(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines:
        raise _integrity("session record is empty")

    heading_match = _SESSION_HEADING_RE.match(lines[0])
    if heading_match is None:
        raise _integrity("session record heading is not valid")

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[1:]:
        if line in SESSION_HEADINGS:
            current = line
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)

    for heading in SESSION_HEADINGS:
        if heading not in sections:
            raise _integrity(f"session record is missing required heading {heading!r}")
        body = "\n".join(sections[heading]).strip()
        if not body:
            raise _integrity(f"session record section {heading} must not be empty")

    return {heading: "\n".join(sections[heading]).strip() for heading in SESSION_HEADINGS}


def _subject_focus_title(topic: str) -> str:
    cleaned = topic.strip()
    if ": " not in cleaned:
        raise _integrity("session record title must use '<Subject>: <Specific focus>'")
    subject, focus = cleaned.split(": ", 1)
    if not subject.strip() or not focus.strip():
        raise _integrity("session record title must use '<Subject>: <Specific focus>'")
    return cleaned


def parse_session_record(text: str) -> ParsedSessionRecord:
    """Parse a validated session record heading and next-step section."""

    heading_match = _SESSION_HEADING_RE.match(text.splitlines()[0] if text else "")
    if heading_match is None:
        raise _integrity("session record heading is not valid")
    sections = _section_map(text)
    refined_title = _subject_focus_title(heading_match.group(2))
    return ParsedSessionRecord(
        session_date=heading_match.group(1),
        refined_title=refined_title,
        next_step=sections["## Next step"],
    )


def _heading_display_title(text: str) -> tuple[str, str | None]:
    first_line = text.splitlines()[0] if text else ""
    match = _SESSION_HEADING_RE.match(first_line)
    if match is None:
        raise _integrity("session record heading is not valid")
    return match.group(1), match.group(2).strip()


def _optional_next_step(text: str) -> str:
    try:
        return _section_map(text)["## Next step"]
    except IntegrityError:
        return ""


def finalize_thread_encounter(
    records: Path,
    thread_id: str,
    encounter_path: Path,
) -> ThreadRecord:
    """Validate one encounter record and atomically refresh thread metadata."""

    thread_dir = _thread_dir(records, thread_id)
    sessions_dir = _require_directory(thread_dir / _SESSIONS_DIRNAME, "encounter records")
    try:
        encounter_path.relative_to(sessions_dir)
    except ValueError as error:
        raise _integrity("encounter path must stay under the thread sessions directory", path=encounter_path) from error
    _require_regular_file(encounter_path, "encounter record")

    try:
        parsed = parse_session_record(encounter_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise _integrity("encounter record is not valid UTF-8", path=encounter_path) from error

    current = load_thread(records, thread_id)
    updated = ThreadRecord(
        id=current.id,
        provisional_title=current.provisional_title,
        refined_title=parsed.refined_title,
        original_goal=current.original_goal,
        status="complete",
        created_at=current.created_at,
        updated_at=_utc_now(),
        latest_encounter=encounter_path.name,
        next_step=parsed.next_step,
    )
    metadata_path = thread_dir / _THREAD_FILENAME
    _save_thread_metadata(metadata_path, updated)
    return updated


def mark_thread_incomplete(records: Path, thread_id: str) -> ThreadRecord:
    """Mark one thread as incomplete without refining its title."""

    current = load_thread(records, thread_id)
    updated = ThreadRecord(
        id=current.id,
        provisional_title=current.provisional_title,
        refined_title=current.refined_title,
        original_goal=current.original_goal,
        status="incomplete",
        created_at=current.created_at,
        updated_at=_utc_now(),
        latest_encounter=current.latest_encounter,
        next_step=current.next_step,
    )
    metadata_path = _thread_dir(records, thread_id) / _THREAD_FILENAME
    _save_thread_metadata(metadata_path, updated)
    return updated


def list_threads(records: Path) -> tuple[ThreadSummary, ...]:
    """List thread summaries sorted by latest activity."""

    root = threads_root(records)
    if not root.is_dir() or root.is_symlink():
        return ()

    summaries: list[ThreadSummary] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir() or entry.is_symlink():
            continue
        metadata_path = entry / _THREAD_FILENAME
        if not metadata_path.is_file() or metadata_path.is_symlink():
            continue
        record = load_thread(records, entry.name)
        display_title = record.refined_title or record.provisional_title
        summaries.append(
            ThreadSummary(
                id=record.id,
                display_title=display_title,
                updated_at=record.updated_at,
                next_step=record.next_step,
                status=record.status,
            )
        )

    summaries.sort(key=lambda item: item.id)
    summaries.sort(key=lambda item: item.updated_at, reverse=True)
    return tuple(summaries)


def discover_legacy_sessions(records: Path) -> tuple[LegacySessionSummary, ...]:
    """Discover legacy flat session records without modifying them."""

    legacy_dir = records / _LEGACY_SESSIONS_DIRNAME
    if not legacy_dir.is_dir() or legacy_dir.is_symlink():
        return ()

    discovered: list[LegacySessionSummary] = []
    for entry in sorted(legacy_dir.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            continue
        try:
            status = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISREG(status.st_mode):
            continue
        try:
            text = entry.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            _, display_title = _heading_display_title(text)
        except IntegrityError:
            continue
        discovered.append(
            LegacySessionSummary(
                path=entry,
                display_title=display_title,
                updated_at=datetime.fromtimestamp(status.st_mtime, tz=UTC)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                next_step=_optional_next_step(text),
            )
        )

    discovered.sort(key=lambda item: item.path.name)
    discovered.sort(key=lambda item: item.updated_at, reverse=True)
    return tuple(discovered)


def _copy_encounter_exclusive(source: Path, destination: Path) -> None:
    _require_regular_file(source, "legacy session record")
    if destination.exists() or destination.is_symlink():
        raise _integrity("encounter record already exists", path=destination)
    payload = source.read_bytes()
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise _integrity("encounter record already exists", path=destination) from error
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def import_legacy_session(records: Path, legacy_path: Path) -> ThreadRecord:
    """Import one legacy session into a new thread while keeping the original."""

    legacy_root = (records / _LEGACY_SESSIONS_DIRNAME).resolve()
    resolved = legacy_path.resolve()
    try:
        resolved.relative_to(legacy_root)
    except ValueError as error:
        raise _integrity("legacy session must stay under the sessions directory", path=legacy_path) from error
    _require_regular_file(resolved, "legacy session record")

    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise _integrity("legacy session is not valid UTF-8", path=resolved) from error
    _, display_title = _heading_display_title(text)
    next_step = _optional_next_step(text)

    record = create_thread(records, goal=display_title, provisional_title=display_title)
    thread_dir = _thread_dir(records, record.id)
    encounter_name = resolved.name
    destination = thread_dir / _SESSIONS_DIRNAME / encounter_name
    _copy_encounter_exclusive(resolved, destination)

    refined_title: str | None
    try:
        refined_title = parse_session_record(text).refined_title
        status = "complete"
    except IntegrityError:
        refined_title = None
        status = "incomplete"

    imported = ThreadRecord(
        id=record.id,
        provisional_title=record.provisional_title,
        refined_title=refined_title,
        original_goal=record.original_goal,
        status=status,
        created_at=record.created_at,
        updated_at=_utc_now(),
        latest_encounter=encounter_name,
        next_step=next_step,
    )
    metadata_path = thread_dir / _THREAD_FILENAME
    _save_thread_metadata(metadata_path, imported)
    return imported