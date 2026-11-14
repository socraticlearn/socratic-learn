from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from socratic_learn.errors import IntegrityError
from socratic_learn.manifest import SESSION_HEADINGS
from socratic_learn.thread_schema import parse_utc_timestamp
from socratic_learn.threads import (
    THREAD_SCHEMA_VERSION,
    create_pending_encounter,
    create_thread,
    discard_pending_encounter,
    discover_legacy_sessions,
    finalize_thread_encounter,
    import_legacy_session,
    list_threads,
    load_thread,
    mark_thread_incomplete,
    parse_session_record,
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
    lines = [f"# Session: {today} — {title}", ""]
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
            _valid_session_body(
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
            _valid_session_body(
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
            _valid_session_body(title="Physics: Motion", next_step="Continue A."),
            encoding="utf-8",
        )
        pending_b.write_text(
            _valid_session_body(title="Physics: Energy", next_step="Continue B."),
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
        body = _valid_session_body(title="Chemistry: Bonds", next_step="Continue.")
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
            _valid_session_body(title="Science: Rollback", next_step="Retry."),
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