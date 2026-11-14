"""Local thread metadata and immutable encounter storage."""

from __future__ import annotations

from .thread_parsing import parse_session_record
from .thread_schema import (
    THREAD_SCHEMA_VERSION,
    LegacySessionSummary,
    ParsedSessionRecord,
    ThreadRecord,
    ThreadSummary,
)
from .thread_paths import ensure_threads_root
from .thread_storage import (
    create_pending_encounter,
    create_thread,
    discover_legacy_sessions,
    finalize_thread_encounter,
    import_legacy_session,
    list_threads,
    load_thread,
    mark_thread_incomplete,
    threads_root,
)

__all__ = (
    "THREAD_SCHEMA_VERSION",
    "LegacySessionSummary",
    "ParsedSessionRecord",
    "ThreadRecord",
    "ThreadSummary",
    "create_pending_encounter",
    "create_thread",
    "discover_legacy_sessions",
    "ensure_threads_root",
    "finalize_thread_encounter",
    "import_legacy_session",
    "list_threads",
    "load_thread",
    "mark_thread_incomplete",
    "parse_session_record",
    "threads_root",
)