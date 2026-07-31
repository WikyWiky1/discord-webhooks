#!/usr/bin/env python3
"""
Birthday Bard — Discord Webhook Bot
====================================
Posts a sweet birthday greeting every morning for anyone celebrating that day.
Never mentions age or birth year — just the celebration. 🎂

Environment variables:
  BIRTHDAY_BARD_WEBHOOK   (required)  Discord webhook URL
  BARD_MODE               (optional)  check | online | preview | list
  BARD_NAME               (optional)  name to use with preview mode

Usage:
  python birthday_bard.py                    # normal daily check
  python birthday_bard.py --online           # posts "Birthday Bard is online."
  python birthday_bard.py --preview WikyWiky # preview one person's greeting
  python birthday_bard.py --list             # print upcoming birthdays (no post)
  python birthday_bard.py --force            # run today's check, ignore time gate
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import random
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore


# ══════════════════════════════════════════════════════════════════════
#  🎂  THE BIRTHDAY LIST  —  EDIT THIS PART
# ══════════════════════════════════════════════════════════════════════
#
#  To add someone, copy a line and change it. Only two things are required:
#
#      {"name": "TheirName", "date": "MM/DD"},
#
#  Optional extras you can tack on:
#
#      "id":   "123456789012345678"   -> @pings them (right-click user →
#                                        Copy User ID; needs Developer Mode on)
#      "note": "custom line for them" -> a personal message under the greeting
#
#  Dates are MONTH/DAY. The year is deliberately not stored — the Bard
#  never announces anyone's age.
#
#  Feb 29 birthdays are automatically celebrated on Feb 28 in non-leap years.
#
# ══════════════════════════════════════════════════════════════════════

BIRTHDAYS = [
    {"name": "WikyWiky",     "date": "09/07", "note": "Builder of places."},
    {"name": "Bish",         "date": "09/05", "note": "You got any games on ya phone?\n\nBirthday games??"},
    {"name": "Arctic Fox",   "date": "10/14", "note": "The life of the party - full of laughter, warmth, kind hearted and the kind of peace that only you can bring! 🎂"},
    {"name": "shouldaducked","date": "05/27", "note": "Go spank the candy out of a piñata. You earned it!"},
    {"name": "capnbrittany", "date": "09/26", "note": "Enjoy your special day!"},
    {"name": "SliceofPiza",  "date": "06/10", "note": "A special day for a special man!"},

    # Birthday unknown — January 1st adopted as their official day 💫
    {"name": "MisfitMoxie",  "date": "01/01", "note": "Nobody actually knows when Moxie's birthday is, so we've adopted January 1st as the official one. Every legend needs a good origin story. ✨"},
    {"name": "Hommiefunny",  "date": "01/01", "note": "Another beautiful mystery! January 1st it is. Sharing an adopted birthday with Mox feels exactly right. 🌟"},

    {"name": "Spartan",      "date": "10/31", "note": "A Halloween birthday is objectively elite. Caked up WITH candy. 🎃"},
    {"name": "Mahogany",     "date": "06/09"},
    {"name": "zafguy",       "date": "01/09", "note": "Hope you get a day that\u2019s chill, fun, and exactly what you need. Wishing you a great year ahead."},
    {"name": "Wizurd",       "date": "12/25", "note": "A Christmas birthday! Double the celebration, and you deserve every bit of it. 🎄"},
    {"name": "Seanuh",       "date": "10/30"},
    {"name": "Kahl",         "date": "12/05", "note": "Sir, a second birthday has hit the tower."},
]


# ══════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════

WEBHOOK_URL = os.environ.get("BIRTHDAY_BARD_WEBHOOK", "").strip()

TIMEZONE = ZoneInfo("America/Chicago")
STATE_FILE = Path(__file__).parent / "birthday_state.json"

BOT_NAME = "Birthday Bard"

# Fire window in local Chicago time (wide enough to absorb Actions delay)
FIRE_HOUR_MIN = 9
FIRE_HOUR_MAX = 11

# Soft pastel pink
COLOR_BIRTHDAY = 0xFFB3D9
COLOR_ONLINE = 0xB4E7CE


# ══════════════════════════════════════════════════════════════════════
#  GREETING CONTENT
# ══════════════════════════════════════════════════════════════════════

TITLE_EMOJIS = ["🎂", "🎉", "🎈", "🥳", "✨", "💖", "🌸", "🎀", "🧁", "🍰", "💐", "🌷"]

TITLES = [
    "Happy Birthday, {name}! 🎂",
    "It's {name}'s Birthday! 🎉",
    "Happiest of Birthdays, {name}! 🎈",
    "Today We Celebrate {name}! 🥳",
    "A Very Happy Birthday to {name}! 🌸",
    "Cheers to {name}! 🧁",
]

GREETINGS = [
    "Wishing you the sweetest day filled with cake, laughter, and every single thing that makes you smile. 💖",
    "May today be as lovely and wonderful as you are. You deserve all the good things. 🌸",
    "Sending you the biggest, warmest birthday hug today. Hope it's absolutely magical. 🤍",
    "Here's to a day full of sunshine, sweetness, and people who adore you. 🌷",
    "Wishing you a day that's just as bright and delightful as you make everyone else's. ✨",
    "Hope your day is soft, joyful, and completely wonderful from start to finish. 🎀",
    "May your day be filled with kindness, good company, and an unreasonable amount of cake. 🍰",
    "Sending all the love your way today. Thank you for being exactly who you are. 💕",
    "Wishing you a beautiful day and a year ahead that's even lovelier. 💐",
    "Hope today treats you gently and brings you every bit of happiness you deserve. 🌼",
]

CLOSERS = [
    "Drop a 🎂 to wish them well!",
    "Everybody say happy birthday! 🎉",
    "Show some love in the chat below 💕",
    "React with 🥳 to join the celebration!",
    "Let's make their day a little brighter ✨",
    "Send them your warmest wishes! 🎈",
]

# Little decorative banner strings
BANNERS = [
    "🎈🎂🎉  ・  ✨  ・  🎉🎂🎈",
    "🌸💖🎀  ・  ✨  ・  🎀💖🌸",
    "🧁🍰🎁  ・  ✨  ・  🎁🍰🧁",
    "💐🌷🌼  ・  ✨  ・  🌼🌷💐",
]


# ══════════════════════════════════════════════════════════════════════
#  STATE  (prevents double-posting from the dual cron)
# ══════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("last_run_date", None)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[Birthday Bard] ⚠️  Could not read state ({exc}); starting fresh.")
    return {"last_run_date": None}


def save_state(state: dict) -> None:
    try:
        with STATE_FILE.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        print(f"[Birthday Bard] 💾  State saved (last run: {state['last_run_date']}).")
    except OSError as exc:
        print(f"[Birthday Bard] ⚠️  Could not save state: {exc}")


# ══════════════════════════════════════════════════════════════════════
#  DATE HELPERS
# ══════════════════════════════════════════════════════════════════════

def parse_md(raw: str) -> tuple[int, int] | None:
    """Parse an 'MM/DD' string into (month, day). Returns None if malformed."""
    try:
        parts = str(raw).strip().split("/")
        if len(parts) != 2:
            return None
        month, day = int(parts[0]), int(parts[1])
    except (ValueError, AttributeError):
        return None

    if not (1 <= month <= 12):
        return None
    # 29 is allowed for Feb; leap handling happens at match time
    max_day = 29 if month == 2 else calendar.monthrange(2024, month)[1]
    if not (1 <= day <= max_day):
        return None
    return month, day


def validate_list() -> list[str]:
    """Return a list of human-readable problems with BIRTHDAYS."""
    problems: list[str] = []
    seen_names: set[str] = set()

    for i, person in enumerate(BIRTHDAYS, start=1):
        name = str(person.get("name", "")).strip()
        if not name:
            problems.append(f"Entry #{i}: missing a 'name'.")
            continue
        if name.lower() in seen_names:
            problems.append(f"Entry #{i} ({name}): duplicate name.")
        seen_names.add(name.lower())

        if parse_md(person.get("date", "")) is None:
            problems.append(
                f"Entry #{i} ({name}): bad date {person.get('date')!r} — expected \"MM/DD\"."
            )
    return problems


def celebrates_today(person: dict, today: date) -> bool:
    md = parse_md(person.get("date", ""))
    if md is None:
        return False
    month, day = md

    # Feb 29 birthdays get celebrated Feb 28 in non-leap years
    if month == 2 and day == 29 and not calendar.isleap(today.year):
        month, day = 2, 28

    return today.month == month and today.day == day


def todays_birthdays(today: date) -> list[dict]:
    return [p for p in BIRTHDAYS if celebrates_today(p, today)]


def days_until(person: dict, today: date) -> int | None:
    md = parse_md(person.get("date", ""))
    if md is None:
        return None
    month, day = md
    if month == 2 and day == 29 and not calendar.isleap(today.year):
        month, day = 2, 28

    try:
        upcoming = date(today.year, month, day)
    except ValueError:
        return None
    if upcoming < today:
        try:
            upcoming = date(today.year + 1, month, day)
        except ValueError:
            upcoming = date(today.year + 1, 2, 28)
    return (upcoming - today).days


# ══════════════════════════════════════════════════════════════════════
#  EMBED BUILDERS
# ══════════════════════════════════════════════════════════════════════

def build_birthday_embed(person: dict) -> dict:
    name = person["name"]
    title = random.choice(TITLES).format(name=name)
    greeting = random.choice(GREETINGS)
    closer = random.choice(CLOSERS)
    banner = random.choice(BANNERS)

    description = f"{banner}\n\n{greeting}"

    note = person.get("note")
    if note:
        # Discord blockquotes only span one line, so prefix each line.
        quoted = "\n".join(f"> {line}" if line.strip() else ">"
                           for line in str(note).split("\n"))
        description += f"\n\n{quoted}"

    description += f"\n\n{closer}"

    return {
        "title": title,
        "description": description,
        "color": COLOR_BIRTHDAY,
        "footer": {"text": f"{BOT_NAME} 🎀 | Wishing you the loveliest day"},
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def build_online_embed() -> dict:
    return {
        "title": "🎀 Birthday Bard is online.",
        "description": (
            "Standing by and ready to celebrate! ✨\n\n"
            f"Currently watching over **{len(BIRTHDAYS)}** birthdays. 🎂"
        ),
        "color": COLOR_ONLINE,
        "footer": {"text": f"{BOT_NAME} 🎀 | Test message"},
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def build_payload(people: list[dict]) -> dict:
    """Build one message. Multiple birthdays on the same day share a post."""
    embeds = [build_birthday_embed(p) for p in people[:10]]

    # @ping anyone who has an "id" set
    pings = [f"<@{p['id']}>" for p in people if p.get("id")]
    content = " ".join(pings) if pings else None

    payload: dict = {"embeds": embeds}
    if content:
        payload["content"] = content
        payload["allowed_mentions"] = {"parse": ["users"]}
    return payload


# ══════════════════════════════════════════════════════════════════════
#  WEBHOOK
# ══════════════════════════════════════════════════════════════════════

def fire_webhook(payload: dict) -> bool:
    if not WEBHOOK_URL:
        print("[Birthday Bard] ❌  BIRTHDAY_BARD_WEBHOOK is not set.")
        return False
    try:
        r = requests.post(
            WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
    except requests.RequestException as exc:
        print(f"[Birthday Bard] ❌  Request failed: {exc}")
        return False

    if r.status_code in (200, 204):
        print("[Birthday Bard] ✅  Webhook fired successfully.")
        return True

    print(f"[Birthday Bard] ❌  Discord returned {r.status_code}: {r.text[:400]}")
    return False


# ══════════════════════════════════════════════════════════════════════
#  MODES
# ══════════════════════════════════════════════════════════════════════

def mode_online() -> int:
    print("[Birthday Bard] 🎀  Online test — firing now.")
    return 0 if fire_webhook({"embeds": [build_online_embed()]}) else 1


def mode_preview(name: str) -> int:
    target = name.strip().lower()
    match = next((p for p in BIRTHDAYS if p["name"].lower() == target), None)

    if not match:
        close = [p["name"] for p in BIRTHDAYS if target in p["name"].lower()]
        print(f"[Birthday Bard] ❌  No one named {name!r} on the list.")
        if close:
            print(f"[Birthday Bard] 💡  Did you mean: {', '.join(close)}?")
        else:
            print(f"[Birthday Bard] 📋  Names: {', '.join(p['name'] for p in BIRTHDAYS)}")
        return 1

    print(f"[Birthday Bard] 👀  Previewing greeting for {match['name']}.")
    return 0 if fire_webhook(build_payload([match])) else 1


def mode_list() -> int:
    today = datetime.now(TIMEZONE).date()
    rows = []
    for p in BIRTHDAYS:
        d = days_until(p, today)
        rows.append((d if d is not None else 9999, p))
    rows.sort(key=lambda x: x[0])

    print(f"\n🎂  Birthday list — {len(BIRTHDAYS)} people (today is {today:%b %d})\n")
    print(f"    {'NAME':<16} {'DATE':<8} {'UP NEXT IN'}")
    print("    " + "─" * 40)
    for d, p in rows:
        when = "today! 🎉" if d == 0 else (f"{d} days" if d != 9999 else "⚠️ bad date")
        print(f"    {p['name']:<16} {p['date']:<8} {when}")
    print()
    return 0


def should_fire(state: dict) -> bool:
    now = datetime.now(TIMEZONE)
    today = now.strftime("%Y-%m-%d")

    if state.get("last_run_date") == today:
        print(f"[Birthday Bard] ⏭️  Already ran today ({today}). Skipping.")
        return False

    if not (FIRE_HOUR_MIN <= now.hour <= FIRE_HOUR_MAX):
        print(
            f"[Birthday Bard] ⏳  Outside fire window "
            f"(local time {now.strftime('%H:%M')} CT). Skipping."
        )
        return False

    return True


def mode_check(forced: bool) -> int:
    state = load_state()

    if not forced and not should_fire(state):
        return 0

    today = datetime.now(TIMEZONE).date()
    people = todays_birthdays(today)

    # Record the run either way, so the second cron doesn't re-check
    state["last_run_date"] = today.strftime("%Y-%m-%d")

    if not people:
        print(f"[Birthday Bard] 🌙  No birthdays today ({today:%b %d}). Resting.")
        save_state(state)
        return 0

    names = ", ".join(p["name"] for p in people)
    print(f"[Birthday Bard] 🎂  Birthday today ({today:%b %d}): {names}")

    if not fire_webhook(build_payload(people)):
        return 1

    save_state(state)
    return 0


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="Birthday Bard — Discord Webhook Bot")
    parser.add_argument("--online", action="store_true",
                        help='Post "Birthday Bard is online." as a test.')
    parser.add_argument("--preview", metavar="NAME",
                        help="Post a preview greeting for one person.")
    parser.add_argument("--list", action="store_true",
                        help="Print the birthday list to the console (posts nothing).")
    parser.add_argument("--force", action="store_true",
                        help="Run today's check, ignoring the time gate.")
    args = parser.parse_args()

    # Env vars let the GitHub Actions workflow drive the same modes
    env_mode = os.environ.get("BARD_MODE", "").strip().lower()
    env_name = os.environ.get("BARD_NAME", "").strip()

    # Validate the list before doing anything else
    problems = validate_list()
    if problems:
        print("[Birthday Bard] ⚠️  Problems found in BIRTHDAYS:")
        for p in problems:
            print(f"    • {p}")
        print("[Birthday Bard] ⚠️  Those entries will be skipped.\n")

    if args.list or env_mode == "list":
        return mode_list()

    if args.online or env_mode == "online":
        return mode_online()

    name = args.preview or (env_name if env_mode == "preview" else None)
    if name:
        return mode_preview(name)

    return mode_check(forced=args.force or env_mode == "force")


if __name__ == "__main__":
    sys.exit(main())
