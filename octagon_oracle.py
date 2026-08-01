#!/usr/bin/env python3
"""
Octagon Oracle — creates Discord Scheduled Events for upcoming UFC cards.

Runs daily. Pulls the UFC schedule from ESPN's public MMA endpoint, creates a
native Discord Scheduled Event for anything starting within the next 7 days,
and PATCHes existing events if the UFC moves a date. Optionally posts a
webhook announcement when a new event is created.

Modes:
    python octagon_oracle.py            # normal run
    python octagon_oracle.py --dry      # show what it would do, change nothing
    python octagon_oracle.py --peek     # dump raw ESPN results, verify the feed
    python octagon_oracle.py --state    # print the current state file
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# ---------------------------------------------------------------- config

DAYS_AHEAD = 7               # create events starting within this many days
EVENT_HOURS = 4              # assumed card length, for the event end time
STATE_FILE = "oracle_state.json"

# ESPN's UFC feed also carries developmental cards. Drop those.
EXCLUDE_KEYWORDS = ["Contender Series", "Road to UFC", "Dana White"]

BROADCAST = "Paramount+"      # shown in the description
BOT_NAME = "Octagon Oracle"
EMBED_COLOR = 0xD20A0A        # UFC red

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
DISCORD_API = "https://discord.com/api/v10"

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "").strip()
WEBHOOK = os.environ.get("ORACLE_WEBHOOK", "").strip()

HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "OctagonOracle (github actions, v1)",
}

DRY = "--dry" in sys.argv


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"events": {}}
    data.setdefault("events", {})
    return data


def save_state(state):
    if DRY:
        print("[dry] state not written")
        return
    state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def prune_state(state):
    """Drop entries for cards that finished more than 3 days ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    stale = []
    for espn_id, rec in state["events"].items():
        try:
            start = datetime.fromisoformat(rec["start"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if start < cutoff:
            stale.append(espn_id)
    for espn_id in stale:
        del state["events"][espn_id]
    if stale:
        print(f"Pruned {len(stale)} finished event(s) from state")


# ---------------------------------------------------------------- espn

def fetch_schedule():
    """Query ESPN one day at a time across the window, de-duped by event id."""
    found = {}
    today = datetime.now(timezone.utc).date()

    for offset in range(0, DAYS_AHEAD + 2):
        day = today + timedelta(days=offset)
        try:
            r = requests.get(
                ESPN_URL,
                params={"dates": day.strftime("%Y%m%d")},
                timeout=20,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            print(f"  ESPN fetch failed for {day}: {exc}")
            continue

        for ev in payload.get("events", []) or []:
            parsed = parse_event(ev)
            if parsed:
                found[parsed["espn_id"]] = parsed

        time.sleep(0.4)

    return sorted(found.values(), key=lambda e: e["start"])


def parse_event(ev):
    espn_id = str(ev.get("id") or "").strip()
    name = (ev.get("name") or ev.get("shortName") or "").strip()
    raw_date = ev.get("date")

    if not (espn_id and name and raw_date):
        return None

    if any(bad.lower() in name.lower() for bad in EXCLUDE_KEYWORDS):
        return None

    try:
        start = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    except ValueError:
        return None

    venue = ""
    comps = ev.get("competitions") or []
    for comp in comps:
        v = (comp.get("venue") or {})
        full = v.get("fullName")
        if full:
            addr = v.get("address") or {}
            city = addr.get("city")
            country = addr.get("country") or addr.get("state")
            bits = [b for b in (full, city, country) if b]
            venue = ", ".join(bits)
            break

    return {
        "espn_id": espn_id,
        "name": name[:100],
        "start": start,
        "venue": venue or "Location TBA",
    }


# ---------------------------------------------------------------- discord

def discord_request(method, path, **kwargs):
    """Wrapper with 429 handling, matching the StormWatch retry pattern."""
    url = f"{DISCORD_API}{path}"
    for attempt in range(4):
        r = requests.request(method, url, headers=HEADERS, timeout=20, **kwargs)
        if r.status_code == 429:
            wait = r.json().get("retry_after", 2)
            print(f"  rate limited, sleeping {wait}s")
            time.sleep(float(wait) + 0.5)
            continue
        return r
    return r


def build_description(ev):
    lines = [
        f"📍 {ev['venue']}",
        f"📺 {BROADCAST}",
        "",
        "React below if you're watching. 🥊",
    ]
    return "\n".join(lines)[:1000]


def event_payload(ev):
    end = ev["start"] + timedelta(hours=EVENT_HOURS)
    return {
        "name": ev["name"],
        "description": build_description(ev),
        "scheduled_start_time": ev["start"].isoformat().replace("+00:00", "Z"),
        "scheduled_end_time": end.isoformat().replace("+00:00", "Z"),
        "privacy_level": 2,          # GUILD_ONLY
        "entity_type": 3,            # EXTERNAL
        "entity_metadata": {"location": ev["venue"][:100]},
    }


def create_event(ev):
    if DRY:
        print(f"  [dry] would CREATE: {ev['name']}")
        return "dry-run-id"

    r = discord_request(
        "POST",
        f"/guilds/{GUILD_ID}/scheduled-events",
        json=event_payload(ev),
    )
    if r.status_code in (200, 201):
        new_id = r.json().get("id")
        print(f"  created: {ev['name']}  (discord id {new_id})")
        return new_id

    print(f"  CREATE FAILED [{r.status_code}]: {r.text[:300]}")
    return None


def update_event(discord_id, ev):
    if DRY:
        print(f"  [dry] would PATCH: {ev['name']}")
        return True

    r = discord_request(
        "PATCH",
        f"/guilds/{GUILD_ID}/scheduled-events/{discord_id}",
        json=event_payload(ev),
    )
    if r.status_code == 200:
        print(f"  updated: {ev['name']}")
        return True

    print(f"  PATCH FAILED [{r.status_code}]: {r.text[:300]}")
    return False


def event_exists(discord_id):
    r = discord_request("GET", f"/guilds/{GUILD_ID}/scheduled-events/{discord_id}")
    return r.status_code == 200


def announce(ev, discord_id):
    """Post a webhook announcement for a newly created event."""
    if not WEBHOOK:
        return
    if DRY:
        print(f"  [dry] would announce: {ev['name']}")
        return

    unix = int(ev["start"].timestamp())
    embed = {
        "title": f"🥊 {ev['name']}",
        "description": (
            f"<t:{unix}:F>  •  <t:{unix}:R>\n\n"
            f"📍 {ev['venue']}\n"
            f"📺 {BROADCAST}"
        ),
        "color": EMBED_COLOR,
        "footer": {"text": "Event added to the server calendar — RSVP in the Events tab"},
    }
    try:
        r = requests.post(
            WEBHOOK,
            json={"username": BOT_NAME, "embeds": [embed]},
            timeout=15,
        )
        if r.status_code not in (200, 204):
            print(f"  webhook failed [{r.status_code}]: {r.text[:200]}")
    except Exception as exc:
        print(f"  webhook error: {exc}")


# ---------------------------------------------------------------- modes

def peek():
    print("Raw ESPN results for the next", DAYS_AHEAD + 2, "days:\n")
    for ev in fetch_schedule():
        print(f"  {ev['start'].strftime('%a %b %d  %H:%M UTC')}  |  {ev['name']}")
        print(f"      espn id {ev['espn_id']}  |  {ev['venue']}")
    print("\nIf this list looks right, the feed is good.")


def show_state():
    state = load_state()
    print(json.dumps(state, indent=2))


# ---------------------------------------------------------------- main

def main():
    if "--peek" in sys.argv:
        peek()
        return
    if "--state" in sys.argv:
        show_state()
        return

    if not DRY and not (TOKEN and GUILD_ID):
        print("Missing DISCORD_BOT_TOKEN or DISCORD_GUILD_ID")
        sys.exit(1)

    state = load_state()
    prune_state(state)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=DAYS_AHEAD)

    schedule = fetch_schedule()
    print(f"ESPN returned {len(schedule)} UFC card(s) in range\n")

    created = 0
    for ev in schedule:
        if ev["start"] < now - timedelta(hours=6):
            continue                       # already happened
        if ev["start"] > horizon:
            print(f"  outside window: {ev['name']} ({ev['start'].date()})")
            continue

        rec = state["events"].get(ev["espn_id"])

        if rec:
            # Known card. Did it move, or did someone delete the event?
            if not DRY and not event_exists(rec["discord_id"]):
                print(f"  discord event vanished, recreating: {ev['name']}")
                new_id = create_event(ev)
                if new_id:
                    state["events"][ev["espn_id"]] = {
                        "discord_id": new_id,
                        "start": ev["start"].isoformat(),
                        "name": ev["name"],
                    }
                continue

            if rec.get("start") != ev["start"].isoformat() or rec.get("name") != ev["name"]:
                print(f"  schedule changed: {ev['name']}")
                if update_event(rec["discord_id"], ev):
                    rec["start"] = ev["start"].isoformat()
                    rec["name"] = ev["name"]
            else:
                print(f"  already handled: {ev['name']}")
            continue

        # New card inside the window.
        new_id = create_event(ev)
        if new_id:
            state["events"][ev["espn_id"]] = {
                "discord_id": new_id,
                "start": ev["start"].isoformat(),
                "name": ev["name"],
            }
            announce(ev, new_id)
            created += 1

    save_state(state)
    print(f"\nDone. {created} new event(s) created.")


if __name__ == "__main__":
    main()
