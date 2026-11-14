"""Thread filesystem storage and encounter lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
import errno
import hashlib
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
    MenuDiscovery,
    PendingRecovery,
    ParsedSessionRecord,
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
_LEGACY_SOURCE_FILENAME = "legacy-source.json"


class PendingLease:
    """An OS-released advisory lock associated with one pending checkpoint."""

    def __init__(self, path: Path, descriptor: int) -> None:
        self.path = path
        self._descriptor = descriptor

    def release(self, *, remove: bool = False) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        identity = os.fstat(descriptor)
        try:
            _unlock_lease_descriptor(descriptor)
        finally:
            os.close(descriptor)
        if not remove:
            return
        try:
            current = self.path.stat(follow_symlinks=False)
            if (
                stat.S_ISREG(current.st_mode)
                and current.st_dev == identity.st_dev
                and current.st_ino == identity.st_ino
            ):
                self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def __enter__(self) -> PendingLease:
        return self

    def __exit__(self, *_error: object) -> None:
        self.release()


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


def _legacy_source_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _legacy_source_marker(thread_dir: Path) -> Path:
    return thread_dir / _LEGACY_SOURCE_FILENAME


def _load_legacy_source_digest(thread_dir: Path) -> str | None:
    marker = _legacy_source_marker(thread_dir)
    if not marker.exists() and not marker.is_symlink():
        return None
    _require_regular_file(marker, "legacy import marker")
    try:
        document = json.loads(marker.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
        raise integrity("legacy import marker is not valid JSON", path=marker) from error
    if (
        type(document) is not dict
        or set(document) != {"schema_version", "sha256"}
        or document["schema_version"] != 1
        or type(document["sha256"]) is not str
        or len(document["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in document["sha256"])
    ):
        raise integrity("legacy import marker is not valid", path=marker)
    return document["sha256"]


def _imported_legacy_threads(records: Path) -> dict[str, ThreadRecord]:
    records_root = require_records_root(records)
    threads = records_root / "threads"
    if not threads.exists() and not threads.is_symlink():
        return {}
    threads = require_threads_root(records)
    imported: dict[str, ThreadRecord] = {}
    for entry in sorted(threads.iterdir(), key=lambda item: item.name):
        try:
            if not _is_complete_thread_dir(entry):
                continue
            record = load_thread(records, entry.name)
            digest = _load_legacy_source_digest(entry)
        except (IntegrityError, OSError, UnicodeError):
            continue
        if digest is not None:
            imported.setdefault(digest, record)
    return imported


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


def _pending_lease_path(pending: Path) -> Path:
    _extract_pending_token(pending.name)
    return pending.with_name(f".{pending.name}.lease")


def _try_lock_lease_descriptor(descriptor: int) -> bool:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise
    return True


def _unlock_lease_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


def _try_acquire_lease_path(path: Path) -> PendingLease | None:
    if path.is_symlink():
        raise integrity("pending encounter lease must not be a symbolic link", path=path)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise integrity("pending encounter lease could not be opened", path=path) from error
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or path.is_symlink():
            raise integrity("pending encounter lease must be a regular file", path=path)
        os.set_inheritable(descriptor, False)
        if status.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        if not _try_lock_lease_descriptor(descriptor):
            os.close(descriptor)
            return None
        return PendingLease(path, descriptor)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


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


def create_pending_encounter_with_lease(
    records: Path,
    thread_id: str,
) -> tuple[Path, PendingLease]:
    """Create a pending checkpoint while already holding its advisory lease."""
    validate_storage_ancestry(records, thread_id=thread_id)
    load_thread(records, thread_id)
    _thread_dir, sessions_dir, _metadata_path = require_thread_workspace(
        records,
        thread_id,
    )
    while True:
        pending = sessions_dir / _pending_filename()
        if pending.exists() or pending.is_symlink():
            continue
        lease = _try_acquire_lease_path(_pending_lease_path(pending))
        if lease is None:
            continue
        try:
            created = create_pending_encounter(
                records,
                thread_id,
                path=pending,
            )
        except BaseException:
            lease.release(remove=True)
            raise
        return created, lease


def try_acquire_pending_lease(
    records: Path,
    thread_id: str,
    encounter_path: Path,
) -> PendingLease | None:
    """Try to lease one regular pending checkpoint without blocking."""
    validate_storage_ancestry(records, thread_id=thread_id)
    _thread_dir, sessions_dir, _metadata_path = require_thread_workspace(
        records,
        thread_id,
    )
    pending = resolve_pending_child(sessions_dir, encounter_path)
    _require_regular_file(pending, "pending encounter record")
    return _try_acquire_lease_path(_pending_lease_path(pending))


def discard_pending_encounter(
    records: Path,
    thread_id: str,
    encounter_path: Path,
) -> None:
    """Remove one validated pending encounter without touching final records."""
    validate_storage_ancestry(records, thread_id=thread_id)
    _thread_dir, sessions_dir, _metadata_path = require_thread_workspace(records, thread_id)
    pending = resolve_pending_child(sessions_dir, encounter_path)
    _require_regular_file(pending, "pending encounter record")
    try:
        pending.unlink()
    except OSError as error:
        raise integrity("pending encounter record could not be removed", path=pending) from error


def discard_unlaunched_thread(
    records: Path,
    thread_id: str,
    encounter_path: Path,
    *,
    lease: PendingLease | None = None,
) -> None:
    """Remove a new thread that never reached child process launch."""
    validate_storage_ancestry(records, thread_id=thread_id)
    thread_dir, sessions_dir, metadata_path = require_thread_workspace(
        records,
        thread_id,
    )
    current = load_thread(records, thread_id)
    pending = resolve_pending_child(sessions_dir, encounter_path)
    _require_regular_file(pending, "pending encounter record")
    entries = tuple(sessions_dir.iterdir())
    expected_entries = {pending}
    if lease is not None:
        expected_lease = _pending_lease_path(pending)
        if lease.path != expected_lease:
            raise integrity("pending encounter lease does not match", path=lease.path)
        _require_regular_file(expected_lease, "pending encounter lease")
        expected_entries.add(expected_lease)
    if (
        current.latest_encounter is not None
        or set(entries) != expected_entries
        or pending.stat(follow_symlinks=False).st_size != 0
        or _legacy_source_marker(thread_dir).exists()
        or _legacy_source_marker(thread_dir).is_symlink()
    ):
        raise integrity("unlaunched thread is not safe to remove", path=thread_dir)
    try:
        pending.unlink()
        if lease is not None:
            lease.release(remove=True)
        metadata_path.unlink()
        sessions_dir.rmdir()
        thread_dir.rmdir()
    except OSError as error:
        raise integrity("unlaunched thread could not be removed", path=thread_dir) from error


def _extract_pending_token(pending_name: str) -> str:
    if not pending_name.startswith(_PENDING_PREFIX) or not pending_name.endswith(".md"):
        raise integrity("pending encounter filename is not valid")
    token = pending_name[len(_PENDING_PREFIX) : -len(".md")]
    if not token:
        raise integrity("pending encounter filename is not valid")
    return token


def _mapped_final_name(pending_name: str, session_date: str) -> str:
    token = _extract_pending_token(pending_name)
    return validate_safe_filename(f"{session_date}-{token}.md", label="final encounter")


def _locate_promoted_final(sessions_dir: Path, pending_name: str) -> Path | None:
    token = _extract_pending_token(pending_name)
    matches = [
        entry
        for entry in sessions_dir.iterdir()
        if entry.is_file()
        and not entry.is_symlink()
        and entry.name.endswith(f"-{token}.md")
        and not entry.name.startswith(_PENDING_PREFIX)
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise integrity("promoted encounter record is ambiguous", path=sessions_dir / pending_name)
    return matches[0]


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
    current = load_thread(records, thread_id)
    promoted = False
    final_path: Path
    parsed: ParsedSessionRecord

    if pending.is_file():
        _require_regular_file(pending, "pending encounter record")
        try:
            payload = pending.read_bytes()
            parsed = parse_session_record(payload.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise integrity("encounter record is not valid UTF-8", path=pending) from error
        except OSError as error:
            raise integrity("encounter record could not be read", path=pending) from error
        final_path = sessions_dir / _mapped_final_name(pending.name, parsed.session_date)
        if final_path.exists() or final_path.is_symlink():
            raise integrity("encounter already finalized", path=final_path)
        try:
            os.replace(pending, final_path)
            promoted = True
        except OSError as error:
            raise integrity("pending encounter could not be promoted", path=pending) from error
    else:
        located = _locate_promoted_final(sessions_dir, pending.name)
        if located is None:
            raise integrity("pending encounter record could not be found", path=pending)
        final_path = located
        try:
            payload = final_path.read_bytes()
            parsed = parse_session_record(payload.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise integrity("encounter record is not valid UTF-8", path=final_path) from error
        except OSError as error:
            raise integrity("encounter record could not be read", path=final_path) from error
        if _mapped_final_name(pending.name, parsed.session_date) != final_path.name:
            raise integrity("promoted encounter record does not match pending token", path=final_path)
        if (
            current.latest_encounter == final_path.name
            and current.status == "complete"
            and current.refined_title == parsed.refined_title
            and current.next_step == parsed.next_step
        ):
            return current
        raise integrity(
            "pending encounter retry does not match current thread metadata",
            path=pending,
        )

    if (
        current.latest_encounter == final_path.name
        and current.status == "complete"
        and current.refined_title == parsed.refined_title
        and current.next_step == parsed.next_step
    ):
        return current

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
        if promoted:
            try:
                os.replace(final_path, pending)
            except OSError as error:
                raise integrity(
                    "promoted encounter could not be rolled back",
                    path=final_path,
                ) from error
        raise
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


def recover_pending_encounters(records: Path, thread_id: str) -> PendingRecovery:
    """Recover regular orphan checkpoints without following unsafe entries."""
    validate_storage_ancestry(records, thread_id=thread_id)
    load_thread(records, thread_id)
    _thread_dir, sessions_dir, _metadata_path = require_thread_workspace(
        records,
        thread_id,
    )
    regular: list[tuple[int, str, Path]] = []
    unsafe = 0
    for entry in sessions_dir.iterdir():
        if not (
            entry.name.startswith(_PENDING_PREFIX)
            and entry.name.endswith(".md")
        ):
            continue
        try:
            status = entry.stat(follow_symlinks=False)
        except OSError:
            unsafe += 1
            continue
        if entry.is_symlink() or not stat.S_ISREG(status.st_mode):
            unsafe += 1
            continue
        regular.append((status.st_mtime_ns, entry.name, entry))

    promoted = 0
    discarded = 0
    active = 0
    for _modified, _name, pending in sorted(regular):
        if _pending_lease_path(pending).is_symlink():
            unsafe += 1
            continue
        lease = try_acquire_pending_lease(records, thread_id, pending)
        if lease is None:
            active += 1
            continue
        try:
            try:
                parse_session_record(pending.read_text(encoding="utf-8"))
            except (IntegrityError, OSError, UnicodeError):
                discard_pending_encounter(records, thread_id, pending)
                discarded += 1
                continue
            finalize_thread_encounter(records, thread_id, pending)
            promoted += 1
        finally:
            lease.release(remove=True)
    return PendingRecovery(
        promoted=promoted,
        discarded=discarded,
        unsafe=unsafe,
        active=active,
    )


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
    imported_digests = set(_imported_legacy_threads(records))
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
            payload = entry.read_bytes()
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise integrity("legacy session is not valid UTF-8", path=entry) from error
        except OSError as error:
            raise integrity("legacy session could not be read", path=entry) from error
        if _legacy_source_digest(payload) in imported_digests:
            continue

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


def discover_menu_records(records: Path) -> MenuDiscovery:
    """Tolerantly discover menu entries while keeping strict APIs unchanged."""
    records_root = require_records_root(records)
    threads_dir = records_root / "threads"
    if threads_dir.is_symlink():
        raise integrity("thread records must not be a symbolic link", path=threads_dir)
    legacy_dir = records_root / "sessions"
    if legacy_dir.is_symlink() or not legacy_dir.is_dir():
        raise integrity("legacy session records root is not safe", path=legacy_dir)

    summaries: list[ThreadSummary] = []
    imported_digests: set[str] = set()
    invalid = 0
    discarded_pending = 0
    unsafe_pending = 0
    recovery_failures = 0
    if threads_dir.is_dir():
        for entry in sorted(threads_dir.iterdir(), key=lambda item: item.name):
            try:
                if not _is_complete_thread_dir(entry):
                    raise integrity("thread entry is not valid", path=entry)
                record = load_thread(records, entry.name)
            except (IntegrityError, OSError, UnicodeError):
                invalid += 1
                continue
            try:
                recovery = recover_pending_encounters(records, record.id)
                discarded_pending += recovery.discarded
                unsafe_pending += recovery.unsafe
            except (IntegrityError, OSError, UnicodeError):
                recovery_failures += 1
            try:
                record = load_thread(records, entry.name)
                digest = _load_legacy_source_digest(entry)
                if digest is not None:
                    imported_digests.add(digest)
                summaries.append(
                    ThreadSummary(
                        id=record.id,
                        display_title=record.refined_title or record.provisional_title,
                        updated_at=record.updated_at,
                        next_step=record.next_step,
                        status=record.status,
                    )
                )
            except (IntegrityError, OSError, UnicodeError):
                invalid += 1

    legacy: list[LegacySessionSummary] = []
    for entry in sorted(legacy_dir.iterdir(), key=lambda item: item.name):
        try:
            if entry.is_symlink():
                raise integrity("legacy session record must not be a symbolic link", path=entry)
            _require_regular_file(entry, "legacy session record")
            payload = entry.read_bytes()
            text = payload.decode("utf-8")
            if _legacy_source_digest(payload) in imported_digests:
                continue
            display_title, next_step, _refined = validate_legacy_session_content(text)
            status = entry.stat(follow_symlinks=False)
            legacy.append(
                LegacySessionSummary(
                    path=entry,
                    display_title=display_title,
                    updated_at=format_utc_timestamp(
                        datetime.fromtimestamp(status.st_mtime, tz=UTC)
                    ),
                    next_step=next_step,
                )
            )
        except (IntegrityError, OSError, UnicodeError):
            invalid += 1

    summaries.sort(key=lambda item: item.id)
    summaries.sort(
        key=lambda item: parse_utc_timestamp(item.updated_at, field="updated_at"),
        reverse=True,
    )
    legacy.sort(key=lambda item: item.path.name)
    legacy.sort(
        key=lambda item: parse_utc_timestamp(item.updated_at, field="updated_at"),
        reverse=True,
    )
    return MenuDiscovery(
        threads=tuple(summaries),
        legacy=tuple(legacy),
        invalid_entries=invalid,
        discarded_pending=discarded_pending,
        unsafe_pending=unsafe_pending,
        recovery_failures=recovery_failures,
    )


def import_legacy_session(records: Path, legacy_path: Path) -> ThreadRecord:
    validate_storage_ancestry(records)
    resolved = require_legacy_within_records(records, legacy_path)
    _require_regular_file(resolved, "legacy session record")

    try:
        payload = resolved.read_bytes()
    except OSError as error:
        raise integrity("legacy session could not be read", path=resolved) from error

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise integrity("legacy session is not valid UTF-8", path=resolved) from error

    digest = _legacy_source_digest(payload)
    existing = _imported_legacy_threads(records).get(digest)
    if existing is not None:
        return existing

    display_title, next_step, refined_title = validate_legacy_session_content(text)
    session_date, _heading_title = parse_session_heading(text)

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
            status="complete",
            created_at=record.created_at,
            updated_at=utc_now_timestamp(),
            latest_encounter=final_path.name,
            next_step=next_step,
        )
        _save_thread_metadata(metadata_path, imported)
        _atomic_write_json(
            _legacy_source_marker(thread_dir),
            {"schema_version": 1, "sha256": digest},
        )
    except IntegrityError:
        _remove_tree(thread_dir)
        raise
    except OSError as error:
        _remove_tree(thread_dir)
        raise integrity("legacy session import could not be completed", path=resolved) from error
    return imported