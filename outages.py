"""Local incident cache and desktop notification helper."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET

SOURCES = {
    "openai": ("OpenAI / ChatGPT", "rss", "https://status.openai.com/feed.rss", "https://status.openai.com/"),
    "claude": ("Claude", "summary", "https://status.claude.com/api/v2/incidents.json", "https://status.claude.com/"),
    "grok": ("Grok Bot", "rss", "https://status.x.ai/feed.xml", "https://status.x.ai/grok-bot"),
    "router": ("OpenRouter", "rss", "https://status.openrouter.ai/history.rss", "https://status.openrouter.ai/"),
    "github": ("GitHub", "summary", "https://www.githubstatus.com/api/v2/incidents.json", "https://www.githubstatus.com/"),
    "cloudflare": ("Cloudflare", "summary", "https://www.cloudflarestatus.com/api/v2/incidents.json", "https://www.cloudflarestatus.com/"),
    "aws": ("AWS public notices", "notices", "https://status.aws.amazon.com/rss/all.rss", "https://health.aws.amazon.com/health/status"),
    "gcp": ("Google Cloud", "google", "https://status.cloud.google.com/incidents.json", "https://status.cloud.google.com/"),
    "workspace": ("Google Workspace", "google", "https://www.google.com/appsstatus/dashboard/incidents.json", "https://www.google.com/appsstatus/dashboard/"),
    "discord": ("Discord", "summary", "https://discordstatus.com/api/v2/incidents.json", "https://discordstatus.com/"),
    "reddit": ("Reddit", "summary", "https://www.redditstatus.com/api/v2/incidents.json", "https://www.redditstatus.com/"),
    "twitch": ("Twitch", "summary", "https://status.twitch.com/api/v2/incidents.json", "https://status.twitch.com/"),
    "zoom": ("Zoom", "summary", "https://www.zoomstatus.com/api/v2/incidents.json", "https://www.zoomstatus.com/"),
    "slack": ("Slack", "slack", "https://slack-status.com/api/v2.0.0/history", "https://slack-status.com/"),
    "azure": ("Azure public notices", "notices", "https://rssfeed.azure.status.microsoft/en-us/status/feed/", "https://azure.status.microsoft/en-us/status/"),
}
COUNTRIES = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "global": "Other / global",
}
# ID -> country, name, kind of check, provider page, provider alert help (optional).
LINKS = {
    "xfinity": ("US", "Xfinity", "Home broadband · address lookup", "https://www.xfinity.com/support/statusmap", "https://www.xfinity.com/support/articles/proactive-outage-notifications"),
    "tds": ("US", "TDS Telecom", "Home broadband · address lookup", "https://tdstelecom.com/support/outage.html", "https://tdstelecom.com/support/outage.html"),
    "att": ("US", "AT&T Internet / Fiber", "Home broadband · address lookup", "https://www.att.com/outages/"),
    "spectrum": ("US", "Spectrum", "Home broadband · provider account", "https://www.spectrum.net/support/troubleshooting-your-spectrum-services-after-outage/"),
    "cox": ("US", "Cox", "Home broadband · provider account", "https://www.cox.com/residential/support/outages.html"),
    "fios": ("US", "Verizon Fios / 5G Home", "Home broadband · network check", "https://www.verizon.com/support/check-network-status/"),
    "frontier": ("US", "Frontier", "Home broadband · address lookup", "https://frontier.com/helpcenter/internet/troubleshooting/check-for-service-outages"),
    "optimum": ("US", "Optimum", "Home broadband · outage map", "https://www.optimum.net/support/outage/"),
    "gfiber": ("US", "Google Fiber", "Home broadband · address lookup", "https://gfiber.com/outage/check", "https://www.prd.gfiber.com/support/en/answer/1952/"),
    "quantum": ("US", "Quantum Fiber", "Home broadband · provider account", "https://www.quantumfiber.com/support/speed-performance/outages"),
    "centurylink": ("US", "CenturyLink", "Home broadband · provider support", "https://www.centurylink.com/home/help/internet/internet-or-phone-not-working.html"),
    "tmobile-home": ("US", "T-Mobile Home Internet", "Home broadband · T-Life diagnostics", "https://www.t-mobile.com/support/home-internet/customer-support"),
    "verizon-mobile": ("US", "Verizon Wireless", "Mobile · network check", "https://www.verizon.com/support/check-network-status/", "https://www.verizon.com/support/network-outage-faqs/"),
    "tmobile": ("US", "T-Mobile Wireless", "Mobile · provider sign-in", "https://network.t-mobile.com/"),
    "att-mobile": ("US", "AT&T Wireless", "Mobile · local outage checker", "https://www.att.com/outages/"),
    "mte": ("US", "Middle Tennessee Electric", "Local power · outage map", "https://mte.com/outages", "https://mte.com/emergency-management-and-outage-alerts"),
    "bt": ("GB", "BT Broadband", "Home broadband · service checker", "https://www.bt.com/help/check-service-status"),
    "virgin": ("GB", "Virgin Media", "Home broadband · service checker", "https://www.virginmedia.com/help/service-status"),
    "sky": ("GB", "Sky", "Home broadband · service checker", "https://www.sky.com/help/servicestatus"),
    "ee": ("GB", "EE", "Mobile · network check", "https://myaccount.ee.co.uk/networkchecker/checkservice"),
    "o2": ("GB", "O2", "Mobile · network check", "https://status.o2.co.uk/"),
    "vodafone": ("GB", "Vodafone UK", "Mobile · network check", "https://www.vodafone.co.uk/network/status-checker"),
    "three": ("GB", "Three UK", "Mobile · network check", "https://www.three.co.uk/support/network-and-coverage/network-support"),
    "rogers": ("CA", "Rogers / Shaw", "Broadband and mobile · provider outage support", "https://www.rogers.com/support/outage"),
    "bell": ("CA", "Bell", "Broadband and mobile · service checker", "https://support.bell.ca/Outage-Check"),
    "telus": ("CA", "TELUS", "Broadband and mobile · service checker", "https://www.telus.com/outages", "https://www.telus.com/en/support/article/service-outage"),
    "telstra": ("AU", "Telstra", "Broadband and mobile · outage checker", "https://www.telstra.com.au/outages"),
    "optus": ("AU", "Optus", "Broadband and mobile · service checker", "https://www.optus.com.au/living-network/service-status"),
    "nbn": ("AU", "nbn", "Fixed broadband network · address lookup", "https://www.nbnco.com.au/support/network-status"),
    "starlink": ("global", "Starlink", "Satellite broadband · Starlink app alerts", "https://starlink.com/us/support/article/5483745b-f8f0-d9c4-4d57-17c35a7c233a"),
    "instagram": ("global", "Instagram", "Consumer reports", "https://downdetector.com/status/instagram/"),
    "facebook": ("global", "Facebook", "Consumer reports", "https://downdetector.com/status/facebook/"),
    "youtube": ("global", "YouTube", "Official known issues", "https://support.google.com/youtube/threads?thread_filter=%28purpose%3Aknown_issue%29"),
    "outlook": ("global", "Outlook.com", "Microsoft consumer status", "https://status.cloud.microsoft/"),
    "m365": ("global", "Microsoft 365 / Teams", "Microsoft business status", "https://status.cloud.microsoft/m365"),
    "radar": ("global", "Cloudflare Radar", "Regional and network disruptions", "https://radar.cloudflare.com/outage-center"),
}
DEFAULT_FAVOURITES = ("openai", "grok", "xfinity", "facebook", "youtube")
LIMIT = 4 * 1024 * 1024
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "omarchy-service-outages"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def fetch(url):
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "Omarchy-Service-Outages/0.1"})
    with opener.open(request, timeout=5) as response:
        data = response.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("Feed exceeds size limit")
    return data

def clean(value, limit=180):
    # Labels and notifications can safely be passed to controls that support markup.
    return " ".join(str(value or "").replace("<", "‹").replace(">", "›").replace("&", "＆").split())[:limit]

def incident(key, title, state, severity=0, scope=()):
    if not key or not title:
        raise ValueError("Incident is missing an ID or title")
    return {"id": str(key)[:240], "title": clean(title), "state": state, "severity": severity,
            "scope": sorted({clean(x, 90) for x in scope if x})[:30]}

def parse(source, data):
    kind = SOURCES[source][1]
    if kind == "summary":
        doc = json.loads(data)
        return [incident(row["id"], row["name"],
                         "resolved" if row["status"] in ("resolved", "postmortem") else "active",
                         {"minor": 1, "major": 2, "critical": 3}.get(row["impact"], 0),
                         [c["name"] for c in row.get("components", [])])
                for row in doc["incidents"][:250]]
    if kind == "slack":
        return [incident(row["id"], row["title"],
                         "resolved" if row["status"] == "resolved" else "active",
                         2 if row["type"] == "outage" else 1, row.get("services", []))
                for row in json.loads(data)[:250] if row["type"] in ("incident", "outage")]
    if kind == "google":
        return [incident(row["id"], row["external_desc"].splitlines()[0]
                         if not row["external_desc"].startswith("**Summary:**\n")
                         else row["external_desc"].splitlines()[1],
                         "resolved" if row.get("end") else "active",
                         {"SERVICE_DISRUPTION": 1, "SERVICE_OUTAGE": 2}.get(row.get("severity"), 0),
                         [p["title"] for p in row.get("affected_products", [])])
                for row in json.loads(data)[:250]]
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Unexpected XML declaration")
    channel = ET.fromstring(data).find("channel")
    if channel is None or not channel.findtext("title"):
        raise ValueError("Missing RSS channel")
    rows = []
    for item in channel.findall("item")[:350]:
        title = item.findtext("title", "")
        desc = item.findtext("description", "").lower()
        categories = {c.text.lower() for c in item.findall("category") if c.text}
        key = item.findtext("guid") or item.findtext("link")
        if kind == "notices":
            state = "notice"
        elif source == "grok":
            if not title.startswith("[Grok Bot]"):
                continue
            title = title.removeprefix("[Grok Bot]").strip()
            state = "resolved" if "resolved" in categories else "active" if "active" in categories else "notice"
        elif "maintenance" in categories:
            continue
        else:
            match = re.search(r"status:\s*(investigating|identified|monitoring|resolved|postmortem)\b", desc)
            if not match and source == "router":
                match = re.search(r"<strong>\s*(investigating|identified|monitoring|resolved|postmortem)\s*</strong>", desc)
            phase = match[1] if match else ""
            state = "resolved" if phase in ("resolved", "postmortem") else "active" if phase else "notice"
        severity = 2 if "outage" in categories else 1 if "disruption" in categories else 0
        rows.append(incident(key, title, state, severity))
    return rows

def read_source(source):
    try:
        return parse(source, fetch(SOURCES[source][2]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, ET.ParseError):
        return None

def connection():
    def probe(target):
        url, marker = target
        try:
            body = fetch(url)
            return body.startswith(marker) if marker else body == b""
        except (OSError, ValueError):
            return False

    targets = (("https://connectivitycheck.gstatic.com/generate_204", b""),
               ("https://www.cloudflare.com/cdn-cgi/trace", b"fl="))
    with ThreadPoolExecutor(max_workers=2) as pool:
        return any(list(pool.map(probe, targets)))


def merge(previous, incoming, now):
    if incoming is None:
        return dict(previous, failures=previous.get("failures", 0) + 1), []
    known = {
        row["id"]: dict(row, confirmed=row.get("confirmed", previous.get("checked", 0)))
        for row in previous.get("events", [])
    }
    changes = []
    for row in incoming:
        old = known.get(row["id"])
        state = row["state"]
        if previous.get("checked"):
            if state in ("active", "notice") and old is None:
                changes.append(state)
            elif state == "active" and old and old["state"] != "active":
                changes.append("active")
            elif state == "active" and old and (row["severity"] > old["severity"]
                                                or set(row["scope"]) - set(old["scope"])):
                changes.append("escalated")
            elif state == "resolved" and old and old["state"] == "active":
                changes.append("resolved")
        known[row["id"]] = dict(row, confirmed=now)
    events = list(known.values())
    active = [row for row in events if row["state"] == "active"]
    history = [row for row in events if row["state"] != "active"]
    return {"events": active[:350] + history[-600:], "checked": now, "failures": 0}, changes

def refresh(state, force):
    now = time.time()
    if now - state.get("attempt", 0) < (5 if force else 270):
        return []
    state["attempt"] = now
    alerts = []
    sources = state.setdefault("sources", {})
    due = [key for key in SOURCES if force or now >= sources.get(key, {}).get("retry", 0)]
    with ThreadPoolExecutor(max_workers=11) as pool:
        probe_result = pool.submit(connection) if state.get("probe") else None
        for key, result in zip(due, pool.map(read_source, due)):
            entry, changes = merge(sources.get(key, {}), result, now)
            if result is None:
                entry["retry"] = now + min(1800, 300 * 2 ** min(entry["failures"] - 1, 3))
            sources[key] = entry
            if key in state.get("alerts", []) and changes:
                alerts.append((SOURCES[key][0], changes))
        if probe_result is not None:
            state["connection"] = "reachable" if probe_result.result() else "unverified"
    if state.get("connection") == "unverified" and any(sources.get(k, {}).get("checked") == now for k in due):
        state["connection"] = "reachable"
    if state.get("connection") == "unverified":
        return []
    return alerts

def view(state):
    now = time.time()
    rows = []
    country = state.get("country", "US")
    defaults = tuple(key for key in DEFAULT_FAVOURITES
                     if key not in LINKS or LINKS[key][0] in ("global", country))
    favourites = state.get("favourites", defaults)
    for key, (name, kind, _, url) in SOURCES.items():
        data = state.get("sources", {}).get(key, {})
        events = data.get("events", [])
        active = [e for e in events if e["state"] == "active"]
        checked = data.get("checked", 0)
        latest_notices = [e for e in events if e["state"] == "notice"]
        if data.get("failures", 0) >= 3 or (checked and now - checked > 900):
            status = "Status unknown"
        elif not checked:
            status = "Check failed" if data.get("failures") else "Awaiting first check"
        elif data.get("failures"):
            status = "Last check failed"
        elif active:
            status = f"{len(active)} active incident" + ("s" if len(active) != 1 else "")
        elif kind == "notices":
            status = "Official notice feed"
        elif latest_notices:
            status = "No confirmed active incident"
        else:
            status = "No active incident reported"
        fresh_active = [e for e in active if e.get("confirmed", checked) == checked]
        stale_active = [e for e in active if e not in fresh_active]
        if stale_active and not fresh_active and not data.get("failures") and now - checked <= 900:
            status = "Last known incident · awaiting confirmation"
        elif fresh_active and stale_active and not data.get("failures") and now - checked <= 900:
            status = f"{len(fresh_active)} currently reported · {len(stale_active)} awaiting confirmation"
        details = active or latest_notices[:1]
        rows.append(dict(id=key, name=name, url=url, status=status, checked=checked,
                         active=bool(fresh_active), cachedActive=bool(active), missingActive=bool(stale_active), alert=key in state.get("alerts", []),
                         healthKnown=bool(checked and not data.get("failures") and (not stale_active or fresh_active)
                                          and kind != "notices"),
                         kind="feed", favourite=key in favourites, inDirectory=True,
                         details=[dict(title=e["title"] + (" · " + ", ".join(e["scope"]) if e["scope"] else ""),
                                       confirmed=e.get("confirmed", checked), active=e["state"] == "active")
                                  for e in details[:4]]))
    for key, (region, name, label, url, *alert_help) in LINKS.items():
        rows.append(dict(id=key, name=name, url=url, kind="link", status=label,
                         favourite=key in favourites, details=[], inDirectory=region in ("global", country),
                         alertHelp=alert_help[0] if alert_help else ""))
    rows.sort(key=lambda row: (not row["favourite"], row["name"].casefold()))
    return dict(rows=rows, background=state.get("background", False), country=country, countries=COUNTRIES,
                showAll=state.get("showAll", False),
                probe=state.get("probe", False), connection=state.get("connection", "unchecked"),
                quiet=state.get("quiet", False), snooze=state.get("snooze", 0) > now,
                attempt=state.get("attempt", 0))

def save(state):
    with tempfile.NamedTemporaryFile(mode="w", dir=STATE, delete=False) as file:
        json.dump(state, file)
    os.replace(file.name, STATE / "state.json")

def send_notification(body):
    subprocess.run(["notify-send", "-a", "Omarchy Canary", "Omarchy Canary", body], timeout=4, check=False)

def main(args):
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (STATE / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = STATE / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        command = args[0]
        alerts = []
        if command in ("refresh", "visit", "poll"):
            if command != "poll" or state.get("background"):
                alerts = refresh(state, command == "refresh")
        elif command in ("background", "probe", "quiet", "showAll"):
            state[command] = args[1] == "true"
            if command == "background" and state[command]:
                alerts = refresh(state, False)
            if command == "probe":
                state["connection"] = ("reachable" if connection() else "unverified") if state[command] else "unchecked"
        elif command == "alert" and args[1] in SOURCES:
            choices = set(state.get("alerts", []))
            if args[1] in choices:
                choices.remove(args[1])
            else:
                choices.add(args[1])
            state["alerts"] = sorted(choices)
        elif command == "country" and args[1] in COUNTRIES:
            state["country"] = args[1]
        elif command == "favourite" and (args[1] in SOURCES or args[1] in LINKS):
            choices = {row["id"] for row in view(state)["rows"] if row["favourite"]}
            choices.symmetric_difference_update([args[1]])
            state["favourites"] = sorted(choices)
        elif command == "snooze":
            state["snooze"] = time.time() + 3600 if args[1] == "true" else 0
        elif command != "status":
            raise ValueError("Unknown command")
        if command != "status":
            save(state)
        muted = state.get("snooze", 0) > time.time() or (
            state.get("quiet") and (dt.datetime.now().hour < 8 or dt.datetime.now().hour >= 22))
        if alerts and not muted:
            labels = {"active": "new incident", "notice": "new notice", "escalated": "impact increased", "resolved": "resolved"}
            body = "\n".join(name + " · " + ", ".join(labels[c] for c in dict.fromkeys(changes))
                             for name, changes in alerts)
            try:
                send_notification(body)
            except (OSError, subprocess.TimeoutExpired):
                pass
        print(json.dumps(view(state)))

if __name__ == "__main__":
    signal.alarm(25)
    main(sys.argv[1:])
