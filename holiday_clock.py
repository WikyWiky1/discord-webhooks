#!/usr/bin/env python3
"""
HOLIDAY CLOCK - WikyWiky Studios
Posts holiday countdowns to a Discord webhook. Runs once a day on GitHub's
servers. No state file, no database: the schedule is pure date math, so it
works for 2027, 2028, 2050 with zero maintenance.

Easter and Thanksgiving are calculated, not hardcoded.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

MILESTONES = (100, 50, 30, 10, 5)


def easter_sunday(year):
    """Western (Catholic) Easter. Anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def thanksgiving(year):
    """Fourth Thursday of November."""
    nov1 = date(year, 11, 1)
    offset = (3 - nov1.weekday() + 7) % 7   # Monday=0, so Thursday=3
    return nov1 + timedelta(days=offset + 21)


HOLIDAYS = (
    # name,                 day-of message,                    color,    date function
    ("Easter",              "Happy Easter! He is risen ✝️🌅",  0xE8C4D8, easter_sunday),
    ("the Fourth of July",  "Happy Fourth of July! 🎆",        0x2E5FBF, lambda y: date(y, 7, 4)),
    ("Halloween",           "Happy Halloween 👻 🎃",            0xE8741C, lambda y: date(y, 10, 31)),
    ("Thanksgiving",        "Happy Thanksgiving! 🦃",           0xA0522D, thanksgiving),
    ("Christmas",           "Merry Christmas! 🎄",              0xC41E3A, lambda y: date(y, 12, 25)),
    ("New Year's Day",      "Happy New Year! 🎊",               0xD4AF37, lambda y: date(y, 1, 1)),
)


def next_occurrence(date_fn, today):
    for year in range(today.year, today.year + 3):
        when = date_fn(year)
        if when >= today:
            return when
    raise RuntimeError("could not resolve next occurrence")


def due_today(today):
    """Everything that should be posted on `today`. Returns [(text, color)]."""
    out = []
    for name, day_of_msg, color, date_fn in HOLIDAYS:
        days = (next_occurrence(date_fn, today) - today).days
        if days == 0:
            out.append((day_of_msg, color))
        elif days in MILESTONES:
            unit = "day" if days == 1 else "days"
            out.append((f"{days} {unit} until {name}", color))
    return out


def post(url, text, color):
    payload = {
        "username": "Holiday Clock",
        "allowed_mentions": {"parse": []},          # never pings anyone
        "embeds": [{"title": text, "color": color}],
        # Want plain text instead of a colored embed?
        # Delete the "embeds" line above and use:  "content": text,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "HolidayClock/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        print(f"posted ({res.status}): {text}")


def main():
    today = date.today()

    # --preview : print the next 13 months, post nothing.
    if "--preview" in sys.argv:
        print("Upcoming posts:\n")
        for i in range(400):
            day = today + timedelta(days=i)
            for text, _ in due_today(day):
                print(f"  {day:%a %b %d %Y}   {text}")
        return 0

    url = os.environ.get("DISCORD_WEBHOOK", "").strip()
    if not url:
        print("DISCORD_WEBHOOK is not set.", file=sys.stderr)
        return 1

    messages = (
        [("Holiday Clock is online 🕰️", 0x8B5CF6)]
        if "--test" in sys.argv
        else due_today(today)
    )

    if not messages:
        print(f"{today}: nothing due today.")
        return 0

    failed = False
    for text, color in messages:
        for attempt in (1, 2, 3):
            try:
                post(url, text, color)
                break
            except urllib.error.HTTPError as err:
                print(f"HTTP {err.code} on attempt {attempt}: {text}", file=sys.stderr)
                if err.code != 429 or attempt == 3:
                    failed = True
                    break
            except Exception as err:
                print(f"attempt {attempt} failed: {err}", file=sys.stderr)
                if attempt == 3:
                    failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
