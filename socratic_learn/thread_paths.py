"""Shared ancestry validation for thread storage paths."""

from __future__ import annotations

from pathlib import Path

from .thread_schema import integrity, validate_canonical_thread_id, validate_safe_filename

_THREADS_DIRNAME = "threads"
_SESSIONS_DIRNAME = "sessions"
_METADATA_FILENAME = "thread.json"
_PENDING_PREFIX = "pending-"


def _reject_symlink_before_resolve(path: Path, description: str) -> None:
    try:
        if path.is_symlink():
            raise integrity(f"{description} must not be a symbolic link", path=path)
    except OSError as error:
        raise integrity(f"{description} could not be inspected", path=path) from error


def require_records_root(records: Path) -> Path:
    _reject_symlink_before_resolve(records, "records path")
    if not records.is_dir():
        raise integrity("records path is not a directory", path=records)
    return records.resolve()


def require_threads_root(records: Path) -> Path:
    records_root = require_records_root(records)
    threads = records_root / _THREADS_DIRNAME
    _reject_symlink_before_resolve(threads, "thread records")
    if not threads.is_dir():
        raise integrity("thread records is not a directory", path=threads)
    return threads.resolve()


def ensure_threads_root(records: Path) -> Path:
    records_root = require_records_root(records)
    threads = records_root / _THREADS_DIRNAME
    if threads.exists() or threads.is_symlink():
        return require_threads_root(records)
    try:
        threads.mkdir()
    except OSError as error:
        raise integrity("thread records directory could not be created", path=threads) from error
    return require_threads_root(records)


def require_thread_workspace(records: Path, thread_id: str) -> tuple[Path, Path, Path]:
    canonical = validate_canonical_thread_id(thread_id)
    threads = require_threads_root(records)
    thread_dir = threads / canonical
    _reject_symlink_before_resolve(thread_dir, "thread directory")
    if not thread_dir.is_dir():
        raise integrity("thread directory is not a directory", path=thread_dir)
    sessions_dir = thread_dir / _SESSIONS_DIRNAME
    _reject_symlink_before_resolve(sessions_dir, "encounter records")
    if not sessions_dir.is_dir():
        raise integrity("encounter records is not a directory", path=sessions_dir)
    metadata_path = thread_dir / _METADATA_FILENAME
    return thread_dir.resolve(), sessions_dir.resolve(), metadata_path


def require_legacy_within_records(records: Path, legacy_path: Path) -> Path:
    _reject_symlink_before_resolve(legacy_path, "legacy session record")
    records_root = require_records_root(records)
    legacy_root = records_root / _SESSIONS_DIRNAME
    _reject_symlink_before_resolve(legacy_root, "legacy session records root")
    if not legacy_root.is_dir():
        raise integrity("legacy session records root is not a directory", path=legacy_root)
    resolved_root = legacy_root.resolve()
    resolved_legacy = legacy_path.resolve()
    if resolved_legacy.parent != resolved_root:
        raise integrity("legacy session must be a direct child of sessions", path=legacy_path)
    try:
        resolved_legacy.relative_to(records_root)
    except ValueError as error:
        raise integrity("legacy session must stay within active records", path=legacy_path) from error
    return resolved_legacy


def resolve_pending_child(sessions_dir: Path, encounter_path: Path) -> Path:
    _reject_symlink_before_resolve(encounter_path, "encounter path")
    sessions_root = sessions_dir.resolve()
    candidate = encounter_path if encounter_path.is_absolute() else sessions_root / encounter_path
    _reject_symlink_before_resolve(candidate, "encounter path")
    candidate = candidate.resolve()
    if candidate.parent != sessions_root:
        raise integrity("encounter path must be a direct child of sessions", path=encounter_path)
    name = validate_safe_filename(candidate.name, path=encounter_path, label="encounter path")
    if not name.startswith(_PENDING_PREFIX):
        raise integrity("pending encounter filename is not valid", path=encounter_path)
    return sessions_root / name


def validate_storage_ancestry(records: Path, *, thread_id: str | None = None) -> Path:
    if thread_id is None:
        return require_records_root(records)
    require_thread_workspace(records, thread_id)
    return require_records_root(records)