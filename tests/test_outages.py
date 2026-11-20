import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

import outages


class OutageTests(unittest.TestCase):
    def test_statuspage_and_grok_parsers(self):
        body = json.dumps({"incidents": [{
            "id": "abc", "name": "Service issue", "status": "resolved", "impact": "major",
            "components": [{"name": "API"}],
        }]}).encode()
        self.assertEqual(outages.parse("github", body), [
            outages.incident("abc", "Service issue", "resolved", 2, ["API"]),
        ])
        rss = b"""<rss><channel><title>Status</title>
          <item><guid>1</guid><title>[Grok Bot] Delay</title><category>active</category></item>
          <item><guid>2</guid><title>[Grok Web] Delay</title><category>active</category></item>
        </channel></rss>"""
        self.assertEqual(outages.parse("grok", rss), [
            outages.incident("1", "Delay", "active"),
        ])

    def test_alert_lifecycle_and_missing_incident(self):
        incident = outages.incident("abc", "Service issue", "active", 1)
        state, _ = outages.merge({}, [], 1)
        state, changes = outages.merge(state, [incident], 2)
        self.assertEqual(changes, ["active"])
        state, changes = outages.merge(state, [incident], 3)
        self.assertEqual(changes, [])
        state, changes = outages.merge(state, [], 4)
        self.assertEqual(changes, [])
        self.assertEqual(state["events"][0]["confirmed"], 3)
        view = outages.view({"alerts": ["github"], "sources": {"github": state}})
        github = next(row for row in view["rows"] if row["id"] == "github")
        self.assertTrue(github["cachedActive"])
        self.assertFalse(github["active"])
        self.assertFalse(github["healthKnown"])
        severity = dict(incident, severity=2)
        state, changes = outages.merge(state, [severity], 5)
        self.assertEqual(changes, ["escalated"])
        resolved = dict(severity, state="resolved")
        state, changes = outages.merge(state, [resolved], 6)
        self.assertEqual(changes, ["resolved"])
        _, changes = outages.merge(state, [resolved], 7)
        self.assertEqual(changes, [])

    def test_bad_feed_and_deadline_keep_good_results_and_old_incidents(self):
        incident = outages.incident("abc", "Old issue", "active")
        old_cache, _ = outages.merge({}, [incident], 1)
        state = {"sources": {"github": old_cache, "claude": old_cache}}
        release = Event()

        def fetch_source(key):
            if key == "github":
                raise http.client.IncompleteRead(b"private response")
            if key == "claude":
                release.wait(3)
            return []

        catalog = {key: outages.SOURCES[key] for key in ("github", "claude", "slack")}
        log = io.StringIO()
        try:
            with patch.multiple(outages, SOURCES=catalog, read_source=fetch_source,
                                CHECK_DEADLINE=0.3), contextlib.redirect_stderr(log):
                outages.refresh(state, True)
        finally:
            release.set()
        self.assertEqual(state["sources"]["slack"]["checked"], state["attempt"])
        for key in ("github", "claude"):
            feed = state["sources"][key]
            self.assertEqual(feed["failures"], 1)
            self.assertEqual(feed["events"][0]["id"], "abc")
            self.assertGreater(feed["retry"], state["attempt"])
        self.assertIn("github: IncompleteRead", log.getvalue())
        self.assertIn("claude: refresh deadline exceeded", log.getvalue())
        self.assertNotIn("private response", log.getvalue())

    def test_cli_exits_and_saves_while_network_check_is_stuck(self):
        code = """
import time
import outages

outages.SOURCES = {key: outages.SOURCES[key] for key in ("github", "slack")}
outages.CHECK_DEADLINE = 0.2

def read_source(key):
    if key == "github":
        time.sleep(10)
    return []

outages.read_source = read_source
outages.main(["refresh"])
"""
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [sys.executable, "-B", "-c", code],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "XDG_STATE_HOME": folder},
                capture_output=True, text=True, check=True, timeout=3,
            )
            state = json.loads((Path(folder) / "omarchy-service-outages/state.json").read_bytes())
            self.assertEqual(state["sources"]["slack"]["checked"], state["attempt"])
            self.assertEqual(state["sources"]["github"]["failures"], 1)
            self.assertIn("github: refresh deadline exceeded", result.stderr)

    def test_successful_feed_does_not_wait_for_probe(self):
        release = Event()
        finished = Event()

        def stalled_probe(_):
            release.wait(3)
            finished.set()
            return False

        state = {"probe": True}
        try:
            with patch.multiple(outages, SOURCES={"github": outages.SOURCES["github"]},
                                read_source=lambda _: [], probe=stalled_probe):
                outages.refresh(state, True)
                self.assertFalse(finished.is_set())
        finally:
            release.set()
        self.assertEqual(state["connection"], "reachable")

    def test_failed_save_preserves_existing_json(self):
        with tempfile.TemporaryDirectory() as folder:
            state_dir = Path(folder)
            saved = state_dir / "state.json"
            saved.write_text('{"country": "US"}')
            with patch.object(outages, "STATE", state_dir):
                with self.assertRaises(TypeError):
                    outages.save({"unserialisable": object()})
            self.assertEqual(json.loads(saved.read_bytes()), {"country": "US"})
            self.assertEqual(list(state_dir.iterdir()), [saved])


if __name__ == "__main__":
    unittest.main()
