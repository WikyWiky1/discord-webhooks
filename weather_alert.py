#!/usr/bin/env python3
"""
STORM WATCH - WikyWiky Studios
Posts active severe weather alerts from the National Weather Service.

*** NOT A LIFE-SAFETY SYSTEM. ***
This polls on a schedule and GitHub's scheduler is best-effort, so a post can
lag the actual alert by 10+ minutes. A tornado warning gives roughly 13 minutes
of lead time. Keep a NOAA weather radio, phone Wireless Emergency Alerts, and a
real weather app as your primary. This is for group awareness, not survival.

Data: api.weather.gov (public, no key).

Flags:
  --check   verify every county code resolves, print its real name, post nothing
  --dry     print what it would post, send nothing
  --test    send a single "online" message
"""

import json
import os
import sys
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


# ── posting ─────────────────────────────────────────────────────────────────

def post(url, embeds, content=None):
    payload = {"username": BRAND, "allowed_mentions": {"parse": []}}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "StormWatch/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        print(f"  posted ({res.status})")


# ── state ───────────────────────────────────────────────────────────────────

def load_seen():
    try:
        with open(SEEN_FILE) as fh:
            data = json.load(fh)
            return set(data) if isinstance(data, list) else set()
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as fh:
        json.dump(sorted(seen)[-500:], fh, indent=1)


# ── main ────────────────────────────────────────────────────────────────────

def main():
    if "--check" in sys.argv:
        return check_zones()

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

    seen = load_seen()
    fresh, posts = [], []

    for feature in features:
        props = feature.get("properties") or {}
        ident = props.get("id") or feature.get("id")
        event = props.get("event") or "?"

        if not wanted(props):
            print(f"  filtered out: {event} ({props.get('severity')})")
            continue
        if ident in seen:
            print(f"  already posted: {event}")
            continue

        print(f"  NEW: {event} - {clip(props.get('areaDesc'), 60)}")
        posts.append(build_embed(props))
        fresh.append(ident)

    if not posts:
        print("Nothing new to post.")
        return 0

    if dry:
        print("\n--- dry run, not posting ---")
        print(json.dumps(posts, indent=2))
        return 0

    # One message per alert so each gets its own notification.
    try:
        for embed in posts:
            headline = embed["title"]
            post(webhook, [embed], content=f"🚨 {headline.strip('🚨 ')} 🚨")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:400]
        print(f"Discord rejected the post: HTTP {err.code} {body}", file=sys.stderr)
        return 1

    seen.update(fresh)
    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
