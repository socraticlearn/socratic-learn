from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import socratic_learn.thread_storage as thread_storage_module
from socratic_learn.errors import IntegrityError
from socratic_learn.manifest import (
    SESSION_HEADINGS,
    SESSION_RECORD_VERSION_MARKER,
)
from socratic_learn.thread_schema import parse_utc_timestamp
from socratic_learn.threads import (
    THREAD_SCHEMA_VERSION,
    PendingLease,
    create_pending_encounter,
    create_pending_encounter_with_lease,
    create_thread,
    discard_pending_encounter,
    discover_legacy_sessions,
    finalize_thread_encounter,
    import_legacy_session,
    list_threads,
    load_thread,
    mark_thread_incomplete,
    parse_session_record,
    recover_pending_encounters,
    try_acquire_pending_lease,
    threads_root,
)


def _valid_session_body(*, title: str, next_step: str, session_date: str | None = None) -> str:
    today = session_date or datetime.now(UTC).strftime("%Y-%m-%d")
    sections = {
        "## Outcome": "Explain one observable result.",
        "## Diagnostic evidence": "Recorded a misconception and revision.",
        "## Learning route": "1. Prerequisite -> target skill.",
        "## Concepts established": "Named the corrected concept with support.",
        "## Application evidence": "Completed one worked example.",
        "## Remaining gaps": "One gap remains explicitly recorded.",
        "## Next step": next_step,
        "## Sources": "No external sources were used.",
    }
    lines = [f"# Session: {today} - {title}", ""]
    for heading in SESSION_HEADINGS:
        lines.extend([heading, sections[heading], ""])
    return "\n".join(lines)


def _pending_checkpoint_body(
    *,
    title: str,
    next_step: str | None = None,
    session_date: str | None = None,
) -> str:
    """Version-2 body for pending checkpoints that finalize under current rules."""

    if next_step is None:
        return _valid_v2_session_body(title=title, session_date=session_date)
    if next_step.startswith("- ["):
        return _valid_v2_session_body(
            title=title,
            next_step=next_step,
            session_date=session_date,
        )
    return _valid_v2_session_body(
        title=title,
        next_step=f"- [G1] {next_step}",
        session_date=session_date,
    )


def _valid_v2_session_body(
    *,
    title: str,
    next_step: str | None = None,
    session_date: str | None = None,
) -> str:
    today = session_date or datetime.now(UTC).strftime("%Y-%m-%d")
    sections = {
        "## Outcome": "Explain one observable result.",
        "## Diagnostic evidence": "Recorded a misconception and revision.",
        "## Learning route": "1. Prerequisite -> target skill.",
        "## Concepts established": "- [E1] Named the corrected concept with support.",
        "## Application evidence": (
            "- [E1] Task: Apply the concept | Conditions: One problem | "
            "Result: Correct answer | Support: Tutor hint | Limits: Single topic"
        ),
        "## Remaining gaps": "- [G1] One gap remains explicitly recorded.",
        "## Next step": next_step or "- [G1] Continue with the gap.",
        "## Sources": "No external sources were used.",
    }
    lines = [f"# Session: {today} - {title}", SESSION_RECORD_VERSION_MARKER, ""]
    for heading in SESSION_HEADINGS:
        lines.extend([heading, sections[heading], ""])
    return "\n".join(lines)


def _valid_v2_all_sentinel_session_body(
    *,
    title: str,
    session_date: str | None = None,
) -> str:
    today = session_date or datetime.now(UTC).strftime("%Y-%m-%d")
    sections = {
        "## Outcome": "Explain one observable result.",
        "## Diagnostic evidence": "Recorded a misconception and revision.",
        "## Learning route": "1. Prerequisite -> target skill.",
        "## Concepts established": "- None.",
        "## Application evidence": "- None collected.",
        "## Remaining gaps": "- None.",
        "## Next step": "- None.",
        "## Sources": "No external sources were used.",
    }
    lines = [f"# Session: {today} - {title}", SESSION_RECORD_VERSION_MARKER, ""]
    for heading in SESSION_HEADINGS:
        lines.extend([heading, sections[heading], ""])
    return "\n".join(lines)


def _symlinks_supported() -> bool:
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        target = base / "target"
        target.mkdir()
        link = base / "link"
        try:
            link.symlink_to(target)
        except OSError:
            return False
        return link.is_symlink()


class ThreadStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.records = Path(self.temporary.name).resolve(strict=True) / "records"
        self.records.mkdir()
        (self.records / "sessions").mkdir()

    def test_threads_root_is_under_active_records(self) -> None:
        self.assertEqual(self.records / "threads", threads_root(self.records))

    def test_create_thread_writes_versioned_metadata_and_sessions_dir(self) -> None:
        created = create_thread(self.records, goal="understand dot products")
        thread_dir = threads_root(self.records) / created.id
        metadata_path = thread_dir / "thread.json"
        self.assertTrue(metadata_path.is_file())
        self.assertTrue((thread_dir / "sessions").is_dir())

        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(
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
            },
            set(payload),
        )
        self.assertEqual(THREAD_SCHEMA_VERSION, payload["schema_version"])
        self.assertEqual(created.id, payload["id"])
        self.assertEqual("understand dot products", payload["original_goal"])
        self.assertEqual("", payload["next_step"])

    def test_partial_thread_directory_is_not_listed(self) -> None:
        thread_id = str(uuid.uuid4())
        partial = threads_root(self.records) / thread_id
        partial.mkdir(parents=True)
        (partial / "sessions").mkdir()
        self.assertEqual([], list(list_threads(self.records)))

    def test_create_thread_cleans_up_when_metadata_publish_fails(self) -> None:
        with mock.patch(
            "socratic_learn.thread_storage._atomic_write_json",
            side_effect=IntegrityError("publish failed"),
        ):
            with self.assertRaises(IntegrityError):
                create_thread(self.records, goal="cleanup")
        root = threads_root(self.records)
        if root.exists():
            self.assertEqual([], list(root.iterdir()))

    def test_rejects_invalid_thread_identifiers(self) -> None:
        created = create_thread(self.records, goal="valid")
        with self.assertRaises(IntegrityError):
            load_thread(self.records, "../escape")
        with self.assertRaises(IntegrityError):
            load_thread(self.records, "not-a-uuid")
        with self.assertRaises(IntegrityError):
            create_pending_encounter(self.records, "not-a-uuid")

    def test_encounter_path_must_be_direct_child(self) -> None:
        thread = create_thread(self.records, goal="paths")
        sessions = threads_root(self.records) / thread.id / "sessions"
        nested = sessions / "nested" / "record.md"
        nested.parent.mkdir()
        absolute = Path(tempfile.gettempdir()) / "absolute-session.md"
        custom = sessions / "custom.md"

        with self.assertRaises(IntegrityError):
            create_pending_encounter(self.records, thread.id, path=nested)
        with self.assertRaises(IntegrityError):
            create_pending_encounter(self.records, thread.id, path=absolute)
        with self.assertRaises(IntegrityError):
            create_pending_encounter(self.records, thread.id, path=custom)

    def test_create_pending_encounter_is_exclusive_and_immutable(self) -> None:
        thread = create_thread(self.records, goal="series practice")
        first = create_pending_encounter(self.records, thread.id)
        second = create_pending_encounter(self.records, thread.id)
        self.assertNotEqual(first, second)
        self.assertTrue(first.name.startswith("pending-"))
        first.write_text("first encounter\n", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            create_pending_encounter(self.records, thread.id, path=first)
        self.assertEqual("first encounter\n", first.read_text(encoding="utf-8"))

    def test_discard_pending_encounter_removes_only_validated_pending_child(self) -> None:
        thread = create_thread(self.records, goal="cleanup")
        pending = create_pending_encounter(self.records, thread.id)
        pending.write_text("malformed host output", encoding="utf-8")

        discard_pending_encounter(self.records, thread.id, pending)

        self.assertFalse(pending.exists())
        with self.assertRaises(IntegrityError):
            discard_pending_encounter(
                self.records,
                thread.id,
                pending.parent / "not-pending.md",
            )

    def test_finalize_publishes_exclusive_final_encounter(self) -> None:
        thread = create_thread(self.records, goal="platonic forms")
        pending = create_pending_encounter(self.records, thread.id)
        pending.write_text(
            _pending_checkpoint_body(
                title="Philosophy: Plato's cave allegory",
                next_step="Predict how the cave metaphor changes with a second source.",
            ),
            encoding="utf-8",
        )

        token = pending.name.removeprefix("pending-").removesuffix(".md")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        finalized = finalize_thread_encounter(self.records, thread.id, pending)
        sessions = threads_root(self.records) / thread.id / "sessions"
        final_path = sessions / finalized.latest_encounter
        self.assertEqual(f"{today}-{token}.md", finalized.latest_encounter)
        self.assertTrue(final_path.is_file())
        self.assertFalse(pending.exists())
        self.assertEqual("Philosophy: Plato's cave allegory", finalized.refined_title)
        self.assertEqual("complete", finalized.status)

    def test_finalize_retry_completes_without_duplicate_final(self) -> None:
        thread = create_thread(self.records, goal="once")
        pending = create_pending_encounter(self.records, thread.id)
        pending.write_text(
            _pending_checkpoint_body(
                title="Math: One attempt",
                next_step="Retry later.",
            ),
            encoding="utf-8",
        )
        first = finalize_thread_encounter(self.records, thread.id, pending)
        second = finalize_thread_encounter(self.records, thread.id, pending)
        self.assertEqual(first, second)
        sessions = threads_root(self.records) / thread.id / "sessions"
        finals = [
            path
            for path in sessions.iterdir()
            if path.is_file() and not path.name.startswith("pending-")
        ]
        self.assertEqual(1, len(finals))

    def test_finalize_retry_rejects_stale_pending_after_newer_encounter(self) -> None:
        thread = create_thread(self.records, goal="encounter order")
        pending_a = create_pending_encounter(self.records, thread.id)
        pending_b = create_pending_encounter(self.records, thread.id)
        pending_a.write_text(
            _pending_checkpoint_body(title="Physics: Motion", next_step="Continue A."),
            encoding="utf-8",
        )
        pending_b.write_text(
            _pending_checkpoint_body(title="Physics: Energy", next_step="Continue B."),
            encoding="utf-8",
        )
        first = finalize_thread_encounter(self.records, thread.id, pending_a)
        second = finalize_thread_encounter(self.records, thread.id, pending_b)
        before = load_thread(self.records, thread.id)

        with self.assertRaises(IntegrityError):
            finalize_thread_encounter(self.records, thread.id, pending_a)

        after = load_thread(self.records, thread.id)
        self.assertEqual(before, after)
        self.assertEqual(second.latest_encounter, after.latest_encounter)
        self.assertNotEqual(first.latest_encounter, after.latest_encounter)

    def test_finalize_identical_content_creates_distinct_final_encounters(self) -> None:
        thread = create_thread(self.records, goal="duplicate bodies")
        body = _pending_checkpoint_body(title="Chemistry: Bonds", next_step="Continue.")
        first_pending = create_pending_encounter(self.records, thread.id)
        second_pending = create_pending_encounter(self.records, thread.id)
        first_pending.write_text(body, encoding="utf-8")
        second_pending.write_text(body, encoding="utf-8")
        first = finalize_thread_encounter(self.records, thread.id, first_pending)
        second = finalize_thread_encounter(self.records, thread.id, second_pending)
        self.assertNotEqual(first.latest_encounter, second.latest_encounter)
        sessions = threads_root(self.records) / thread.id / "sessions"
        finals = [
            path
            for path in sessions.iterdir()
            if path.is_file() and not path.name.startswith("pending-")
        ]
        self.assertEqual(2, len(finals))

    def test_finalize_rejects_malformed_record_without_mutating_metadata(self) -> None:
        thread = create_thread(self.records, goal="keep metadata")
        pending = create_pending_encounter(self.records, thread.id)
        pending.write_text("# Session: not-a-valid-heading\n\n## Next step\nx\n", encoding="utf-8")
        before = load_thread(self.records, thread.id)

        with self.assertRaises(IntegrityError):
            finalize_thread_encounter(self.records, thread.id, pending)

        after = load_thread(self.records, thread.id)
        self.assertEqual(before, after)

    def test_mark_thread_incomplete_preserves_provisional_title(self) -> None:
        thread = create_thread(self.records, goal="interrupted study")
        incomplete = mark_thread_incomplete(self.records, thread.id)
        self.assertEqual("incomplete", incomplete.status)

    def test_list_threads_sorts_different_instants_latest_first(self) -> None:
        newer = create_thread(self.records, goal="newer")
        older = create_thread(self.records, goal="older")
        newer_meta = threads_root(self.records) / newer.id / "thread.json"
        older_meta = threads_root(self.records) / older.id / "thread.json"
        newer_payload = json.loads(newer_meta.read_text(encoding="utf-8"))
        older_payload = json.loads(older_meta.read_text(encoding="utf-8"))
        newer_payload["updated_at"] = "2026-01-03T12:00:00.000001Z"
        older_payload["updated_at"] = "2026-01-03T12:00:00.000000Z"
        newer_meta.write_text(json.dumps(newer_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        older_meta.write_text(json.dumps(older_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summaries = list_threads(self.records)
        self.assertEqual([newer.id, older.id], [item.id for item in summaries])

    def test_list_threads_sorts_by_parsed_updated_at_desc_then_id(self) -> None:
        first_id = "00000000-0000-4000-8000-000000000001"
        second_id = "00000000-0000-4000-8000-000000000002"
        root = threads_root(self.records)
        root.mkdir(parents=True, exist_ok=True)

        def seed(thread_id: str, updated_at: str, title: str) -> None:
            thread_dir = root / thread_id
            (thread_dir / "sessions").mkdir(parents=True)
            payload = {
                "schema_version": THREAD_SCHEMA_VERSION,
                "id": thread_id,
                "provisional_title": title,
                "refined_title": None,
                "original_goal": title,
                "status": "active",
                "created_at": "2026-01-01T00:00:00.123456Z",
                "updated_at": updated_at,
                "latest_encounter": None,
                "next_step": "",
            }
            (thread_dir / "thread.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        seed(first_id, "2026-01-02T00:00:00.500000Z", "alpha")
        seed(second_id, "2026-01-02T00:00:00.500000Z", "beta")

        summaries = list_threads(self.records)
        self.assertEqual([first_id, second_id], [item.id for item in summaries])

    def test_load_thread_rejects_metadata_id_mismatch(self) -> None:
        created = create_thread(self.records, goal="match")
        metadata_path = threads_root(self.records) / created.id / "thread.json"
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload["id"] = str(uuid.uuid4())
        metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            load_thread(self.records, created.id)

    def test_load_thread_rejects_malformed_timestamp(self) -> None:
        created = create_thread(self.records, goal="time")
        metadata_path = threads_root(self.records) / created.id / "thread.json"
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload["updated_at"] = "not-a-timestamp"
        metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            load_thread(self.records, created.id)

    def test_discover_legacy_sessions_is_read_only(self) -> None:
        legacy = self.records / "sessions" / "2026-01-15-example.md"
        legacy.write_bytes(
            (
                Path(__file__).resolve().parents[1]
                / "workspace.example/sessions/2026-01-15-example.md"
            ).read_bytes()
        )
        before = legacy.read_bytes()
        discovered = discover_legacy_sessions(self.records)
        self.assertEqual(1, len(discovered))
        self.assertEqual(before, legacy.read_bytes())

    def test_discover_legacy_sessions_rejects_malformed_record(self) -> None:
        (self.records / "sessions" / "broken.md").write_text("not a session", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            discover_legacy_sessions(self.records)

    def test_discover_legacy_sessions_rejects_unsafe_sessions_root(self) -> None:
        sessions = self.records / "sessions"
        sessions.rmdir()
        outside = self.records.parent / "outside-sessions"
        outside.mkdir()
        sessions.symlink_to(outside) if _symlinks_supported() else None
        if sessions.is_symlink():
            with self.assertRaises(IntegrityError):
                discover_legacy_sessions(self.records)
        else:
            self.skipTest("symlinks unavailable")

    def test_import_legacy_session_copies_without_removing_original(self) -> None:
        legacy = self.records / "sessions" / "2026-01-15-example.md"
        legacy.write_bytes(
            (
                Path(__file__).resolve().parents[1]
                / "workspace.example/sessions/2026-01-15-example.md"
            ).read_bytes()
        )
        imported = import_legacy_session(self.records, legacy)
        self.assertTrue(legacy.is_file())
        copied = (
            threads_root(self.records) / imported.id / "sessions" / imported.latest_encounter
        )
        self.assertTrue(copied.is_file())
        self.assertFalse(imported.latest_encounter.startswith("pending-"))
        self.assertNotEqual(legacy.name, imported.latest_encounter)

    def test_repeated_legacy_import_reuses_thread_and_hides_source(self) -> None:
        legacy = self.records / "sessions" / "legacy.md"
        legacy.write_text(
            _valid_session_body(
                title="History: Reused evidence",
                next_step="Compare another source.",
            ),
            encoding="utf-8",
        )

        first = import_legacy_session(self.records, legacy)
        second = import_legacy_session(self.records, legacy)

        self.assertEqual(first.id, second.id)
        self.assertTrue(legacy.is_file())
        self.assertEqual(1, len(list_threads(self.records)))
        self.assertEqual((), discover_legacy_sessions(self.records))

    def test_corrupt_thread_does_not_block_valid_legacy_import(self) -> None:
        corrupt = threads_root(self.records) / str(uuid.uuid4())
        (corrupt / "sessions").mkdir(parents=True)
        (corrupt / "thread.json").write_text("{broken", encoding="utf-8")
        legacy = self.records / "sessions" / "legacy.md"
        legacy.write_text(
            _valid_session_body(
                title="History: Valid legacy",
                next_step="Continue.",
            ),
            encoding="utf-8",
        )

        imported = import_legacy_session(self.records, legacy)

        self.assertEqual("History: Valid legacy", imported.refined_title)
        self.assertTrue(legacy.is_file())

    def test_legacy_without_refined_title_is_complete_prior_evidence(self) -> None:
        legacy = self.records / "sessions" / "legacy.md"
        legacy.write_text(
            _valid_session_body(
                title="History overview",
                next_step="Choose a narrower question.",
            ),
            encoding="utf-8",
        )

        imported = import_legacy_session(self.records, legacy)

        self.assertEqual("complete", imported.status)
        self.assertIsNone(imported.refined_title)
        self.assertEqual("History overview", imported.provisional_title)

    def test_import_legacy_renames_pending_like_sources_to_final_encounter(self) -> None:
        legacy = self.records / "sessions" / "pending-evil.md"
        legacy.write_text(
            _valid_session_body(
                title="History: Ancient trade routes",
                next_step="Map one additional route.",
                session_date="2026-01-15",
            ),
            encoding="utf-8",
        )
        imported = import_legacy_session(self.records, legacy)
        self.assertFalse(imported.latest_encounter.startswith("pending-"))

    def test_legacy_whitespace_heading_topic_is_rejected(self) -> None:
        legacy = self.records / "sessions" / "blank-title.md"
        lines = _valid_session_body(
            title="Math: Topic",
            next_step="Continue.",
            session_date="2026-01-15",
        ).splitlines()
        lines[0] = "# Session: 2026-01-15 —    "
        legacy.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            discover_legacy_sessions(self.records)

    def test_create_pending_encounter_validates_thread_metadata_first(self) -> None:
        created = create_thread(self.records, goal="validate metadata")
        metadata_path = threads_root(self.records) / created.id / "thread.json"
        metadata_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            create_pending_encounter(self.records, created.id)

    def test_load_thread_rejects_pending_latest_encounter_in_metadata(self) -> None:
        thread = create_thread(self.records, goal="referenced")
        pending = create_pending_encounter(self.records, thread.id)
        metadata_path = threads_root(self.records) / thread.id / "thread.json"
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload["latest_encounter"] = pending.name
        metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            load_thread(self.records, thread.id)

    def test_finalize_rollback_restores_pending_on_metadata_failure(self) -> None:
        thread = create_thread(self.records, goal="rollback")
        pending = create_pending_encounter(self.records, thread.id)
        pending.write_text(
            _pending_checkpoint_body(title="Science: Rollback", next_step="Retry."),
            encoding="utf-8",
        )
        with mock.patch(
            "socratic_learn.thread_storage._save_thread_metadata",
            side_effect=IntegrityError("metadata publish failed"),
        ):
            with self.assertRaises(IntegrityError):
                finalize_thread_encounter(self.records, thread.id, pending)
        self.assertTrue(pending.is_file())
        sessions = threads_root(self.records) / thread.id / "sessions"
        finals = [
            path
            for path in sessions.iterdir()
            if path.is_file() and not path.name.startswith("pending-")
        ]
        self.assertEqual([], finals)

    def test_legacy_import_preserves_source_bytes_including_crlf(self) -> None:
        legacy = self.records / "sessions" / "crlf-legacy.md"
        source = (
            _valid_session_body(
                title="History: Ancient trade routes",
                next_step="Map one additional route.",
                session_date="2026-01-15",
            ).replace("\n", "\r\n")
        ).encode("utf-8")
        legacy.write_bytes(source)
        imported = import_legacy_session(self.records, legacy)
        copied = (
            threads_root(self.records) / imported.id / "sessions" / imported.latest_encounter
        )
        self.assertEqual(source, copied.read_bytes())

    def test_import_malformed_legacy_leaves_no_thread(self) -> None:
        legacy = self.records / "sessions" / "broken.md"
        legacy.write_text("broken", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            import_legacy_session(self.records, legacy)
        self.assertEqual([], list(list_threads(self.records)))

    def test_personal_and_project_records_stay_isolated(self) -> None:
        other = self.records.parent / "other-records"
        other.mkdir()
        (other / "sessions").mkdir()
        first = create_thread(self.records, goal="personal")
        second = create_thread(other, goal="project")
        self.assertEqual([first.id], [item.id for item in list_threads(self.records)])
        self.assertEqual([second.id], [item.id for item in list_threads(other)])

    def test_parse_rejects_nonempty_preamble(self) -> None:
        text = _valid_session_body(title="Math: Preamble", next_step="Continue.")
        text = text.replace("\n\n## Outcome", "\nPreamble noise\n\n## Outcome", 1)
        with self.assertRaises(IntegrityError):
            parse_session_record(text)

    def test_parse_allows_html_comment_preamble(self) -> None:
        text = _valid_session_body(
            title="Math: Template comments",
            next_step="Continue.",
        ).replace(
            "\n\n## Outcome",
            "\n<!-- Complete the required sections below. -->\n\n## Outcome",
            1,
        )
        parsed = parse_session_record(text)
        self.assertEqual("Math: Template comments", parsed.refined_title)

    def test_timestamp_fields_reject_non_string_types(self) -> None:
        with self.assertRaises(IntegrityError):
            parse_utc_timestamp(1735689600)
        with self.assertRaises(IntegrityError):
            parse_utc_timestamp(True)
        with self.assertRaises(IntegrityError):
            parse_utc_timestamp(None)

    def test_load_thread_rejects_non_string_timestamp_fields(self) -> None:
        created = create_thread(self.records, goal="typed")
        metadata_path = threads_root(self.records) / created.id / "thread.json"
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload["updated_at"] = 123
        metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            load_thread(self.records, created.id)

    def test_legacy_import_rejects_foreign_records_root(self) -> None:
        other = self.records.parent / "other-records"
        other.mkdir()
        (other / "sessions").mkdir()
        foreign = other / "sessions" / "foreign.md"
        foreign.write_text("broken", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            import_legacy_session(self.records, foreign)

    @unittest.skipUnless(_symlinks_supported(), "symlinks unavailable")
    def test_load_thread_rejects_dangling_metadata_symlink(self) -> None:
        created = create_thread(self.records, goal="dangling")
        metadata_path = threads_root(self.records) / created.id / "thread.json"
        metadata_path.unlink()
        metadata_path.symlink_to(self.records.parent / "missing-thread.json")
        with self.assertRaises(IntegrityError):
            load_thread(self.records, created.id)

    @unittest.skipUnless(_symlinks_supported(), "symlinks unavailable")
    def test_create_rejects_symlink_records_root(self) -> None:
        records_link = self.records.parent / "records-link"
        try:
            records_link.symlink_to(self.records, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks unavailable: {error}")
        with self.assertRaises(IntegrityError):
            create_thread(records_link, goal="blocked")

    def test_parse_session_record_accepts_legacy_em_dash_separator(self) -> None:
        text = _valid_session_body(
            title="Logic: Legacy title",
            next_step="Review one gap.",
        ).replace(" - Logic:", " — Logic:", 1)
        record = parse_session_record(text)
        self.assertEqual("Logic: Legacy title", record.refined_title)

    def test_parse_session_record_requires_subject_focus_title(self) -> None:
        parse_session_record(
            _valid_session_body(
                title="Philosophy: Plato's cave allegory",
                next_step="Revisit the shadow/image distinction.",
            )
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(
                _valid_session_body(
                    title="Philosophy only",
                    next_step="Continue tomorrow.",
                )
            )

    def test_parse_rejects_literal_subject_focus_placeholder(self) -> None:
        with self.assertRaises(IntegrityError):
            parse_session_record(
                _valid_session_body(
                    title="Subject: Specific focus",
                    next_step="Continue.",
                )
            )

    def test_html_comments_do_not_make_required_section_nonempty(self) -> None:
        text = _valid_session_body(
            title="Logic: Comment-only evidence",
            next_step="Continue.",
        ).replace(
            "## Outcome\nExplain one observable result.",
            "## Outcome\n<!-- generated placeholder -->",
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(text)

    def test_hard_kill_pending_checkpoint_is_recovered_on_next_load(self) -> None:
        thread = create_thread(self.records, goal="recover after hard kill")
        pending = create_pending_encounter(self.records, thread.id)
        pending.write_text(
            _pending_checkpoint_body(
                title="Logic: Recovered checkpoint",
                next_step="Resume from recovered evidence.",
            ),
            encoding="utf-8",
        )
        mark_thread_incomplete(self.records, thread.id)

        recovery = recover_pending_encounters(self.records, thread.id)

        recovered = load_thread(self.records, thread.id)
        self.assertEqual(1, recovery.promoted)
        self.assertEqual(0, recovery.discarded)
        self.assertEqual("complete", recovered.status)
        self.assertEqual("Logic: Recovered checkpoint", recovered.refined_title)
        self.assertFalse(pending.exists())

    def test_orphan_empty_and_malformed_pending_files_are_discarded(self) -> None:
        thread = create_thread(self.records, goal="clean stale pending")
        empty = create_pending_encounter(self.records, thread.id)
        malformed = create_pending_encounter(self.records, thread.id)
        malformed.write_text("not a session", encoding="utf-8")
        mark_thread_incomplete(self.records, thread.id)

        recovery = recover_pending_encounters(self.records, thread.id)

        self.assertEqual(0, recovery.promoted)
        self.assertEqual(2, recovery.discarded)
        self.assertEqual(0, recovery.unsafe)
        self.assertEqual([], list(empty.parent.iterdir()))

    def test_active_empty_and_valid_pending_checkpoints_are_not_recovered(self) -> None:
        thread = create_thread(self.records, goal="concurrent launch")
        empty, empty_lease = create_pending_encounter_with_lease(
            self.records,
            thread.id,
        )
        valid, valid_lease = create_pending_encounter_with_lease(
            self.records,
            thread.id,
        )
        valid.write_text(
            _pending_checkpoint_body(
                title="Logic: Active checkpoint",
                next_step="Wait for the active launcher.",
            ),
            encoding="utf-8",
        )
        self.addCleanup(empty_lease.release)
        self.addCleanup(valid_lease.release)

        recovery = recover_pending_encounters(self.records, thread.id)

        self.assertEqual(2, recovery.active)
        self.assertEqual(0, recovery.promoted)
        self.assertEqual(0, recovery.discarded)
        self.assertTrue(empty.is_file())
        self.assertTrue(valid.is_file())
        self.assertIsNone(load_thread(self.records, thread.id).latest_encounter)

        empty_lease.release()
        valid_lease.release()
        recovered = recover_pending_encounters(self.records, thread.id)
        self.assertEqual(1, recovered.promoted)
        self.assertEqual(1, recovered.discarded)
        self.assertFalse(empty.exists())
        self.assertFalse(valid.exists())

    def test_pending_lease_is_released_when_owner_process_exits(self) -> None:
        thread = create_thread(self.records, goal="crashed launcher")
        pending = create_pending_encounter(self.records, thread.id)
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            "from socratic_learn.threads import try_acquire_pending_lease\n"
            "lease = try_acquire_pending_lease("
            "Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))\n"
            "assert lease is not None\n"
            "print('locked', flush=True)\n"
            "sys.stdin.read()\n"
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(self.records),
                thread.id,
                str(pending),
            ],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        assert process.stdout is not None
        self.assertEqual("locked", process.stdout.readline().strip())
        self.assertEqual(
            1,
            recover_pending_encounters(self.records, thread.id).active,
        )

        process.kill()
        process.wait(timeout=10)
        assert process.stdin is not None
        process.stdin.close()
        process.stdout.close()

        acquired = try_acquire_pending_lease(
            self.records,
            thread.id,
            pending,
        )
        self.assertIsNotNone(acquired)
        assert acquired is not None
        acquired.release()

    def test_recovery_is_noop_when_another_process_already_recovered_pending(
        self,
    ) -> None:
        thread = create_thread(self.records, goal="concurrent recovery")
        pending = create_pending_encounter(self.records, thread.id)
        pending.write_text(
            _pending_checkpoint_body(
                title="Logic: Recovered elsewhere",
                next_step="Continue safely.",
            ),
            encoding="utf-8",
        )
        real_acquire = thread_storage_module._try_acquire_lease_path

        def recover_before_acquire(lease_path: Path) -> PendingLease | None:
            finalize_thread_encounter(self.records, thread.id, pending)
            return real_acquire(lease_path)

        with mock.patch.object(
            thread_storage_module,
            "_try_acquire_lease_path",
            side_effect=recover_before_acquire,
        ):
            recovery = recover_pending_encounters(self.records, thread.id)

        self.assertEqual(0, recovery.promoted)
        self.assertEqual(0, recovery.discarded)
        self.assertEqual(0, recovery.unsafe)
        self.assertEqual(
            "Logic: Recovered elsewhere",
            load_thread(self.records, thread.id).refined_title,
        )

    def test_posix_lease_file_is_removed_before_unlock(self) -> None:
        lease_path = self.records / "lease-order"
        descriptor = os.open(lease_path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        lease = PendingLease(lease_path, descriptor)
        events: list[str] = []

        with (
            mock.patch.object(thread_storage_module.os, "name", "posix"),
            mock.patch.object(
                lease,
                "_remove_if_same",
                side_effect=lambda _identity: events.append("remove"),
            ),
            mock.patch.object(
                thread_storage_module,
                "_unlock_lease_descriptor",
                side_effect=lambda _descriptor: events.append("unlock"),
            ),
        ):
            lease.release(remove=True)

        self.assertEqual(["remove", "unlock"], events)
        lease_path.unlink()

    @unittest.skipUnless(_symlinks_supported(), "symlinks unavailable")
    def test_orphan_pending_symlink_is_untouched_and_reported(self) -> None:
        thread = create_thread(self.records, goal="unsafe pending")
        sessions = threads_root(self.records) / thread.id / "sessions"
        outside = self.records.parent / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        pending = sessions / "pending-unsafe.md"
        pending.symlink_to(outside)

        recovery = recover_pending_encounters(self.records, thread.id)

        self.assertEqual(1, recovery.unsafe)
        self.assertTrue(pending.is_symlink())
        self.assertEqual("outside\n", outside.read_text(encoding="utf-8"))

    def test_parse_rejects_duplicate_headings(self) -> None:
        text = _valid_session_body(
            title="Math: Duplicate headings",
            next_step="Fix the record.",
        )
        text = text.replace("## Outcome\n", "## Outcome\n## Outcome\n", 1)
        with self.assertRaises(IntegrityError):
            parse_session_record(text)

    def test_parse_rejects_reordered_headings(self) -> None:
        lines = _valid_session_body(
            title="Math: Order",
            next_step="Continue.",
        ).splitlines()
        swapped = []
        for line in lines:
            if line == "## Diagnostic evidence":
                swapped.append("## Learning route")
            elif line == "## Learning route":
                swapped.append("## Diagnostic evidence")
            else:
                swapped.append(line)
        with self.assertRaises(IntegrityError):
            parse_session_record("\n".join(swapped))

    def test_parse_rejects_invalid_calendar_date(self) -> None:
        with self.assertRaises(IntegrityError):
            parse_session_record(
                _valid_session_body(
                    title="Math: Bad date",
                    next_step="Continue.",
                    session_date="2026-02-30",
                )
            )

    def test_threads_root_rejects_symlink_conflict(self) -> None:
        root = threads_root(self.records)
        root.mkdir(parents=True)
        root.rmdir()
        link = threads_root(self.records)
        outside = self.records.parent / "outside-threads"
        outside.mkdir()
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks unavailable: {error}")
        with self.assertRaises(IntegrityError):
            create_thread(self.records, goal="blocked")

    @unittest.skipUnless(_symlinks_supported(), "symlinks unavailable")
    def test_pending_encounter_rejects_symlink_destination(self) -> None:
        thread = create_thread(self.records, goal="links")
        sessions = threads_root(self.records) / thread.id / "sessions"
        outside = self.records.parent / "outside.md"
        outside.write_text("external\n", encoding="utf-8")
        link = sessions / "pending-link.md"
        link.symlink_to(outside)
        with self.assertRaises(IntegrityError):
            finalize_thread_encounter(self.records, thread.id, link)


class VersionedSessionRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.records = Path(self.temporary.name).resolve(strict=True) / "records"
        self.records.mkdir()
        (self.records / "sessions").mkdir()

    def test_valid_v2_parses_and_finalizes(self) -> None:
        body = _valid_v2_session_body(
            title="Logic: Versioned memory",
            next_step="- [G1] Revisit the recorded gap.",
        )
        parsed = parse_session_record(body)
        self.assertEqual("Logic: Versioned memory", parsed.refined_title)
        self.assertEqual("- [G1] Revisit the recorded gap.", parsed.next_step)

        thread = create_thread(self.records, goal="versioned memory")
        pending = create_pending_encounter(self.records, thread.id)
        pending.write_text(body, encoding="utf-8")
        finalized = finalize_thread_encounter(self.records, thread.id, pending)
        self.assertEqual("Logic: Versioned memory", finalized.refined_title)

    def test_legacy_session_body_still_parses(self) -> None:
        parse_session_record(
            _valid_session_body(
                title="Logic: Legacy record",
                next_step="Continue tomorrow.",
            )
        )

    def test_valid_v2_all_sentinel_sections_parse(self) -> None:
        body = _valid_v2_all_sentinel_session_body(title="Logic: Empty structured sections")
        parsed = parse_session_record(body)
        self.assertEqual("Logic: Empty structured sections", parsed.refined_title)
        self.assertEqual("- None.", parsed.next_step)

    def test_v2_rejects_established_e_without_application_e(self) -> None:
        body = _valid_v2_session_body(
            title="Logic: Missing application",
            next_step="- [G1] Continue.",
        ).replace(
            "## Concepts established\n- [E1] Named the corrected concept with support.",
            "## Concepts established\n- [E1] First concept.\n- [E2] Second concept.",
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(body)

    def test_v2_rejects_application_e_without_established_e(self) -> None:
        body = _valid_v2_session_body(
            title="Logic: Missing concept",
            next_step="- [G1] Continue.",
        ).replace(
            "- [E1] Task: Apply the concept",
            "- [E2] Task: Apply the concept",
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(body)

    def test_v2_rejects_duplicate_e_p_and_g_identifiers(self) -> None:
        cases = [
            "## Concepts established\n- [E1] First.\n- [E1] Duplicate.",
            "## Application evidence\n"
            "- [E1] Task: A | Conditions: B | Result: C | Support: D | Limits: E\n"
            "- [E1] Task: F | Conditions: G | Result: H | Support: I | Limits: J",
            "## Application evidence\n"
            "- [E1] Task: A | Conditions: B | Result: C | Support: D | Limits: E\n"
            "- [P1] Task: A | Conditions: B | Result: C | Support: D | Limits: E\n"
            "- [P1] Task: F | Conditions: G | Result: H | Support: I | Limits: J",
            "## Remaining gaps\n- [G1] First gap.\n- [G1] Duplicate gap.",
        ]
        for replacement in cases:
            heading, _, content = replacement.partition("\n")
            body = _valid_v2_session_body(
                title="Logic: Duplicate ids",
                next_step="- [G1] Continue.",
            )
            old = {
                "## Concepts established": "## Concepts established\n"
                "- [E1] Named the corrected concept with support.",
                "## Application evidence": "## Application evidence\n"
                "- [E1] Task: Apply the concept | Conditions: One problem | "
                "Result: Correct answer | Support: Tutor hint | Limits: Single topic",
                "## Remaining gaps": "## Remaining gaps\n"
                "- [G1] One gap remains explicitly recorded.",
            }[heading]
            with self.assertRaises(IntegrityError, msg=replacement):
                parse_session_record(body.replace(old, f"{heading}\n{content}"))

    def test_v2_rejects_p_outside_application_evidence(self) -> None:
        body = _valid_v2_session_body(
            title="Logic: Progress outside application",
            next_step="- [G1] Continue.",
        ).replace(
            "## Concepts established\n- [E1] Named the corrected concept with support.",
            "## Concepts established\n- [P1] Wrong placement.",
        )
        with self.assertRaisesRegex(
            IntegrityError,
            "progress identifiers are only allowed in application evidence",
        ):
            parse_session_record(body)

    def test_v2_rejects_malformed_or_empty_application_fields(self) -> None:
        malformed = [
            "- [E1] Conditions: One | Task: Apply | Result: R | Support: S | Limits: L",
            "- [E1] Task:  | Conditions: One | Result: R | Support: S | Limits: L",
            "- [E1] Task: Apply | Conditions: One | Result: R | Support: S",
        ]
        for application in malformed:
            body = _valid_v2_session_body(
                title="Logic: Malformed application",
                next_step="- [G1] Continue.",
            ).replace(
                "- [E1] Task: Apply the concept | Conditions: One problem | "
                "Result: Correct answer | Support: Tutor hint | Limits: Single topic",
                application,
            )
            with self.assertRaises(IntegrityError, msg=application):
                parse_session_record(body)

    def test_v2_rejects_stale_next_step_g_and_next_step_without_g(self) -> None:
        stale = _valid_v2_session_body(
            title="Logic: Stale next step",
            next_step="- [G2] Not in remaining gaps.",
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(stale)

        without_gap = _valid_v2_session_body(
            title="Logic: Freeform next step",
            next_step="Continue without a gap identifier.",
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(without_gap)

    def test_v2_rejects_sentinel_mixed_with_structured_bullets(self) -> None:
        replacements = {
            "## Concepts established": (
                "## Concepts established\n- None.\n"
                "- [E1] Concept after sentinel."
            ),
            "## Application evidence": (
                "## Application evidence\n- None collected.\n"
                "- [E1] Task: A | Conditions: B | Result: C | Support: D | Limits: E"
            ),
            "## Remaining gaps": (
                "## Remaining gaps\n- None.\n- [G1] Gap after sentinel."
            ),
            "## Next step": "## Next step\n- None.\n- [G1] Step after sentinel.",
        }
        for heading, replacement in replacements.items():
            body = _valid_v2_session_body(
                title="Logic: Mixed sentinel",
                next_step="- [G1] Continue.",
            )
            old = {
                "## Concepts established": "## Concepts established\n"
                "- [E1] Named the corrected concept with support.",
                "## Application evidence": "## Application evidence\n"
                "- [E1] Task: Apply the concept | Conditions: One problem | "
                "Result: Correct answer | Support: Tutor hint | Limits: Single topic",
                "## Remaining gaps": "## Remaining gaps\n"
                "- [G1] One gap remains explicitly recorded.",
                "## Next step": "## Next step\n- [G1] Continue.",
            }[heading]
            with self.assertRaises(IntegrityError, msg=heading):
                parse_session_record(body.replace(old, replacement))

    def test_v2_rejects_mixed_or_mismatched_sentinels(self) -> None:
        cases = [
            _valid_v2_session_body(
                title="Logic: Mixed concept sentinel",
                next_step="- [G1] Continue.",
            ).replace(
                "- [E1] Named the corrected concept with support.",
                "- None.",
            ),
            _valid_v2_session_body(
                title="Logic: Mixed application sentinel",
                next_step="- [G1] Continue.",
            ).replace(
                "## Application evidence\n"
                "- [E1] Task: Apply the concept | Conditions: One problem | "
                "Result: Correct answer | Support: Tutor hint | Limits: Single topic",
                "## Application evidence\n- None collected.",
            ),
            _valid_v2_session_body(
                title="Logic: Wrong gap sentinel",
                next_step="- [G1] Continue.",
            ).replace(
                "## Remaining gaps\n- [G1] One gap remains explicitly recorded.",
                "## Remaining gaps\n- None collected.",
            ),
        ]
        for body in cases:
            with self.assertRaises(IntegrityError):
                parse_session_record(body)

    def test_v2_rejects_invalid_record_version_comments(self) -> None:
        malformed = _valid_v2_session_body(
            title="Logic: Malformed marker",
            next_step="- [G1] Continue.",
        ).replace(
            SESSION_RECORD_VERSION_MARKER,
            "<!-- socratic-learn-record-version:2 -->",
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(malformed)

        unknown_in_body = _valid_v2_session_body(
            title="Logic: Body version comment",
            next_step="- [G1] Continue.",
        ).replace(
            "## Sources\nNo external sources were used.",
            "## Sources\n<!-- socratic-learn-record-version: 3 -->\n"
            "No external sources were used.",
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(unknown_in_body)

        extra_comment = _valid_v2_session_body(
            title="Logic: Extra version comment",
            next_step="- [G1] Continue.",
        ).replace(
            "## Sources\nNo external sources were used.",
            "## Sources\n<!-- socratic-learn-record-version: 1 -->\n"
            "No external sources were used.",
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(extra_comment)

        duplicate = _valid_v2_session_body(
            title="Logic: Duplicate marker",
            next_step="- [G1] Continue.",
        ).replace(
            SESSION_RECORD_VERSION_MARKER,
            f"{SESSION_RECORD_VERSION_MARKER}\n{SESSION_RECORD_VERSION_MARKER}",
        )
        with self.assertRaises(IntegrityError):
            parse_session_record(duplicate)

    def test_finalize_rejects_unmarked_pending_checkpoint(self) -> None:
        body = _valid_session_body(
            title="Logic: Unmarked pending",
            next_step="Continue tomorrow.",
        )
        parse_session_record(body)

        thread = create_thread(self.records, goal="unmarked pending")
        pending = create_pending_encounter(self.records, thread.id)
        pending.write_text(body, encoding="utf-8")
        with self.assertRaisesRegex(
            IntegrityError,
            "session record version marker is required for new checkpoints",
        ):
            finalize_thread_encounter(self.records, thread.id, pending)

    def test_v2_rejects_non_ascii_identifier_digits(self) -> None:
        body = _valid_v2_session_body(
            title="Logic: Unicode digit",
            next_step="- [G1] Continue.",
        ).replace("[E1]", "[E1٠]", 1)
        with self.assertRaises(IntegrityError):
            parse_session_record(body)


class WorkspaceThreadRootTests(unittest.TestCase):
    def test_prepare_here_workspace_creates_threads_directory(self) -> None:
        from socratic_learn.workspace import prepare_here_workspace

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary).resolve(strict=True) / "project"
            project.mkdir()
            workspace = prepare_here_workspace(project)
            threads = workspace.records / "threads"
            self.assertTrue(threads.is_dir())
            if os.name != "nt":
                self.assertFalse(threads.is_symlink())
                mode = threads.stat().st_mode
                self.assertTrue(stat.S_ISDIR(mode))