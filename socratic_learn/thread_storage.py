"""Thread filesystem storage and encounter lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import secrets
import shutil
import stat
import uuid
from pathlib import Path

from .errors import IntegrityError
from .thread_parsing import (
    parse_session_heading,
    parse_session_record,
    validate_legacy_session_content,
)
from .thread_paths import (
    ensure_threads_root,
    require_legacy_within_records,
    require_records_root,
    require_thread_workspace,
    require_threads_root,
    resolve_pending_child,
    validate_storage_ancestry,
)
from .thread_schema import (
    LegacySessionSummary,
    ThreadRecord,
    ThreadSummary,
    document_to_record,
    format_utc_timestamp,
    integrity,
    load_metadata_document,
    parse_utc_timestamp,
    record_to_document,
    utc_now_timestamp,
    validate_safe_filename,
)

_THREAD_FILENAME = "thread.json"
_SESSIONS_DIRNAME = "sessions"
_PENDING_PREFIX = "pending-"


def threads_root(records: Path) -> Path:
    return require_records_root(records) / "threads"


def _require_regular_file(path: Path, description: str) -> None:
    try:
        if path.is_symlink():
            raise integrity(f"{description} must not be a symbolic link", path=path)
    except OSError as error:
        raise integrity(f"{description} could not be inspected", path=path) from error
    try:
        status = path.stat(follow_symlinks=False)
    except OSError as error:
        raise integrity(f"{description} could not be inspected", path=path) from error
    if not stat.S_ISREG(status.st_mode):
        raise integrity(f"{description} must be a regular file", path=path)


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _remove_tree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError as error:
        raise integrity("partial thread directory could not be removed", path=path) from error


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    parent = path.parent
    try:
        if parent.is_symlink():
            raise integrity("thread metadata parent must not be a symbolic link", path=parent)
    except OSError as error:
        raise integrity("thread metadata parent could not be inspected", path=parent) from error
    if path.exists() or path.is_symlink():
        try:
            if path.is_symlink():
                raise integrity("thread metadata must not be a symbolic link", path=path)
        except OSError as error:
            raise integrity("thread metadata could not be inspected", path=path) from error
    serialized = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
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
            except OSError as error:
                raise integrity("thread metadata could not be created", path=candidate) from error
            temporary = candidate

        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, path)
        except OSError as error:
            raise integrity("thread metadata could not be published", path=path) from error
        temporary = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _save_thread_metadata(metadata_path: Path, record: ThreadRecord) -> None:
    _atomic_write_json(metadata_path, record_to_document(record))


def _is_complete_thread_dir(thread_dir: Path) -> bool:
    if not thread_dir.is_dir() or thread_dir.is_symlink():
        return False
    metadata_path = thread_dir / _THREAD_FILENAME
    sessions_dir = thread_dir / _SESSIONS_DIRNAME
    if metadata_path.is_symlink() or sessions_dir.is_symlink():
        return False
    return (
        metadata_path.is_file()
        and sessions_dir.is_dir()
    )


def load_thread(records: Path, thread_id: str) -> ThreadRecord:
    validate_storage_ancestry(records, thread_id=thread_id)
    thread_dir, sessions_dir, metadata_path = require_thread_workspace(records, thread_id)
    if metadata_path.is_symlink():
        raise integrity("thread metadata must not be a symbolic link", path=metadata_path)
    _require_regular_file(metadata_path, "thread metadata")
    document = load_metadata_document(metadata_path)
    return document_to_record(
        document,
        path=metadata_path,
        directory_id=thread_dir.name,
        sessions_dir=sessions_dir,
    )


def create_thread(
    records: Path,
    *,
    goal: str,
    provisional_title: str | None = None,
) -> ThreadRecord:
    cleaned_goal = goal.strip()
    if not cleaned_goal:
        raise integrity("thread goal must not be empty")

    ensure_threads_root(records)
    title = cleaned_goal if provisional_title is None else provisional_title.strip()
    if not title:
        raise integrity("thread provisional title must not be empty")

    thread_id = str(uuid.uuid4())
    threads = require_threads_root(records)
    thread_dir = threads / thread_id
    sessions_dir = thread_dir / _SESSIONS_DIRNAME
    metadata_path = thread_dir / _THREAD_FILENAME

    if thread_dir.exists() or thread_dir.is_symlink():
        raise integrity("thread directory already exists", path=thread_dir)

    try:
        thread_dir.mkdir()
        sessions_dir.mkdir()
    except OSError as error:
        if thread_dir.exists():
            _remove_tree(thread_dir)
        raise integrity("thread directory could not be created", path=thread_dir) from error

    timestamp = utc_now_timestamp()
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

    try:
        _atomic_write_json(metadata_path, record_to_document(record))
    except IntegrityError:
        _remove_tree(thread_dir)
        raise
    except OSError as error:
        _remove_tree(thread_dir)
        raise integrity("thread metadata could not be published", path=metadata_path) from error
    return record


def _pending_filename() -> str:
    return f"{_PENDING_PREFIX}{secrets.token_hex(8)}.md"


def create_pending_encounter(
    records: Path,
    thread_id: str,
    *,
    path: Path | None = None,
) -> Path:
    validate_storage_ancestry(records, thread_id=thread_id)
    load_thread(records, thread_id)
    _thread_dir, sessions_dir, _metadata_path = require_thread_workspace(records, thread_id)

    if path is None:
        destination = sessions_dir / _pending_filename()
        while destination.exists() or destination.is_symlink():
            destination = sessions_dir / _pending_filename()
    else:
        destination = resolve_pending_child(sessions_dir, path)

    if destination.exists() or destination.is_symlink():
        raise integrity("encounter record already exists", path=destination)

    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise integrity("encounter record already exists", path=destination) from error
    except OSError as error:
        raise integrity("encounter record could not be created", path=destination) from error

    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise integrity("encounter record could not be created", path=destination) from error
    return destination


def _publish_final_encounter(sessions_dir: Path, *, session_date: str, payload: bytes) -> Path:
    final_name = validate_safe_filename(f"{session_date}-{secrets.token_hex(4)}.md", label="final encounter")
    destination = sessions_dir / final_name
    if destination.exists() or destination.is_symlink():
        raise integrity("final encounter record already exists", path=destination)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise integrity("final encounter record already exists", path=destination) from error
    except OSError as error:
        raise integrity("final encounter record could not be created", path=destination) from error

    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        try:
            destination.unlink()
        except OSError:
            pass
        raise integrity("final encounter record could not be created", path=destination) from error
    return destination


def finalize_thread_encounter(
    records: Path,
    thread_id: str,
    encounter_path: Path,
) -> ThreadRecord:
    validate_storage_ancestry(records, thread_id=thread_id)
    thread_dir, sessions_dir, metadata_path = require_thread_workspace(records, thread_id)
    pending = resolve_pending_child(sessions_dir, encounter_path)
    _require_regular_file(pending, "pending encounter record")

    current = load_thread(records, thread_id)
    if current.latest_encounter is not None and current.latest_encounter == pending.name:
        raise integrity(
            "pending encounter is already referenced by thread metadata",
            path=pending,
        )

    try:
        payload = pending.read_bytes()
        parsed = parse_session_record(payload.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise integrity("encounter record is not valid UTF-8", path=pending) from error
    except OSError as error:
        raise integrity("encounter record could not be read", path=pending) from error

    if current.latest_encounter is not None:
        existing_final = sessions_dir / current.latest_encounter
        if existing_final.is_file() and not existing_final.is_symlink():
            try:
                if existing_final.read_bytes() == payload:
                    _best_effort_unlink(pending)
                    return current
            except OSError:
                pass

    final_path = _publish_final_encounter(sessions_dir, session_date=parsed.session_date, payload=payload)
    updated = ThreadRecord(
        id=current.id,
        provisional_title=current.provisional_title,
        refined_title=parsed.refined_title,
        original_goal=current.original_goal,
        status="complete",
        created_at=current.created_at,
        updated_at=utc_now_timestamp(),
        latest_encounter=final_path.name,
        next_step=parsed.next_step,
    )
    try:
        _save_thread_metadata(metadata_path, updated)
    except (IntegrityError, OSError):
        try:
            if final_path.exists() or final_path.is_symlink():
                final_path.unlink()
        except OSError:
            pass
        raise

    _best_effort_unlink(pending)
    return updated


def mark_thread_incomplete(records: Path, thread_id: str) -> ThreadRecord:
    validate_storage_ancestry(records, thread_id=thread_id)
    current = load_thread(records, thread_id)
    updated = ThreadRecord(
        id=current.id,
        provisional_title=current.provisional_title,
        refined_title=current.refined_title,
        original_goal=current.original_goal,
        status="incomplete",
        created_at=current.created_at,
        updated_at=utc_now_timestamp(),
        latest_encounter=current.latest_encounter,
        next_step=current.next_step,
    )
    _thread_dir, _sessions_dir, metadata_path = require_thread_workspace(records, thread_id)
    _save_thread_metadata(metadata_path, updated)
    return updated


def list_threads(records: Path) -> tuple[ThreadSummary, ...]:
    records_root = require_records_root(records)
    threads = records_root / "threads"
    if threads.is_symlink():
        raise integrity("thread records must not be a symbolic link", path=threads)
    if not threads.is_dir():
        return ()

    summaries: list[ThreadSummary] = []
    for entry in sorted(threads.iterdir(), key=lambda item: item.name):
        if not _is_complete_thread_dir(entry):
            continue
        record = load_thread(records, entry.name)
        if record.id != entry.name:
            continue
        summaries.append(
            ThreadSummary(
                id=record.id,
                display_title=record.refined_title or record.provisional_title,
                updated_at=record.updated_at,
                next_step=record.next_step,
                status=record.status,
            )
        )

    summaries.sort(key=lambda item: item.id)
    summaries.sort(
        key=lambda item: parse_utc_timestamp(item.updated_at, field="updated_at"),
        reverse=True,
    )
    return tuple(summaries)


def discover_legacy_sessions(records: Path) -> tuple[LegacySessionSummary, ...]:
    records_root = require_records_root(records)
    legacy_dir = records_root / "sessions"
    if legacy_dir.is_symlink():
        raise integrity("legacy session records root must not be a symbolic link", path=legacy_dir)
    if not legacy_dir.is_dir():
        raise integrity("legacy session records root is not a directory", path=legacy_dir)

    discovered: list[LegacySessionSummary] = []
    for entry in sorted(legacy_dir.iterdir(), key=lambda item: item.name):
        try:
            if entry.is_symlink():
                raise integrity("legacy session record must not be a symbolic link", path=entry)
        except OSError as error:
            raise integrity("legacy session record could not be inspected", path=entry) from error
        _require_regular_file(entry, "legacy session record")
        try:
            text = entry.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise integrity("legacy session is not valid UTF-8", path=entry) from error
        except OSError as error:
            raise integrity("legacy session could not be read", path=entry) from error

        display_title, next_step, _refined = validate_legacy_session_content(text)
        status = entry.stat(follow_symlinks=False)
        discovered.append(
            LegacySessionSummary(
                path=entry,
                display_title=display_title,
                updated_at=format_utc_timestamp(datetime.fromtimestamp(status.st_mtime, tz=UTC)),
                next_step=next_step,
            )
        )

    discovered.sort(key=lambda item: item.path.name)
    discovered.sort(
        key=lambda item: parse_utc_timestamp(item.updated_at, field="updated_at"),
        reverse=True,
    )
    return tuple(discovered)


def import_legacy_session(records: Path, legacy_path: Path) -> ThreadRecord:
    validate_storage_ancestry(records)
    resolved = require_legacy_within_records(records, legacy_path)
    _require_regular_file(resolved, "legacy session record")

    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise integrity("legacy session is not valid UTF-8", path=resolved) from error
    except OSError as error:
        raise integrity("legacy session could not be read", path=resolved) from error

    display_title, next_step, refined_title = validate_legacy_session_content(text)
    session_date, _heading_title = parse_session_heading(text)
    status = "complete" if refined_title is not None else "incomplete"
    payload = text.encode("utf-8")

    record = create_thread(records, goal=display_title, provisional_title=display_title)
    thread_dir, sessions_dir, metadata_path = require_thread_workspace(records, record.id)

    try:
        final_path = _publish_final_encounter(
            sessions_dir,
            session_date=session_date,
            payload=payload,
        )
        imported = ThreadRecord(
            id=record.id,
            provisional_title=record.provisional_title,
            refined_title=refined_title,
            original_goal=record.original_goal,
            status=status,
            created_at=record.created_at,
            updated_at=utc_now_timestamp(),
            latest_encounter=final_path.name,
            next_step=next_step,
        )
        _save_thread_metadata(metadata_path, imported)
    except IntegrityError:
        _remove_tree(thread_dir)
        raise
    except OSError as error:
        _remove_tree(thread_dir)
        raise integrity("legacy session import could not be completed", path=resolved) from error
    return imported