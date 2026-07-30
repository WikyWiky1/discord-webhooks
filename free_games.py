#!/usr/bin/env python3
"""
FREE GAME ALERT - WikyWiky Studios
Posts free PC game giveaways to a Discord webhook, with claim links.

Sources:
  1. Epic Games Store, direct from their own promotions endpoint (most detail)
  2. GamerPower, which aggregates Steam, GOG, itch.io, DRM-free and more

Runs daily. Remembers what it already announced in seen.json, so a game is
posted once when it goes free, not every day for a week. Overlapping offers
(Epic appears in both sources) are collapsed by title.

Giveaway data for non-Epic stores comes from GamerPower.com, whose terms
require visible attribution. The footer link stays.

Flags:
  --dry     print what it would post, send nothing
  --force   ignore seen.json and repost everything currently free
  --test    send a single "online" message
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Chicago")
except Exception:
    LOCAL_TZ = timezone.utc

FEED = (
    "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
    "?locale=en-US&country=US&allowCountries=US"
)
STORE = "https://store.epicgames.com/en-US"

GAMERPOWER = "https://www.gamerpower.com/api/giveaways?type=game&sort-by=date"
GAMERPOWER_SITE = "https://www.gamerpower.com"

# Which GamerPower platforms to accept. Bare "pc" is deliberately absent -
# it sweeps in key-site signup giveaways and would flood the channel.
WANTED_PLATFORMS = ("steam", "gog", "epic", "itch", "drm-free")

# Prime Gaming has no platform tag of its own, so catch it by name.
PLATFORM_KEYWORDS = ("prime gaming", "amazon prime")

SEEN_FILE = "seen.json"
BRAND = "FreeGameAlert"


# ── fetching ────────────────────────────────────────────────────────────────

def get_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (FreeGameAlert/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def elements(feed):
    """Dig the game list out of the response, tolerating shape changes."""
    try:
        return feed["data"]["Catalog"]["searchStore"]["elements"] or []
    except (KeyError, TypeError):
        return []


# ── parsing ─────────────────────────────────────────────────────────────────

def parse_dt(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_free_now(element, now):
    """True if a 100%-off promo is live right now. Returns (bool, end_datetime)."""
    promos = (element.get("promotions") or {}).get("promotionalOffers") or []
    for block in promos:
        for offer in block.get("promotionalOffers") or []:
            start = parse_dt(offer.get("startDate"))
            end = parse_dt(offer.get("endDate"))
            pct = (offer.get("discountSetting") or {}).get("discountPercentage")
            # Epic uses 0 to mean "price becomes zero".
            if pct not in (0, None):
                continue
            if start and start > now:
                continue
            if end and end < now:
                continue
            return True, end
    return False, None


def was_already_free(element):
    """Skip permanently free-to-play titles - they aren't news."""
    try:
        original = element["price"]["totalPrice"]["originalPrice"]
        return original == 0
    except (KeyError, TypeError):
        return False


HASH_SLUG = re.compile(r"^[0-9a-f]{24,}$", re.I)


def slug_candidates(element):
    """Epic scatters the slug around. Readable ones first, junk hash last."""
    yield element.get("productSlug")
    for mapping in (element.get("catalogNs") or {}).get("mappings") or []:
        yield mapping.get("pageSlug")
    for mapping in element.get("offerMappings") or []:
        yield mapping.get("pageSlug")
    yield element.get("urlSlug")


def store_link(element):
    kind = "bundles" if element.get("offerType") == "BUNDLE" else "p"
    fallback = None

    for candidate in slug_candidates(element):
        if not candidate:
            continue
        candidate = candidate.split("/")[0]
        if HASH_SLUG.match(candidate):
            fallback = fallback or candidate   # keep it, but prefer a real slug
            continue
        return f"{STORE}
