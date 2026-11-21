import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
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

    def test_custom_link_and_country_persist_and_feed_can_be_enabled(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(outages, "STATE", Path(folder)):
            def run(*args):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    self.assertIsNone(outages.main(args))
                return json.loads(output.getvalue())

            self.assertEqual(run("country", "DE")["country"], "DE")
            self.assertIn("inputError", run("add-custom", "Invalid", "file:///etc/passwd"))
            with patch.object(outages, "read_custom_feed", side_effect=ValueError):
                result = run("add-custom", "My provider", "https://status.example.org/")
            key = result["added"]
            row = next(row for row in result["rows"] if row["id"] == key)
            self.assertEqual(row["kind"], "link")
            self.assertTrue(row["favourite"])
            self.assertNotIn("alert", row)
            self.assertFalse(next(row for row in result["rows"] if row["id"] == "xfinity")["inDirectory"])

            baseline = outages.incident("old", "Existing incident", "active")
            with patch.object(outages, "read_custom_feed", return_value=[baseline]), \
                    patch.object(outages, "send_notification") as notify:
                result = run("add-custom", "My provider", "https://status.example.org/")
                self.assertEqual(result["added"], key)
                row = next(row for row in result["rows"] if row["id"] == key)
                self.assertEqual(row["kind"], "feed")
                self.assertFalse(row["alert"])
                self.assertTrue(row["active"])
                run("alert", key)
                notify.assert_not_called()

            with patch.multiple(outages, SOURCES={}, read_custom_feed=lambda _: [dict(baseline, state="resolved")]), \
                    patch.object(outages, "send_notification") as notify:
                run("refresh")
                notify.assert_called_once_with("My provider · resolved")
            run("country", "GB")
            self.assertTrue(next(row for row in run("status")["rows"] if row["id"] == key)["favourite"])

    def test_remove_custom_link_and_feed_preserves_other_services(self):
        incident = outages.incident("old", "Existing incident", "active")
        checker = {"name": "Local outage checker", "url": "https://example.org/outages"}
        feed = {"name": "Custom feed", "url": "https://example.org/",
                "feed": "https://example.org/api/v2/incidents.json"}
        state = {
            "country": "GB", "background": True,
            "custom": {"custom-link": checker, "custom-feed": feed},
            "favourites": ["custom-link", "custom-feed", "github"],
            "alerts": ["custom-feed", "github"],
            "sources": {"custom-feed": {"events": [incident]}, "github": {}},
        }
        with tempfile.TemporaryDirectory() as folder, patch.object(outages, "STATE", Path(folder)):
            outages.save(state)
            for key in ("github", "custom-link", "custom-feed", "custom-feed"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    outages.main(["remove-custom", key])
                rows = json.loads(output.getvalue())["rows"]
                self.assertTrue(any(row["id"] == "github" and row["favourite"] and row["alert"] for row in rows))
                if key != "github":
                    self.assertFalse(any(row["id"] == key for row in rows))

            saved = json.loads((Path(folder) / "state.json").read_bytes())
            self.assertEqual(saved, {
                "country": "GB", "background": True, "custom": {},
                "favourites": ["github"], "alerts": ["github"], "sources": {"github": {}},
            })

    def test_custom_feed_requires_statuspage_metadata_and_ignores_maintenance(self):
        with self.assertRaises(KeyError):
            outages.parse(None, b'{"incidents":[]}', "custom-statuspage")
        data = json.dumps({"page": {"id": "public"}, "incidents": [
            {"id": "a", "name": "Maintenance", "status": "in_progress", "impact": "maintenance"},
            {"id": "b", "name": "<b>Failure</b>", "status": "identified", "impact": "major"},
        ]}).encode()
        self.assertEqual(outages.parse(None, data, "custom-statuspage"), [
            outages.incident("b", "‹b›Failure‹/b›", "active", 2),
        ])

    def test_custom_feed_does_not_connect_to_private_or_mixed_dns_answers(self):
        for addr in ("127.0.0.1", "10.0.0.1", "::ffff:127.0.0.1", "ff02::1"):
            with self.subTest(addr=addr), patch.object(outages.socket, "getaddrinfo", return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 443)),
            ]), patch.object(outages.socket, "socket") as connect:
                with self.assertRaises(ValueError):
                    outages.fetch_custom_feed("https://status.example.org/api/v2/incidents.json")
                connect.assert_not_called()

    def test_manual_test_alert_reports_result_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(outages, "STATE", Path(folder)):
            saved = Path(folder) / "state.json"
            original = '{"alerts":[],"quiet":true,"snooze":9999999999}'
            saved.write_text(original)
            for returncode, sent in ((0, True), (1, False)):
                output = io.StringIO()
                result = subprocess.CompletedProcess(["notify-send"], returncode)
                with patch.object(outages.subprocess, "run", return_value=result) as notify, \
                        contextlib.redirect_stdout(output):
                    outages.main(["test-alert"])
                self.assertEqual(json.loads(output.getvalue()), {"testAlertSent": sent})
                icon = Path(outages.__file__).resolve().with_name("notification-icon.png")
                self.assertTrue(icon.is_file())
                notify.assert_called_once_with(
                    ["notify-send", "-a", "Omarchy Canary", "-h", f"string:image-path:{icon}",
                     "--", "Omarchy Canary",
                     "Test alert · Example service · new incident"], timeout=4, check=False,
                )
            self.assertEqual(saved.read_text(), original)
            self.assertEqual(list(Path(folder).iterdir()), [saved])


if __name__ == "__main__":
    unittest.main()
