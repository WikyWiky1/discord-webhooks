#!/usr/bin/env python3
"""
Movie Muppet — Discord Webhook Bot
===================================
Fires every Saturday ~11:30 AM CST/CDT with a random movie suggestion.

Sources:
  1. TMDB live discovery — filtered to what's actually streaming on
     Netflix / Prime / Hulu / Disney+ / Max / Peacock / Paramount+ / AppleTV+
  2. Curated fallback list of house favorites

Never repeats a title until the entire pool is exhausted (tracked in
movie_history.json, auto-committed by the GitHub Actions workflow).

Environment variables:
  MOVIE_MUPPET_WEBHOOK   (required)  Discord webhook URL
  TMDB_API_KEY           (optional)  TMDB v3 API key — enables live streaming search
  FORCE_SUGGEST          (optional)  "true" to bypass the time gate

Usage:
  python movie_muppet.py              # respects schedule gate
  python movie_muppet.py --suggest    # fire immediately
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore


# ══════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════

WEBHOOK_URL = os.environ.get("MOVIE_MUPPET_WEBHOOK", "").strip()
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "").strip()

TIMEZONE = ZoneInfo("America/Chicago")
HISTORY_FILE = Path(__file__).parent / "movie_history.json"

BOT_NAME = "Movie Muppet"
BOT_AVATAR = "https://i.imgur.com/4M34hi2.png"

# Fire window (local Chicago time) — wide enough to absorb GitHub Actions delay
FIRE_WEEKDAY = 5      # 0=Mon ... 5=Sat
FIRE_HOUR_MIN = 11
FIRE_HOUR_MAX = 13

# Chance the pick comes from live TMDB streaming data vs. the curated list
TMDB_PICK_CHANCE = 0.55

# ── TMDB constants ────────────────────────────────────────────────────
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w500"

# US streaming provider IDs
PROVIDERS = {
    8: "Netflix",
    9: "Prime Video",
    15: "Hulu",
    337: "Disney+",
    1899: "HBO Max",
    386: "Peacock",
    531: "Paramount+",
    350: "Apple TV+",
}

GENRES = {
    "horror": 27,
    "comedy": 35,
    "action": 28,
    "thriller": 53,
    "crime": 80,
}

# Genre weighting by month. October = horror season.
GENRE_WEIGHTS_NORMAL = {
    "horror": 35,
    "comedy": 30,
    "action": 20,
    "thriller": 10,
    "crime": 5,
}
GENRE_WEIGHTS_HALLOWEEN = {
    "horror": 75,
    "thriller": 12,
    "comedy": 8,
    "action": 5,
}

# Blacklist of overexposed "heavy hitter" titles TMDB loves to surface
HEAVY_HITTER_BLOCKLIST = {
    "the godfather", "the shawshank redemption", "forrest gump",
    "the dark knight", "inception", "interstellar", "titanic",
    "avatar", "avengers: endgame", "avengers: infinity war",
    "the avengers", "jurassic park", "star wars", "the lion king",
    "frozen", "frozen ii", "toy story", "the matrix", "gladiator",
    "schindler's list", "the lord of the rings: the fellowship of the ring",
    "the lord of the rings: the two towers",
    "the lord of the rings: the return of the king",
    "harry potter and the sorcerer's stone", "spider-man: no way home",
    "top gun: maverick", "barbie", "oppenheimer", "joker",
    "black panther", "iron man", "deadpool", "deadpool & wolverine",
}


# ══════════════════════════════════════════════════════════════════════
#  CURATED FALLBACK POOL  (title, year, poster_url | None)
# ══════════════════════════════════════════════════════════════════════

CURATED = [
    # ── CULT CLASSICS ─────────────────────────────────────────────────
    ("Reservoir Dogs", 1992, "/ix8B3vMh8eTr1L1BTZL2vHHJzMz.jpg"),
    ("Dogma", 1999, "/A7VONAfNZ5OPLkLqFXl3cexHBE9.jpg"),
    ("Clerks", 1994, "/pPNjqBDGU4GBIP6QoWPqXagEP9t.jpg"),
    ("Mallrats", 1995, "/4Pk4WdMdMBrJRqTPL2AnYFqrwGk.jpg"),
    ("Jay and Silent Bob Strike Back", 2001, "/frWUzEsOHl62GdqJHHuIsSDfN5.jpg"),
    ("Office Space", 1999, "/8EWD0HNhZuJGEBGHK6jXLGvM1K8.jpg"),
    ("The Big Lebowski", 1998, "/fhCOvY4a4WDvMV3iG8AUJPF8GEy.jpg"),
    ("Super Troopers", 2001, "/k7NfVJVYGzSB5mqNwPFuyCMsF3H.jpg"),
    ("Half Baked", 1998, "/a7LGnwnFPJbVTbCYaZK3VEJiYGO.jpg"),
    ("Dazed and Confused", 1993, "/4KHDLtYJuNpkMSMHZ2bkMJcFSvY.jpg"),
    ("Fear and Loathing in Las Vegas", 1998, "/r9sWNVSmb7hBkSASoJkAhMoJpP3.jpg"),
    ("Idiocracy", 2006, "/pMxrTOxvSCSTUEsGWZoW9wYTAgS.jpg"),
    ("Boondock Saints", 1999, "/mLB1jNAxDwGbOxDzvhLbnGCGkbF.jpg"),
    ("Snatch", 2000, "/56mOJth6DJ6JhgoE2jtpilVqJO.jpg"),
    ("Lock, Stock and Two Smoking Barrels", 1998, "/8kSUuMTS5Ho2FKCn7GbxfSpvpaC.jpg"),

    # ── RAUNCHY / DUMB COMEDY ─────────────────────────────────────────
    ("Little Nicky", 2000, "/5AoHkWAiVw8QdFnpZGq5V1JLOPM.jpg"),
    ("Tommy Boy", 1995, "/zUYHgUhm0HNr60JkD1wFkAF5HoN.jpg"),
    ("Black Sheep", 1996, "/oEoKHVZHRETgoCbqXNl6IuZmPP1.jpg"),
    ("Billy Madison", 1995, "/mzDMFjzABM5KyUFJHKlPnfFvNKX.jpg"),
    ("Happy Gilmore", 1996, "/7oJZtNNVzBNYPTDfDdGEi72FhUE.jpg"),
    ("The Waterboy", 1998, "/jqNGZnmWRSjY7ExaROlR7P6pnNs.jpg"),
    ("Big Daddy", 1999, "/7IrX3DUWQVQ64WDCpHqFMCNVBmS.jpg"),
    ("Mr. Deeds", 2002, "/f8cQH2fUhWbIpRY5sYY7sxMOfzV.jpg"),
    ("Click", 2006, "/6kPXGjrWfx5WjuvVqXqnFHHVZ4Y.jpg"),
    ("You Don't Mess with the Zohan", 2008, "/2sPBSNMGipekVJFpXnFTJK4Ts7H.jpg"),
    ("Grown Ups", 2010, "/7dkGM5VxMJ53IHO7Pv4cOWV3uwR.jpg"),
    ("The Ridiculous 6", 2015, "/d1lNJLg0IHIjJL8kp8SJqfZvX4S.jpg"),
    ("Deuce Bigalow: Male Gigolo", 1999, "/9cFXlIHt9yRsQmBB0KXPB63P0Xd.jpg"),
    ("The Animal", 2001, "/nTkQnOTAqJA0VxEwMFkbSBaU3Wb.jpg"),
    ("The Hot Chick", 2002, "/hQKlNMPvOCLxYVWfhwsLBnEbXZa.jpg"),
    ("Joe Dirt", 2001, "/1VfRwJLvCUwjGDKhElqJIJbxFpe.jpg"),
    ("Dirty Work", 1998, "/cbr3Ql8Cf1YVBI0vOt8DVvw5HwB.jpg"),
    ("Anchorman", 2004, "/nHCJlFY5UlJQUKzHuOQdxkpXI2g.jpg"),
    ("Step Brothers", 2008, "/lGGa2dMqt9sPn6Ouo1zAMWJOqbz.jpg"),
    ("Talladega Nights", 2006, "/r2ERrNRFX5ASYNO8lN5C3wjmMpn.jpg"),
    ("Blades of Glory", 2007, "/qHJnYn3hMmrF6jJXy1I5nwLPVIp.jpg"),
    ("Semi-Pro", 2008, "/uFNVpzFXqNIcHRxLmDFuLtWkFZa.jpg"),
    ("The Other Guys", 2010, "/e4vFPCKzXhZMLuTdxrIeMbjSJEB.jpg"),
    ("Old School", 2003, "/pEmYCSMPQNlUXbNSQe2rXrpJVLJ.jpg"),
    ("Superbad", 2007, "/ek8e8txUyGD8r17oSBMYMl4SDIX.jpg"),
    ("Pineapple Express", 2008, "/wayHHq74HGXF6UtdH0GKCY8KqSP.jpg"),
    ("Tropic Thunder", 2008, "/3MHCVjmV0JhaSzANpCrKHXMEJDZ.jpg"),
    ("Borat", 2006, "/cosFnAPdoIRLMJUFbwXZPpj3Mfa.jpg"),
    ("Jackass: The Movie", 2002, "/glFfS6aECJQp5SBSRq1mODST8Aw.jpg"),
    ("Jackass Number Two", 2006, "/dfzMlE5BKLA25MOo98C5TAfTOay.jpg"),
    ("Rickey Stanicky", 2024, "/6Vy3khrpKLEPFPpJI1zCEr12RiY.jpg"),
    ("Wedding Crashers", 2005, "/e6vGqfoZAaGCkxBQeaB5AwB3bTb.jpg"),
    ("Role Models", 2008, "/2NfaCPTvJmDzIWCG7wV4yQnJHNY.jpg"),

    # ── ACTION ────────────────────────────────────────────────────────
    ("Die Hard", 1988, "/yFihWxQcmqcaBR31QM6Y8gT6aYV.jpg"),
    ("Lethal Weapon", 1987, "/fNOH9f1aA7XRToPj5XGh4wqFRkQ.jpg"),
    ("The Rock", 1996, "/bFbFzMPPeqWqYcXhpSJ3cdfglzS.jpg"),
    ("Con Air", 1997, "/mZm7RoHiD0ykJniGxVWAfcXGGEE.jpg"),
    ("Face/Off", 1997, "/gMFQFJPcq6AhGE2ioRuBvW27F5h.jpg"),
    ("Speed", 1994, "/vGBCMBogQFTfKMhUe4QmJ2EGAaK.jpg"),
    ("Point Break", 1991, "/lFD3fFAQkqJxlcl8n1blnYRlKPw.jpg"),
    ("Predator", 1987, "/5xSc3Mfn9ATJN18SkOGgbCrgRcY.jpg"),
    ("Total Recall", 1990, "/wuSBizFkNyQsNVWLcGgmIkkGnHc.jpg"),
    ("Commando", 1985, "/eiPjOsHvRAXWLb3EEBrCq2SB4Ux.jpg"),
    ("Road House", 1989, "/eBGRnLbBaEnwZmnvIkKa9uAqUFa.jpg"),
    ("Demolition Man", 1993, "/mnahpvbLcgMdrEXbrHhpalcQBpo.jpg"),
    ("Escape from New York", 1981, "/g5Hbr0Ai3O2ZoJP3OpDRyzGnHRj.jpg"),
    ("Big Trouble in Little China", 1986, "/qCr8bnCQMg3hMbG4gDBrEmxfSMj.jpg"),
    ("They Live", 1988, "/hMDgd0FLQoMbmz2fSSAxRvSlZzS.jpg"),

    # ── HORROR — TERRIFIER / ROB ZOMBIE ───────────────────────────────
    ("Terrifier", 2016, "/6P3N2EboXwEFuS38dFBX4qy40tF.jpg"),
    ("Terrifier 2", 2022, "/qLg8aFnqmOkLHzSqWvhHq5fGnAE.jpg"),
    ("Terrifier 3", 2024, "/ju8SHBiNFhEfRpFAMRsJHRJjT8Q.jpg"),
    ("All Hallows' Eve", 2013, "/5DzMtDDWLYbfEgOSVDgJPQZKMlC.jpg"),
    ("House of 1000 Corpses", 2003, "/kMJgMFvhALZnkh08PVGHaVvVCAS.jpg"),
    ("The Devil's Rejects", 2005, "/lEy3dOuCJkOETuePWd1QH4mHEay.jpg"),
    ("3 from Hell", 2019, "/mFvX5mQZIBEBQ9OyCKnZBVLiQkW.jpg"),
    ("Halloween (Rob Zombie)", 2007, "/2lxC8vEQAuFGlVlJ3DBLBJ9mXji.jpg"),
    ("Halloween II (Rob Zombie)", 2009, "/1G1PKZV7LHMoU2jE3SXqRJhsNlL.jpg"),
    ("The Lords of Salem", 2012, "/hMbHBKaR3UuqNVZNhXpTHcX5WdH.jpg"),
    ("31", 2016, "/sxfMfPPfOkJQe1VCOuJJIkxqHLJ.jpg"),
    ("The Munsters", 2022, "/vCKPsHplBnn3EmSHDoBSbEjnG4x.jpg"),

    # ── HORROR — CLASSICS & MODERN ────────────────────────────────────
    ("Halloween", 1978, "/qVBIAGTy1MlZkZFMEFJxqFf1vOt.jpg"),
    ("A Nightmare on Elm Street", 1984, "/lhBjspFotJ8hiAaWwEuKEsqGbQx.jpg"),
    ("Friday the 13th", 1980, "/HtSL4hVNUvJNVgRGHdiEm84mGFD.jpg"),
    ("The Texas Chain Saw Massacre", 1974, "/gMOlnVP4TxGnPoCHl2uaSDHcFEa.jpg"),
    ("Evil Dead", 1981, "/byPLCtMPJoTVTYAMsLXApTOEAGj.jpg"),
    ("Evil Dead II", 1987, "/rKzTh2KEmHLp1hWA4AiBxnCx3y2.jpg"),
    ("Army of Darkness", 1992, "/75aGgWVNkOqoHetpwFmFBtxHVnj.jpg"),
    ("The Thing", 1982, "/tzGY49kseSE9QAKk47uuDGwnSCu.jpg"),
    ("Hellraiser", 1987, "/mFrarNC1nQyE8Ub5tzRgbXAxAsw.jpg"),
    ("Re-Animator", 1985, "/vPl2LuHGGjIQeFB5EnJmQKGCXAf.jpg"),
    ("Suspiria", 1977, "/nRHAlGXWTVQoLXXMFYPMKqzLc8O.jpg"),
    ("Scream", 1996, "/aYN4OF7AFLMPOb8EMJMD9bFsVE4.jpg"),
    ("Saw", 2004, "/i4OnoB5BhxBE11K0S3M2VoJhGC7.jpg"),
    ("The Descent", 2005, "/eiPjOsHvRAXWLb3EEBrCq2SB4Ux.jpg"),
    ("Insidious", 2010, "/7bXFWmDJDmJlHHCe6IaODFTkzLY.jpg"),
    ("Sinister", 2012, "/6I9k6qWFPrgkp3HgbZf7OijJCqA.jpg"),
    ("The Conjuring", 2013, "/wVYREutTvI2tmxr6ujrHT704wGF.jpg"),
    ("It Follows", 2014, "/4tqlAbXRoNaxJPCJoRb0zBIcbNH.jpg"),
    ("The Witch", 2015, "/fK8MzpjRBEj5mLjJoAkJJBxz0Oc.jpg"),
    ("Get Out", 2017, "/tFXcEccSQMf3lfhfXKSU9iRBpa3.jpg"),
    ("Hereditary", 2018, "/4HWAQu28e2yaWrtupFPGFkdNU7V.jpg"),
    ("Mandy", 2018, "/hn0nNvtPNAuMz7we0Xvtf9EiMry.jpg"),
    ("Midsommar", 2019, "/7LEI8ulZzO5gy9Ww2NVCrKmC9SM.jpg"),
    ("Barbarian", 2022, "/ig4gjJjkGDFy1VIrCHmekzpSwVX.jpg"),
    ("Talk to Me", 2022, "/kdPMUd184oPkjbn4ArpKjPfYoNP.jpg"),
    ("Longlegs", 2024, "/mE7bMFXqGE2h7mTAWFRFCBJgYhL.jpg"),
    ("The Substance", 2024, "/lqoMzCcZYEFK729d6qzt349fB4o.jpg"),
    ("In a Violent Nature", 2024, "/dRIMoXQGjVCJTLYtSNfC4KUcSHF.jpg"),
    ("Late Night with the Devil", 2023, "/9NPBcnGRGSWlWWzLKKcyBEEjLRK.jpg"),
    ("Sinners", 2025, None),
    ("Weapons", 2025, None),

    # ── ODDBALLS & DRAMA (light touch) ────────────────────────────────
    ("Uncut Gems", 2019, "/5UHR0bIV9sLlAVqdBxnMCOiGYHq.jpg"),
    ("Punch-Drunk Love", 2002, "/ylzrqFIAkOdqfJrSqXVVBPOknFh.jpg"),
    ("Cocaine Bear", 2023, "/gOnkqE5gBOobgSmjOIdnqCVsqE5.jpg"),
    ("The Menu", 2022, "/v66has9HOVQFoNL3l5M7DznXCGO.jpg"),
    ("M3GAN", 2022, "/d9nBoowhjiiYc4FBNtQkPY7c11H.jpg"),
    ("Nightcrawler", 2014, "/j9HrX8f7GbZQm1BrBiR40uFQZSb.jpg"),
    ("Falling Down", 1993, "/xUOA8OjhAHiCbCNCcNvbNsxDrp0.jpg"),
]

EMOJIS = ["🎬", "🍿", "🎥", "👻", "💀", "🎭", "🔪", "😂", "🤣", "🎞️", "🩸", "🕶️"]

INTROS = [
    "What's on the docket this weekend?",
    "Your couch is calling. Answer it.",
    "Saturday night sorted. You're welcome.",
    "The Muppet has spoken.",
    "Clear your schedule. Pop the popcorn.",
    "No excuses. Movie night is mandatory.",
    "Randomized by the cinema gods.",
    "Time to dim the lights and question your life choices.",
    "Fresh outta the barrel. Enjoy.",
    "Don't fight it. Just press play.",
]

OUTROS = [
    "Hop on, grab a snack, and let's see if this one slaps. 🍕",
    "Snacks are on you. Judgment is on us. 🍺",
    "React with 🍿 if you're in.",
    "If it's bad, blame RNG. If it's great, thank the Muppet.",
    "Bring your own commentary track.",
    "Nobody's allowed to pause for a bathroom break. Hold it.",
]


# ══════════════════════════════════════════════════════════════════════
#  HISTORY  (no-repeat tracking)
# ══════════════════════════════════════════════════════════════════════

def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            with HISTORY_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("seen", [])
            data.setdefault("last_run_date", None)
            data.setdefault("cycles", 0)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[Movie Muppet] ⚠️  Could not read history ({exc}); starting fresh.")
    return {"seen": [], "last_run_date": None, "cycles": 0}


def save_history(history: dict) -> None:
    try:
        with HISTORY_FILE.open("w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        print(f"[Movie Muppet] 💾  History saved ({len(history['seen'])} titles seen).")
    except OSError as exc:
        print(f"[Movie Muppet] ⚠️  Could not save history: {exc}")


def norm_key(title: str, year) -> str:
    """Stable dedupe key — case/whitespace insensitive."""
    return f"{str(title).strip().lower()}::{year}"


# ══════════════════════════════════════════════════════════════════════
#  TMDB LIVE STREAMING SEARCH
# ══════════════════════════════════════════════════════════════════════

def weighted_genre() -> tuple[str, int]:
    month = datetime.now(TIMEZONE).month
    weights = GENRE_WEIGHTS_HALLOWEEN if month == 10 else GENRE_WEIGHTS_NORMAL
    names = list(weights.keys())
    picked = random.choices(names, weights=[weights[n] for n in names], k=1)[0]
    return picked, GENRES[picked]


def tmdb_discover(seen: set[str]) -> dict | None:
    """
    Query TMDB /discover/movie filtered to titles currently streaming on the
    major US services. Returns a normalized dict or None.
    """
    if not TMDB_API_KEY:
        return None

    genre_name, genre_id = weighted_genre()
    provider_ids = "|".join(str(p) for p in PROVIDERS)

    # Rotate through pages so we don't always see the same top-20
    page = random.randint(1, 5)

    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "region": "US",
        "watch_region": "US",
        "with_watch_providers": provider_ids,
        "with_watch_monetization_types": "flatrate|free|ads",
        "with_genres": genre_id,
        "sort_by": "popularity.desc",
        "vote_count.gte": 120,
        "vote_average.gte": 5.5,
        "include_adult": "false",
        "page": page,
    }

    try:
        r = requests.get(f"{TMDB_BASE}/discover/movie", params=params, timeout=20)
        r.raise_for_status()
        results = r.json().get("results", [])
    except (requests.RequestException, ValueError) as exc:
        print(f"[Movie Muppet] ⚠️  TMDB discover failed: {exc}")
        return None

    random.shuffle(results)

    for m in results:
        title = (m.get("title") or "").strip()
        if not title:
            continue
        if title.lower() in HEAVY_HITTER_BLOCKLIST:
            continue

        release = m.get("release_date") or ""
        year = release[:4] if len(release) >= 4 else "????"

        if norm_key(title, year) in seen:
            continue

        return {
            "title": title,
            "year": year,
            "poster": f"{TMDB_IMG}{m['poster_path']}" if m.get("poster_path") else None,
            "overview": (m.get("overview") or "").strip(),
            "rating": m.get("vote_average"),
            "tmdb_id": m.get("id"),
            "genre": genre_name,
            "source": "tmdb",
        }

    print(f"[Movie Muppet] ℹ️  TMDB page {page} ({genre_name}) had no unseen titles.")
    return None


def tmdb_watch_providers(tmdb_id: int) -> list[str]:
    """Return the list of US streaming services carrying this title."""
    if not TMDB_API_KEY or not tmdb_id:
        return []
    try:
        r = requests.get(
            f"{TMDB_BASE}/movie/{tmdb_id}/watch/providers",
            params={"api_key": TMDB_API_KEY},
            timeout=15,
        )
        r.raise_for_status()
        us = r.json().get("results", {}).get("US", {})
    except (requests.RequestException, ValueError):
        return []

    names: list[str] = []
    for bucket in ("flatrate", "free", "ads"):
        for p in us.get(bucket, []):
            name = p.get("provider_name")
            if name and name not in names:
                names.append(name)
    return names[:6]


def tmdb_lookup(title: str, year) -> dict | None:
    """Enrich a curated pick with a live poster / overview / providers."""
    if not TMDB_API_KEY:
        return None
    # Strip parenthetical director notes, e.g. "Halloween (Rob Zombie)"
    clean = title.split(" (")[0].strip()
    try:
        r = requests.get(
            f"{TMDB_BASE}/search/movie",
            params={
                "api_key": TMDB_API_KEY,
                "query": clean,
                "year": year,
                "include_adult": "false",
            },
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
    except (requests.RequestException, ValueError):
        return None

    return results[0] if results else None


# ══════════════════════════════════════════════════════════════════════
#  PICK LOGIC
# ══════════════════════════════════════════════════════════════════════

def pick_from_curated(seen: set[str]) -> dict | None:
    available = [
        (t, y, p) for (t, y, p) in CURATED if norm_key(t, y) not in seen
    ]
    if not available:
        return None

    title, year, poster_path = random.choice(available)
    return {
        "title": title,
        "year": year,
        "poster": f"{TMDB_IMG}{poster_path}" if poster_path else None,
        "overview": "",
        "rating": None,
        "tmdb_id": None,
        "genre": None,
        "source": "curated",
    }


def choose_movie(history: dict) -> tuple[dict | None, dict]:
    """Pick an unseen movie. Resets the cycle if everything's been seen."""
    seen = set(history["seen"])

    order = ["tmdb", "curated"] if random.random() < TMDB_PICK_CHANCE else ["curated", "tmdb"]

    for attempt in range(2):
        for src in order:
            movie = tmdb_discover(seen) if src == "tmdb" else pick_from_curated(seen)
            if movie:
                return movie, history

        # Everything exhausted — wipe the slate and go again
        if attempt == 0:
            history["cycles"] += 1
            history["seen"] = []
            seen = set()
            print(
                f"[Movie Muppet] 🔄  Full pool exhausted — starting cycle "
                f"#{history['cycles'] + 1}. History reset."
            )

    return None, history


# ══════════════════════════════════════════════════════════════════════
#  DISCORD EMBED
# ══════════════════════════════════════════════════════════════════════

def build_payload(movie: dict) -> dict:
    emoji = random.choice(EMOJIS)
    intro = random.choice(INTROS)
    outro = random.choice(OUTROS)

    title = movie["title"]
    year = movie["year"]
    poster = movie["poster"]
    overview = movie["overview"]
    rating = movie["rating"]
    tmdb_id = movie["tmdb_id"]

    # Enrich curated picks with live TMDB data when possible
    if movie["source"] == "curated" and TMDB_API_KEY:
        hit = tmdb_lookup(title, year)
        if hit:
            tmdb_id = hit.get("id")
            rating = hit.get("vote_average")
            overview = (hit.get("overview") or "").strip()
            if not poster and hit.get("poster_path"):
                poster = f"{TMDB_IMG}{hit['poster_path']}"

    providers = tmdb_watch_providers(tmdb_id) if tmdb_id else []

    # Trim long synopses
    if overview and len(overview) > 320:
        overview = overview[:317].rsplit(" ", 1)[0] + "…"

    description = f"**{intro}**\n\n"
    if overview:
        description += f"> {overview}\n\n"
    description += outro

    embed: dict = {
        "title": f"{emoji}  {title} ({year})",
        "description": description,
        "color": 0xFF4500,
        "fields": [],
        "footer": {"text": f"{BOT_NAME} 🎬 | Weekly randomized pick"},
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if providers:
        embed["fields"].append(
            {"name": "📺 Streaming On", "value": " • ".join(providers), "inline": False}
        )

    if rating:
        stars = "⭐" * max(1, min(5, round(float(rating) / 2)))
        embed["fields"].append(
            {"name": "Rating", "value": f"{stars}  `{float(rating):.1f}/10`", "inline": True}
        )

    if tmdb_id:
        embed["fields"].append(
            {
                "name": "More Info",
                "value": f"[View on TMDB](https://www.themoviedb.org/movie/{tmdb_id})",
                "inline": True,
            }
        )

    if poster:
        embed["image"] = {"url": poster}

    if not embed["fields"]:
        embed.pop("fields")

    return {"username": BOT_NAME, "avatar_url": BOT_AVATAR, "embeds": [embed]}


def fire_webhook(payload: dict) -> bool:
    if not WEBHOOK_URL:
        print("[Movie Muppet] ❌  MOVIE_MUPPET_WEBHOOK is not set.")
        return False
    try:
        r = requests.post(
            WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
    except requests.RequestException as exc:
        print(f"[Movie Muppet] ❌  Request failed: {exc}")
        return False

    if r.status_code in (200, 204):
        print("[Movie Muppet] ✅  Webhook fired successfully.")
        return True

    print(f"[Movie Muppet] ❌  Discord returned {r.status_code}: {r.text[:400]}")
    return False


# ══════════════════════════════════════════════════════════════════════
#  SCHEDULE GATE
# ══════════════════════════════════════════════════════════════════════

def should_fire(history: dict) -> bool:
    now = datetime.now(TIMEZONE)
    today = now.strftime("%Y-%m-%d")

    if history.get("last_run_date") == today:
        print(f"[Movie Muppet] ⏭️  Already fired today ({today}). Skipping.")
        return False

    if now.weekday() != FIRE_WEEKDAY:
        print(f"[Movie Muppet] ⏳  Not Saturday (it's {now.strftime('%A')}). Skipping.")
        return False

    if not (FIRE_HOUR_MIN <= now.hour <= FIRE_HOUR_MAX):
        print(
            f"[Movie Muppet] ⏳  Outside fire window "
            f"(local time {now.strftime('%H:%M')} CT). Skipping."
        )
        return False

    return True


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="Movie Muppet — Discord Webhook Bot")
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="Fire a suggestion immediately, bypassing the schedule gate.",
    )
    args = parser.parse_args()

    forced = args.suggest or os.environ.get("FORCE_SUGGEST", "").lower() == "true"

    history = load_history()

    if not forced and not should_fire(history):
        return 0

    if forced:
        print("[Movie Muppet] 🚀  Manual trigger — firing now.")

    movie, history = choose_movie(history)

    if not movie:
        print("[Movie Muppet] ❌  Could not find any movie to suggest.")
        return 1

    print(
        f"[Movie Muppet] 🎯  Picked: {movie['title']} ({movie['year']}) "
        f"[source: {movie['source']}"
        + (f", genre: {movie['genre']}" if movie.get("genre") else "")
        + "]"
    )

    payload = build_payload(movie)

    if not fire_webhook(payload):
        return 1

    # Only record the pick after a successful post
    history["seen"].append(norm_key(movie["title"], movie["year"]))
    history["last_run_date"] = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    save_history(history)

    return 0


if __name__ == "__main__":
    sys.exit(main())
