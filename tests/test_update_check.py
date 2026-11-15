from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from socratic_learn.update_check import check_for_update


class UpdateCheckTests(unittest.TestCase):
    def test_new_release_is_cached_and_reported_for_pipx(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            calls: list[tuple[str, float]] = []

            def fetch(url: str, timeout: float) -> object:
                calls.append((url, timeout))
                return {"info": {"version": "0.2.0"}}

            notice = check_for_update(
                "0.1.0",
                home=home,
                environ={},
                now=100_000.0,
                fetch_json=fetch,
            )

            self.assertIn("Socratic Learn 0.2.0", notice or "")
            self.assertIn("pipx upgrade socratic-learn", notice or "")
            self.assertEqual(1, len(calls))
            cache = json.loads((home / "update-check.json").read_text("utf-8"))
            self.assertEqual("0.2.0", cache["latest_version"])

    def test_fresh_cache_avoids_network_and_still_reports_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / "update-check.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "checked_at": 100_000.0,
                        "latest_version": "0.2.0",
                    }
                ),
                encoding="utf-8",
            )

            notice = check_for_update(
                "0.1.0",
                home=home,
                environ={},
                now=100_001.0,
                fetch_json=lambda *_: self.fail("fresh cache must avoid network"),
            )

            self.assertIn("0.2.0", notice or "")

    def test_npm_launch_gets_npx_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            notice = check_for_update(
                "0.1.0",
                home=Path(temporary),
                environ={"SOCRATIC_DISTRIBUTION_CHANNEL": "npm"},
                now=100_000.0,
                fetch_json=lambda *_: {"info": {"version": "0.2.0"}},
            )

            self.assertIn("npx @socraticlearn/cli@latest", notice or "")

    def test_opt_out_skips_network_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            notice = check_for_update(
                "0.1.0",
                home=home,
                environ={"SOCRATIC_NO_UPDATE_CHECK": "1"},
                now=100_000.0,
                fetch_json=lambda *_: self.fail("opt-out must avoid network"),
            )

            self.assertIsNone(notice)
            self.assertFalse((home / "update-check.json").exists())

    def test_network_failure_is_silent_and_cached_for_the_day(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)

            def fail(*_: object) -> object:
                raise TimeoutError("offline")

            self.assertIsNone(
                check_for_update(
                    "0.1.0",
                    home=home,
                    environ={},
                    now=100_000.0,
                    fetch_json=fail,
                )
            )
            cache = json.loads((home / "update-check.json").read_text("utf-8"))
            self.assertIsNone(cache["latest_version"])

    def test_equal_invalid_and_older_versions_do_not_report(self) -> None:
        for latest in ("0.1.0", "not-a-version", "0.0.9"):
            with self.subTest(latest=latest):
                with tempfile.TemporaryDirectory() as temporary:
                    self.assertIsNone(
                        check_for_update(
                            "0.1.0",
                            home=Path(temporary),
                            environ={},
                            now=100_000.0,
                            fetch_json=lambda *_: {"info": {"version": latest}},
                        )
                    )


if __name__ == "__main__":
    unittest.main()
