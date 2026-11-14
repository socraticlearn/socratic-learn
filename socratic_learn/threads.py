"""Local thread metadata and immutable encounter storage."""

from __future__ import annotations

from .thread_parsing import parse_session_record
from .thread_schema import (
    THREAD_SCHEMA_VERSION,
    LegacySessionSummary,
    MenuDiscovery,
    PendingRecovery,
    ParsedSessionRecord,
    ThreadRecord,
    ThreadSummary,
)
from .thread_paths import ensure_threads_root
from .thread_storage import (
    PendingLease,
    create_pending_encounter,
    create_pending_encounter_with_lease,
    create_thread,
    discard_unlaunched_thread,
    discard_pending_encounter,
    discover_menu_records,
    discover_legacy_sessions,
    finalize_thread_encounter,
    import_legacy_session,
    list_threads,
    load_thread,
    mark_thread_incomplete,
    recover_pending_encounters,
    threads_root,
    try_acquire_pending_lease,
)

__all__ = (
    "THREAD_SCHEMA_VERSION",
    "LegacySessionSummary",
    "MenuDiscovery",
    "PendingRecovery",
    "ParsedSessionRecord",
    "PendingLease",
    "ThreadRecord",
    "ThreadSummary",
    "create_pending_encounter",
    "create_pending_encounter_with_lease",
    "create_thread",
    "discard_unlaunched_thread",
    "discard_pending_encounter",
    "discover_menu_records",
    "discover_legacy_sessions",
    "ensure_threads_root",
    "finalize_thread_encounter",
    "import_legacy_session",
    "list_threads",
    "load_thread",
    "mark_thread_incomplete",
    "recover_pending_encounters",
    "parse_session_record",
    "threads_root",
    "try_acquire_pending_lease",
)