from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from socratic_learn.errors import IntegrityError
from socratic_learn.manifest import SESSION_HEADINGS
from socratic_learn.threads import (
    THREAD_SCHEMA_VERSION,
    ThreadRecord,
    create_pending_encounter,
    create_thread,
    discover_legacy_sessions,
    finalize_thread_encounter,
    import_legacy_session,
    list_threads,
    load_thread,
    mark_thread_incomplete,
    parse_session_record,
    threads_root,
)


def _valid_session_body(*, title: str, next_step: str) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
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
        self.assertEqual("understand dot products", payload["provisional_title"])
        self.assertIsNone(payload["refined_title"])
        self.assertEqual("active", payload["status"])
        self.assertIsNone(payload["latest_encounter"])
        self.assertEqual("", payload["next_step"])

    def test_create_pending_encounter_is_exclusive_and_immutable(self) -> None:
        thread = create_thread(self.records, goal="series practice")
        first = create_pending_encounter(self.records, thread.id)
        second = create_pending_encounter(self.records, thread.id)
        self.assertNotEqual(first, second)
        first.write_text("first encounter\n", encoding="utf-8")
        with self.assertRaises(IntegrityError):
            create_pending_encounter(self.records, thread.id, path=first)
        self.assertEqual("first encounter\n", first.read_text(encoding="utf-8"))

    def test_finalize_refines_metadata_from_heading_and_next_step(self) -> None:
        thread = create_thread(self.records, goal="platonic forms")
        encounter = create_pending_encounter(self.records, thread.id)
        encounter.write_text(
            _valid_session_body(
                title="Philosophy: Plato's cave allegory",
                next_step="Predict how the cave metaphor changes with a second source.",
            ),
            encoding="utf-8",
        )

        finalized = finalize_thread_encounter(self.records, thread.id, encounter)
        self.assertEqual("Philosophy: Plato's cave allegory", finalized.refined_title)
        self.assertEqual(encounter.name, finalized.latest_encounter)
        self.assertEqual(
            "Predict how the cave metaphor changes with a second source.",
            finalized.next_step,
        )
        self.assertEqual("complete", finalized.status)
        self.assertGreaterEqual(finalized.updated_at, thread.updated_at)

        persisted = load_thread(self.records, thread.id)
        self.assertEqual(finalized, persisted)

    def test_finalize_rejects_malformed_record_without_mutating_metadata(self) -> None:
        thread = create_thread(self.records, goal="keep metadata")
        encounter = create_pending_encounter(self.records, thread.id)
        encounter.write_text("# Session: not-a-valid-heading\n\n## Next step\nx\n", encoding="utf-8")
        before = load_thread(self.records, thread.id)

        with self.assertRaises(IntegrityError):
            finalize_thread_encounter(self.records, thread.id, encounter)

        after = load_thread(self.records, thread.id)
        self.assertEqual(before, after)

    def test_mark_thread_incomplete_preserves_provisional_title(self) -> None:
        thread = create_thread(self.records, goal="interrupted study")
        incomplete = mark_thread_incomplete(self.records, thread.id)
        self.assertEqual("incomplete", incomplete.status)
        self.assertEqual(thread.provisional_title, incomplete.provisional_title)
        self.assertIsNone(incomplete.refined_title)

    def test_list_threads_sorts_by_updated_at_desc_then_id(self) -> None:
        first = create_thread(self.records, goal="alpha")
        second = create_thread(self.records, goal="beta")
        encounter = create_pending_encounter(self.records, second.id)
        encounter.write_text(
            _valid_session_body(
                title="Math: Ordering practice",
                next_step="Compare two more dot products.",
            ),
            encoding="utf-8",
        )
        finalize_thread_encounter(self.records, second.id, encounter)

        summaries = list_threads(self.records)
        self.assertEqual([second.id, first.id], [item.id for item in summaries])

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
        self.assertEqual(legacy, discovered[0].path)
        self.assertEqual("Vector dot products", discovered[0].display_title)
        self.assertTrue(discovered[0].next_step)
        self.assertEqual(before, legacy.read_bytes())

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
        self.assertEqual(legacy.read_text(encoding="utf-8"), copied.read_text(encoding="utf-8"))
        self.assertEqual("Vector dot products", imported.provisional_title)

    def test_personal_and_project_records_stay_isolated(self) -> None:
        other = self.records.parent / "other-records"
        other.mkdir()
        (other / "sessions").mkdir()
        first = create_thread(self.records, goal="personal")
        second = create_thread(other, goal="project")
        self.assertEqual([first.id], [item.id for item in list_threads(self.records)])
        self.assertEqual([second.id], [item.id for item in list_threads(other)])

    def test_parse_session_record_requires_subject_focus_title(self) -> None:
        parsed = parse_session_record(
            _valid_session_body(
                title="Philosophy: Plato's cave allegory",
                next_step="Revisit the shadow/image distinction.",
            )
        )
        self.assertEqual("Philosophy: Plato's cave allegory", parsed.refined_title)
        self.assertEqual("Revisit the shadow/image distinction.", parsed.next_step)

        with self.assertRaises(IntegrityError):
            parse_session_record(
                _valid_session_body(
                    title="Philosophy only",
                    next_step="Continue tomorrow.",
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