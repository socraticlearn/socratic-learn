"""Thread metadata schema validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
import re
from pathlib import Path
import uuid

from .errors import IntegrityError

THREAD_SCHEMA_VERSION = 1
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
_PENDING_PREFIX = "pending-"
_CANONICAL_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
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


def integrity(message: str, *, path: Path | None = None) -> IntegrityError:
    return IntegrityError(message, path=path, remediation=_REMEDIATION)


def utc_now_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_utc_timestamp(value: object, *, path: Path | None = None, field: str = "timestamp") -> datetime:
    if type(value) is not str or not value:
        raise integrity(f"thread metadata has an invalid {field}", path=path)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise integrity(f"thread metadata has an invalid {field}", path=path) from error
    if parsed.tzinfo is None:
        raise integrity(f"thread metadata has an invalid {field}", path=path)
    return parsed.astimezone(UTC)


def format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validate_canonical_thread_id(thread_id: str, *, path: Path | None = None) -> str:
    if type(thread_id) is not str or not _CANONICAL_UUID_RE.fullmatch(thread_id):
        raise integrity("thread identifier is not valid", path=path)
    try:
        parsed = uuid.UUID(thread_id)
    except ValueError as error:
        raise integrity("thread identifier is not valid", path=path) from error
    canonical = str(parsed)
    if canonical != thread_id:
        raise integrity("thread identifier is not valid", path=path)
    return canonical


def validate_final_encounter_filename(
    name: str,
    *,
    path: Path | None = None,
    label: str = "latest encounter",
) -> str:
    validated = validate_safe_filename(name, path=path, label=label)
    if validated.startswith(_PENDING_PREFIX):
        raise integrity("latest encounter must not reference a pending record", path=path)
    return validated


def validate_safe_filename(name: str, *, path: Path | None = None, label: str = "filename") -> str:
    if type(name) is not str or not name or name in {".", ".."}:
        raise integrity(f"{label} is not valid", path=path)
    if "/" in name or "\\" in name or name != Path(name).name:
        raise integrity(f"{label} is not valid", path=path)
    return name


def validate_calendar_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise integrity("session record heading is not valid") from error
    if parsed.isoformat() != value:
        raise integrity("session record heading is not valid")
    return value


def record_to_document(record: ThreadRecord) -> dict[str, object]:
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


def _validated_status(value: object, path: Path) -> str:
    if type(value) is not str or value not in _VALID_STATUSES:
        raise integrity("thread metadata has an invalid status", path=path)
    return value


def _validated_title(value: object, path: Path, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise integrity(f"thread metadata has an invalid {field}", path=path)
    return value.strip()


def _validated_optional_title(value: object, path: Path, field: str) -> str | None:
    if value is None:
        return None
    return _validated_title(value, path, field)


def document_to_record(
    document: dict[str, object],
    *,
    path: Path,
    directory_id: str,
    sessions_dir: Path | None = None,
) -> ThreadRecord:
    if set(document) != _THREAD_KEYS:
        raise integrity("thread metadata has unexpected fields", path=path)
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != THREAD_SCHEMA_VERSION
    ):
        raise integrity("thread metadata schema is not supported", path=path)

    thread_id = validate_canonical_thread_id(_validated_title(document["id"], path, "id"), path=path)
    if thread_id != directory_id:
        raise integrity("thread metadata id does not match directory", path=path)

    if type(document["next_step"]) is not str:
        raise integrity("thread metadata has an invalid next_step", path=path)

    latest_encounter: str | None
    if document["latest_encounter"] is None:
        latest_encounter = None
    else:
        latest_encounter = validate_final_encounter_filename(
            document["latest_encounter"],
            path=path,
        )
        if sessions_dir is not None:
            encounter_path = sessions_dir / latest_encounter
            if not encounter_path.is_file() or encounter_path.is_symlink():
                raise integrity(
                    "thread metadata latest encounter is not available",
                    path=encounter_path,
                )

    created_at = format_utc_timestamp(
        parse_utc_timestamp(
            document["created_at"],
            path=path,
            field="created_at",
        )
    )
    updated_at = format_utc_timestamp(
        parse_utc_timestamp(
            document["updated_at"],
            path=path,
            field="updated_at",
        )
    )

    return ThreadRecord(
        id=thread_id,
        provisional_title=_validated_title(document["provisional_title"], path, "provisional_title"),
        refined_title=_validated_optional_title(document["refined_title"], path, "refined_title"),
        original_goal=_validated_title(document["original_goal"], path, "original_goal"),
        status=_validated_status(document["status"], path),
        created_at=created_at,
        updated_at=updated_at,
        latest_encounter=latest_encounter,
        next_step=document["next_step"],
    )


def load_metadata_document(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
        raise integrity("thread metadata is not valid JSON", path=path) from error
    if type(document) is not dict:
        raise integrity("thread metadata must contain an object", path=path)
    return document