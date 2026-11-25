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
import time
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
        self.assertEqual(changes, [("active", incident)])
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
        self.assertFalse(github["details"][0]["current"])
        severity = dict(incident, severity=2)
        state, changes = outages.merge(state, [severity], 5)
        self.assertEqual(changes, [("escalated", severity)])
        resolved = dict(severity, state="resolved")
        state, changes = outages.merge(state, [resolved], 6)
        self.assertEqual(changes, [("resolved", resolved)])
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
                notify.assert_called_once_with(
                    "My provider · resolved · Existing incident",
                    [{"label": "Open status page", "argv": ["xdg-open", "https://status.example.org/"]}],
                )
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
                "revision": 5,
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
                action = [{"label": "Open status page", "argv": ["xdg-open", outages.SOURCES["github"][3]]}]
                notify.assert_called_once_with(
                    ["notify-send", "-a", "Omarchy Canary", "-h", f"string:image-path:{icon}",
                     "-h", "string:omarchy-actions:" + json.dumps(action), "--", "Omarchy Canary",
                     "Test alert · GitHub · example incident\nAPI requests failing"], timeout=4, check=False,
                )
            self.assertEqual(saved.read_text(), original)
            self.assertEqual(list(Path(folder).iterdir()), [saved])

    def test_incident_notification_titles_scopes_and_actions(self):
        active = outages.incident("voice", "Voice calls failing", "active", 2, ["Voice"])
        resolved = outages.incident("chat", "Delayed messages", "resolved", 1)
        with patch.object(outages, "send_notification") as send:
            outages.notify_incidents([
                ("Discord", "https://discordstatus.com/", [("active", active), ("resolved", resolved)]),
                ("Example", "file:///home/example", [("escalated", active)]),
            ])
        send.assert_called_once_with(
            "Discord · new incident · Voice calls failing · Voice\n"
            "Discord · resolved · Delayed messages\n"
            "Example · impact increased · Voice calls failing · Voice",
            [{"label": "Open Discord", "argv": ["xdg-open", "https://discordstatus.com/"]}],
        )

        with patch.object(outages, "send_notification") as send:
            outages.notify_incidents([
                (f"Service {i}", f"https://service{i}.example.com/", [("active", active)])
                for i in range(5)
            ])
        message, actions = send.call_args.args
        self.assertTrue(message.endswith("\n+1 more updates"))
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[-1]["argv"], ["omarchy-shell", "shaggyrs6.service-outages", "show"])

    def test_current_incidents_take_precedence_over_cached_incidents(self):
        cached = outages.incident("old", "Cached incident", "active")
        fresh = outages.incident("current", "Current incident", "active", scope=["API"])
        state, _ = outages.merge({}, [cached], 1)
        state, _ = outages.merge(state, [fresh], 2)
        result = outages.view({"sources": {"github": state}, "alerts": ["github"], "favourites": []})
        github = next(row for row in result["rows"] if row["id"] == "github")
        self.assertTrue(github["active"])
        self.assertTrue(github["missingActive"])
        self.assertEqual(github["details"], [
            {"title": "Current incident · API", "confirmed": 2, "active": True, "current": True},
            {"title": "Cached incident", "confirmed": 1, "active": True, "current": False},
        ])

    def test_builtin_history_and_api_urls_match_existing_service(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(outages, "STATE", Path(folder)), \
                patch.object(outages, "read_custom_feed") as read:
            for url in ("https://status.openai.com/history", "https://status.openai.com/api/v2/incidents.json",
                        "https://www.githubstatus.com/history"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    outages.main(["add-custom", "Example", url])
                self.assertEqual(json.loads(output.getvalue())["added"], "openai" if "openai" in url else "github")
            read.assert_not_called()
            self.assertFalse(outages.read_state().get("custom"))

    def test_recent_recoveries_use_stable_timestamps(self):
        active = outages.incident("active", "Observed recovery", "active")
        resolved = dict(active, state="resolved")
        old = outages.incident("old", "Old recovery", "resolved", resolved_at="2025-01-01T12:00:00Z")
        recent = outages.incident("recent", "Provider recovery", "resolved",
                                  resolved_at="2025-01-02T12:00:00Z")
        now = recent["resolvedAt"] + 50
        cache, _ = outages.merge({}, [active, old], now - 100)
        cache, _ = outages.merge(cache, [resolved, old, recent], now)
        cache, changes = outages.merge(cache, [resolved, old, recent], now + 200)
        self.assertEqual(changes, [])
        with patch.object(outages.time, "time", return_value=now + 200):
            items = outages.view({"favourites": ["github"], "sources": {"github": cache}})["recoveries"]
        self.assertEqual([(item["title"], item["at"], item["reported"]) for item in items],
                         [("Observed recovery", now, False),
                          ("Provider recovery", recent["resolvedAt"], True)])

    def test_background_scope_does_not_throttle_full_directory_check(self):
        catalog = {key: outages.SOURCES[key] for key in ("github", "slack")}
        state = {"favourites": [], "alerts": ["github"]}
        with patch.object(outages, "SOURCES", catalog), patch.object(outages, "read_source", return_value=[]) as read:
            outages.refresh(state, False, selected_only=True)
            read.assert_called_once_with("github")
            read.reset_mock()
            outages.refresh(state, False)
            self.assertEqual({call.args[0] for call in read.call_args_list}, {"github", "slack"})

    def test_directory_categories_keep_regional_services_and_links_distinct(self):
        us, gb, ca = (outages.view({"country": country}) for country in ("US", "GB", "CA"))
        def rows(model):
            return {row["id"]: row for row in model["rows"] if row["inDirectory"]}

        for model in (us, gb, ca):
            directory = rows(model)
            self.assertIn("Gaming", model["categories"])
            self.assertEqual(directory["epic"]["kind"], "feed")
            self.assertEqual(directory["steam"]["sourceLabel"], "Community status dashboard")
            self.assertEqual(directory["m365"]["category"], "Apps & cloud")
            self.assertIn("MS Teams", directory["m365"]["aliases"])
        self.assertEqual(rows(us)["chase"]["sourceLabel"], "Provider support")
        self.assertEqual(rows(us)["cash-app"]["kind"], "feed")
        self.assertEqual(rows(gb)["lloyds"]["category"], "Banking")
        self.assertEqual(rows(ca)["interac"]["category"], "Payments")
        self.assertNotIn("chase", rows(gb))
        self.assertNotIn("cash-app", rows(ca))
        self.assertNotIn("lloyds", rows(us))

    def test_us_feed_is_checked_elsewhere_only_if_selected(self):
        with patch.object(outages, "SOURCES", {"cash-app": outages.SOURCES["cash-app"]}), \
                patch.object(outages, "read_source", return_value=[]) as read:
            outages.refresh({"country": "GB"}, False)
            read.assert_not_called()
            outages.refresh({"country": "GB", "alerts": ["cash-app"]}, False)
            read.assert_called_once_with("cash-app")

    def test_preference_changes_can_finish_during_refresh_and_survive_commit(self):
        code = """
import time
import outages

outages.SOURCES = {"github": outages.SOURCES["github"]}
def read_source(key):
    (outages.STATE / "started").touch()
    while not (outages.STATE / "finish").exists():
        time.sleep(0.02)
    return []
outages.read_source = read_source
outages.main(["refresh"])
"""
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, "XDG_STATE_HOME": folder}
            state = Path(folder) / "omarchy-service-outages"
            state.mkdir()
            with subprocess.Popen([sys.executable, "-B", "-c", code], env=env,
                                  stdout=subprocess.PIPE, text=True) as refreshing:
                try:
                    deadline = time.monotonic() + 3
                    while not (state / "started").exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue((state / "started").exists())
                    for command in ("favourite", "alert"):
                        subprocess.run([sys.executable, "-B", str(Path(outages.__file__)), command, "github"],
                                       env=env, stdout=subprocess.DEVNULL, check=True, timeout=1.5)
                    self.assertIsNone(refreshing.poll())
                finally:
                    (state / "finish").touch()
                output, _ = refreshing.communicate(timeout=3)
                self.assertEqual(refreshing.returncode, 0)
            snapshot = json.loads(output)
            row = next(row for row in snapshot["rows"] if row["id"] == "github")
            self.assertTrue(row["favourite"] and row["alert"] and row["checked"])


if __name__ == "__main__":
    unittest.main()
