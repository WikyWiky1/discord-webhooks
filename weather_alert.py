#!/usr/bin/env python3
"""
STORM WATCH - WikyWiky Studios
Posts active severe weather alerts from the National Weather Service, then
edits each post to EXPIRED once the alert clears.

*** NOT A LIFE-SAFETY SYSTEM. ***
This polls on a schedule and GitHub's scheduler is best-effort, so a post can
lag the actual alert by 10+ minutes. A tornado warning gives roughly 13 minutes
of lead time. Keep a NOAA weather radio, phone Wireless Emergency Alerts, and a
real weather app as your primary. This is for group awareness, not survival.

Data: api.weather.gov (public, no key).

Flags:
  --check   verify every county code resolves, print its real name, post nothing
  --dry     print what it would post and what it would expire, send nothing
  --state   print the tracked alerts and exit, touch nothing
  --test    send a single "online" message
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Chicago")
except Exception:
    LOCAL_TZ = timezone.utc

# NWS asks for a contact email. It's read from the NWS_CONTACT_EMAIL secret so
# it never appears in this file, in the repo, or in the Actions log - GitHub
# masks secret values in output. Without it the script still runs, just with a
# less polite User-Agent.
CONTACT_EMAIL = os.environ.get("NWS_CONTACT_EMAIL", "").strip()
USER_AGENT = (
    f"StormWatch/1.0 (github.com/WikyWiky1, {CONTACT_EMAIL})" if CONTACT_EMAIL
    else "StormWatch/1.0 (github.com/WikyWiky1)"
)

API = "https://api.weather.gov"
BRAND = "StormWatch"
SEEN_FILE = "weather_seen.json"
STATE_VERSION = 2

# How many alert IDs to remember, and how long to wait between messages.
# Discord's webhook limit is roughly 5 requests per 2 seconds; during an
# outbreak this script can easily have a dozen alerts to send at once.
SEEN_LIMIT = 500
POST_GAP_SECONDS = 1.5

# UGC county codes: 2-letter state + "C" + 3-digit FIPS county code.
# Run --check to confirm each of these resolves to the county you expect.
ZONES = {
    "MOC077": "Greene County, MO (Springfield / Republic)",
    "MOC043": "Christian County, MO",
    "KSC173": "Sedgwick County, KS (Wichita)",
    "KSC191": "Sumner County, KS",
    "ILC201": "Winnebago County, IL (Rockford)",
    "TXC201": "Harris County, TX (Houston)",
    "NVC003": "Clark County, NV (Las Vegas)",
}

# Tornado is the explicit exception: post warnings AND watches.
ALWAYS_EVENTS = ("tornado",)

# Everything else must be an actual Warning at Severe or Extreme severity.
# That means occurring or imminent - not advisories, outlooks, or statements.
WANTED_SEVERITY = ("extreme", "severe")

# Add events here to mute them. Houston and Vegas throw a lot of heat warnings
# in summer, so ("Excessive Heat Warning", "Heat Advisory") is a common trim.
EXCLUDE_EVENTS = ()

SEVERITY_COLOR = {
    "extreme": 0xB3001B,
    "severe": 0xE8741C,
}
TORNADO_COLOR = 0xFF0033

# ── expired appearance ──────────────────────────────────────────────────────
# When an alert drops off the NWS active feed, the original message is edited
# in place instead of a new "all clear" post being sent. The heading survives,
# the body does not - no headline, no timing, no instructions. Someone
# scrolling back should be able to see that a tornado warning happened here
# and where, without the dead safety text still reading like it's live.
EXPIRED_COLOR = 0x4F545C
EXPIRED_TITLE = "{event} — EXPIRED"
EXPIRED_CONTENT = "{event} — expired"
EXPIRED_KEEP_LOCATION = True


# ── http ────────────────────────────────────────────────────────────────────

def get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


# ── county code verification ────────────────────────────────────────────────

def check_zones():
    """Ask NWS to confirm each code. Wrong codes fail silently forever
    otherwise, which is the worst possible bug in something like this."""
    print("Verifying county codes against NWS...")
    if not CONTACT_EMAIL:
        print("  (note: NWS_CONTACT_EMAIL secret not set)")
    print()
    bad = []

    for code, label in ZONES.items():
        try:
            data = get_json(f"{API}/zones/county/{code}")
            real = (data.get("properties") or {}).get("name", "?")
            state = (data.get("properties") or {}).get("state", "?")
            print(f"  OK    {code}  ->  {real}, {state}")
            print(f"        expected: {label}")
        except urllib.error.HTTPError as err:
            print(f"  BAD   {code}  ->  HTTP {err.code}   (expected {label})")
            bad.append(code)
        except Exception as err:
            print(f"  ERR   {code}  ->  {err}")
            bad.append(code)

    print()
    if bad:
        print(f"These codes did not resolve: {', '.join(bad)}")
        return 1
    print("All county codes valid.")
    return 0


# ── alert filtering ─────────────────────────────────────────────────────────

def is_tornado(event):
    return any(word in event.lower() for word in ALWAYS_EVENTS)


def wanted(props):
    event = props.get("event") or ""

    if event in EXCLUDE_EVENTS:
        return False

    # Drills, tests and system messages never post.
    if (props.get("status") or "").lower() != "actual":
        return False
    if (props.get("messageType") or "") not in ("Alert", "Update"):
        return False

    if is_tornado(event):
        return True

    if not event.lower().endswith("warning"):
        return False

    return (props.get("severity") or "").lower() in WANTED_SEVERITY


def fmt_time(text):
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return f"{stamp.astimezone(LOCAL_TZ):%-I:%M %p %Z}"


def clip(text, limit):
    text = " ".join((text or "").split())
    return text[:limit - 3].rstrip() + "..." if len(text) > limit else text


def build_embed(props):
    event = props.get("event") or "Weather Alert"
    severity = (props.get("severity") or "").lower()

    if is_tornado(event):
        color = TORNADO_COLOR
    else:
        color = SEVERITY_COLOR.get(severity, 0xE8741C)

    embed = {
        "title": f"🚨 {event} 🚨",
        "description": clip(props.get("headline") or props.get("description"), 900),
        "color": color,
        "fields": [],
    }

    area = clip(props.get("areaDesc"), 900)
    if area:
        embed["fields"].append({"name": "Where", "value": area, "inline": False})

    until = fmt_time(props.get("expires") or props.get("ends"))
    if until:
        embed["fields"].append({"name": "Until", "value": until, "inline": True})

    if severity:
        embed["fields"].append(
            {"name": "Severity", "value": severity.title(), "inline": True})

    instruction = clip(props.get("instruction"), 700)
    if instruction:
        embed["fields"].append(
            {"name": "What to do", "value": instruction, "inline": False})

    sender = props.get("senderName")
    embed["footer"] = {"text": sender or "National Weather Service"}
    return embed


def build_expired_embed(record):
    """The gutted version of an alert embed. Everything that told you what to
    do is gone on purpose - only the fact that it happened, and where, stays."""
    event = record.get("event") or "Weather Alert"
    embed = {
        "title": EXPIRED_TITLE.format(event=event),
        "color": EXPIRED_COLOR,
        "fields": [],
    }

    area = record.get("area")
    if EXPIRED_KEEP_LOCATION and area:
        embed["fields"].append({"name": "Where", "value": area, "inline": False})

    cleared = f"{datetime.now(LOCAL_TZ):%-I:%M %p %Z on %b %-d}"
    embed["footer"] = {"text": f"No longer active · cleared {cleared}"}
    return embed


# ── posting and editing ─────────────────────────────────────────────────────

def with_wait(url):
    """Discord only returns the created message (and therefore its ID) when
    ?wait=true is set. Without the ID there is nothing to edit later."""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["wait"] = "true"
    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(query)))


def message_url(url, message_id):
    """PATCH target for an existing webhook message. Any thread_id on the
    original webhook URL is preserved - editing a message inside a thread
    needs it - but wait= is dropped since it isn't valid here."""
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/") + f"/messages/{message_id}"
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query.pop("wait", None)
    return urllib.parse.urlunsplit(
        parts._replace(path=path, query=urllib.parse.urlencode(query)))


def retry_after_seconds(err):
    """Pull the wait time out of a Discord 429 body. Discord has reported
    retry_after in both seconds and milliseconds over the years, so treat
    anything implausibly large as milliseconds."""
    wait = 2.0
    try:
        info = json.loads(err.read().decode("utf-8", "replace"))
        wait = float(info.get("retry_after", wait))
        if wait > 100:
            wait /= 1000.0
    except Exception:
        pass
    return min(max(wait, 0.5), 30.0)


def send(url, payload, method="POST"):
    """One request with rate-limit backoff, shared by posts and edits. An
    outbreak means a burst of messages, which means Discord will rate limit
    us. Back off and retry rather than failing the whole run."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "StormWatch/1.0"},
        method=method,
    )

    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                body = res.read().decode("utf-8", "replace")
                print(f"  {method.lower()}ed ({res.status})")
                if not body.strip():
                    return {}
                try:
                    return json.loads(body)
                except ValueError:
                    return {}
        except urllib.error.HTTPError as err:
            if err.code != 429 or attempt == 3:
                raise
            wait = retry_after_seconds(err)
            print(f"  rate limited, waiting {wait:.1f}s")
            time.sleep(wait)
    return {}


def post(url, embeds, content=None):
    """Returns the new message's ID, or None if Discord didn't hand one back."""
    payload = {"username": BRAND, "allowed_mentions": {"parse": []}}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    data = send(with_wait(url), payload, "POST")
    return (data or {}).get("id")


def edit(url, message_id, embeds, content=None):
    """Editing a webhook message accepts content, embeds and allowed_mentions
    only - username is fixed at creation time, so it isn't sent here."""
    payload = {
        "content": content or "",
        "embeds": embeds or [],
        "allowed_mentions": {"parse": []},
    }
    send(message_url(url, message_id), payload, "PATCH")


# ── state ───────────────────────────────────────────────────────────────────
#
# v1 stored a flat list of alert IDs - enough to answer "have I posted this?"
# and nothing else. v2 stores a record per alert so a posted alert has an
# identity: which Discord message is it, and has it been retired yet.
#
# Records stay oldest-first. Order matters: the trim in save_state drops from
# the front, so it has to be chronological.

def normalise(item):
    if isinstance(item, str):
        # Legacy v1 entry. No message ID exists for it, so it can never be
        # edited - mark it retired so nothing tries.
        return {
            "id": item,
            "message_id": None,
            "event": None,
            "area": None,
            "expired": True,
            "posted_at": None,
            "expired_at": None,
        }

    if isinstance(item, dict) and item.get("id"):
        return {
            "id": str(item["id"]),
            "message_id": item.get("message_id"),
            "event": item.get("event"),
            "area": item.get("area"),
            "expired": bool(item.get("expired")),
            "posted_at": item.get("posted_at"),
            "expired_at": item.get("expired_at"),
        }

    return None


def load_state():
    try:
        with open(SEEN_FILE) as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get("alerts") or []
    else:
        return []

    records, seen_ids = [], set()
    for item in raw:
        record = normalise(item)
        if record and record["id"] not in seen_ids:
            seen_ids.add(record["id"])
            records.append(record)
    return records


def save_state(records):
    """Trim to SEEN_LIMIT, but never drop a record that's still live - losing
    one means its message sits in the channel as a warning forever."""
    live = [rec for rec in records if not rec.get("expired")]
    retired = [rec for rec in records if rec.get("expired")]

    room = max(SEEN_LIMIT - len(live), 0)
    keep = {rec["id"] for rec in live}
    if room:
        keep.update(rec["id"] for rec in retired[-room:])

    kept = [rec for rec in records if rec["id"] in keep]
    with open(SEEN_FILE, "w") as fh:
        json.dump({"version": STATE_VERSION, "alerts": kept}, fh, indent=1)


def make_record(props, ident, message_id):
    return {
        "id": ident,
        "message_id": message_id,
        "event": props.get("event") or "Weather Alert",
        "area": clip(props.get("areaDesc"), 900),
        "expired": False,
        "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "expired_at": None,
    }


def show_state():
    records = load_state()
    if not records:
        print("No alerts tracked yet.")
        return 0

    live = [rec for rec in records if not rec.get("expired")]
    print(f"{len(records)} tracked, {len(live)} still live.\n")
    for rec in records:
        mark = "live   " if not rec.get("expired") else "expired"
        msg = rec.get("message_id") or "(no message id)"
        print(f"  {mark}  {rec.get('event') or '?'}")
        print(f"           alert {rec['id']}")
        print(f"           msg   {msg}")
    return 0


# ── main ────────────────────────────────────────────────────────────────────

def main():
    if "--check" in sys.argv:
        return check_zones()
    if "--state" in sys.argv:
        return show_state()

    dry = "--dry" in sys.argv
    webhook = os.environ.get("DISCORD_WEBHOOK", "").strip()

    if not webhook and not dry:
        print("DISCORD_WEBHOOK is not set.", file=sys.stderr)
        return 1

    if "--test" in sys.argv:
        post(webhook, [{"title": "🚨 StormWatch is online 🚨",
                        "description": "Monitoring "
                                       f"{len(ZONES)} counties via the National "
                                       "Weather Service.",
                        "color": 0x3BA55D}])
        return 0

    query = urllib.parse.urlencode({"zone": ",".join(ZONES)})
    try:
        data = get_json(f"{API}/alerts/active?{query}")
    except Exception as err:
        print(f"NWS request failed: {err}", file=sys.stderr)
        return 1

    features = data.get("features") or []
    print(f"NWS returned {len(features)} active alert(s) for {len(ZONES)} counties.")

    records = load_state()
    known = {rec["id"] for rec in records}

    # Every ID currently in the feed, including ones this bot filtered out.
    # Presence here is the only thing that keeps an alert alive - if NWS still
    # lists it, it's still in effect, whether or not it met the post criteria.
    active_ids = set()
    posts = []

    for feature in features:
        props = feature.get("properties") or {}
        ident = props.get("id") or feature.get("id")
        event = props.get("event") or "?"
        if ident:
            active_ids.add(ident)

        if not wanted(props):
            print(f"  filtered out: {event} ({props.get('severity')})")
            continue
        if ident in known:
            print(f"  already posted: {event}")
            continue

        print(f"  NEW: {event} - {clip(props.get('areaDesc'), 60)}")
        posts.append((ident, props, build_embed(props)))

    # Anything tracked, still live, and no longer in the feed has ended.
    stale = [rec for rec in records
             if not rec.get("expired") and rec["id"] not in active_ids]
    for rec in stale:
        print(f"  ENDED: {rec.get('event') or rec['id']}")

    if not posts and not stale:
        print("Nothing new to post, nothing to expire.")
        return 0

    if dry:
        print("\n--- dry run, not posting ---")
        if posts:
            print(json.dumps([embed for _, _, embed in posts], indent=2))
        if stale:
            print("\n--- dry run, not editing ---")
            for rec in stale:
                target = rec.get("message_id") or "(no message id - skipped)"
                print(f"would edit message {target}:")
                print(json.dumps(build_expired_embed(rec), indent=2))
        return 0

    failed = 0

    # New alerts go out first - they're the time-critical half. Cleanup edits
    # can wait the extra second or two.
    #
    # One message per alert so each gets its own notification. State is saved
    # after every successful send - if send #7 of 15 dies, the first six stay
    # recorded and don't get reposted on the next run.
    try:
        for index, (ident, props, embed) in enumerate(posts):
            if index:
                time.sleep(POST_GAP_SECONDS)
            headline = embed["title"]
            message_id = post(webhook, [embed],
                              content=f"🚨 {headline.strip('🚨 ')} 🚨")
            records.append(make_record(props, ident, message_id))
            save_state(records)
            if not message_id:
                print("  warning: no message id returned, this one can't expire")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:400]
        print(f"Discord rejected the post: HTTP {err.code} {body}", file=sys.stderr)
        failed = 1

    # Same per-item save discipline on the edits.
    for index, rec in enumerate(stale):
        message_id = rec.get("message_id")
        if not message_id:
            # Pre-v2 post, or a send that never returned an ID. Nothing to
            # edit - just stop tracking it.
            rec["expired"] = True
            rec["expired_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            save_state(records)
            continue

        if index or posts:
            time.sleep(POST_GAP_SECONDS)

        event = rec.get("event") or "Weather Alert"
        try:
            edit(webhook, message_id, [build_expired_embed(rec)],
                 content=EXPIRED_CONTENT.format(event=event))
        except urllib.error.HTTPError as err:
            if err.code == 404:
                # Someone deleted the message. Fine - it's gone, which is the
                # outcome we wanted anyway.
                print(f"  message {message_id} is gone, dropping it")
            else:
                body = err.read().decode("utf-8", "replace")[:400]
                print(f"Discord rejected the edit: HTTP {err.code} {body}",
                      file=sys.stderr)
                failed = 1
                continue

        rec["expired"] = True
        rec["expired_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_state(records)

    return failed


if __name__ == "__main__":
    sys.exit(main())
