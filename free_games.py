#!/usr/bin/env python3
"""
FREE GAME ALERT - WikyWiky Studios
Posts Epic Games Store weekly freebies to a Discord webhook, with links.

Runs daily. Remembers what it already announced in seen.json, so a game is
posted once when it goes free, not every day for a week.

Flags:
  --dry     print what it would post, send nothing
  --force   ignore seen.json and repost everything currently free
  --test    send a single "online" message
"""

import json
import os
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
SEEN_FILE = "seen.json"
BRAND = "FreeGameAlert"


# ── fetching ────────────────────────────────────────────────────────────────

def fetch_feed():
    req = urllib.request.Request(
        FEED, headers={"User-Agent": "Mozilla/5.0 (FreeGameAlert/1.0)"}
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


def store_link(element):
    """Epic scatters the URL slug across several fields depending on the item."""
    slug = element.get("productSlug") or element.get("urlSlug")

    if not slug:
        for mapping in element.get("offerMappings") or []:
            if mapping.get("pageSlug"):
                slug = mapping["pageSlug"]
                break

    if not slug:
        mappings = (element.get("catalogNs") or {}).get("mappings") or []
        for mapping in mappings:
            if mapping.get("pageSlug"):
                slug = mapping["pageSlug"]
                break

    if not slug:
        return f"{STORE}/free-games"

    slug = slug.split("/")[0]
    kind = "bundles" if element.get("offerType") == "BUNDLE" else "p"
    return f"{STORE}/{kind}/{slug}"


def artwork(element):
    images = element.get("keyImages") or []
    preferred = ("OfferImageWide", "DieselStoreFrontWide", "featuredMedia",
                 "OfferImageTall", "Thumbnail")
    for want in preferred:
        for image in images:
            if image.get("type") == want and image.get("url"):
                return image["url"]
    return images[0].get("url") if images and images[0].get("url") else None


def key_for(element, end):
    ident = element.get("id") or element.get("title") or "?"
    stamp = end.date().isoformat() if end else "?"
    return f"{ident}|{stamp}"


# ── output ──────────────────────────────────────────────────────────────────

def build_embed(element, end):
    title = element.get("title") or "Untitled"
    desc = (element.get("description") or "").strip()
    if len(desc) > 280:
        desc = desc[:277].rstrip() + "..."

    embed = {
        "title": f"🎮 {title} — FREE",
        "url": store_link(element),
        "description": desc,
        "color": 0x0074E4,
    }

    image = artwork(element)
    if image:
        embed["image"] = {"url": image}

    if end:
        local = end.astimezone(LOCAL_TZ)
        embed["footer"] = {"text": f"Claim before {local:%a %b %d, %-I:%M %p %Z}"}

    return embed


def post(url, embeds, content=None):
    payload = {
        "username": BRAND,
        "allowed_mentions": {"parse": []},
    }
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "FreeGameAlert/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        print(f"posted ({res.status}) with {len(embeds)} embed(s)")


# ── state ───────────────────────────────────────────────────────────────────

def load_seen():
    try:
        with open(SEEN_FILE) as fh:
            data = json.load(fh)
            return set(data) if isinstance(data, list) else set()
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    # Keep the file from growing forever.
    with open(SEEN_FILE, "w") as fh:
        json.dump(sorted(seen)[-120:], fh, indent=1)


# ── main ────────────────────────────────────────────────────────────────────

def main():
    dry = "--dry" in sys.argv
    force = "--force" in sys.argv
    webhook = os.environ.get("DISCORD_WEBHOOK", "").strip()

    if not webhook and not dry:
        print("DISCORD_WEBHOOK is not set.", file=sys.stderr)
        return 1

    if "--test" in sys.argv:
        post(webhook, [{"title": "FreeGameAlert is online 🎮",
                        "color": 0x0074E4}])
        return 0

    try:
        feed = fetch_feed()
    except Exception as err:
        print(f"Could not reach Epic: {err}", file=sys.stderr)
        return 1

    items = elements(feed)
    if not items:
        print("Epic returned no elements. Their response shape may have changed.",
              file=sys.stderr)
        print(f"Top-level keys seen: {list(feed.keys())}", file=sys.stderr)
        return 1

    print(f"Epic returned {len(items)} catalog entries.")

    now = datetime.now(timezone.utc)
    seen = set() if force else load_seen()
    fresh, embeds = [], []

    for element in items:
        free, end = is_free_now(element, now)
        if not free or was_already_free(element):
            continue

        key = key_for(element, end)
        title = element.get("title", "?")

        if key in seen:
            print(f"  already announced: {title}")
            continue

        print(f"  NEW: {title} -> {store_link(element)}")
        embeds.append(build_embed(element, end))
        fresh.append(key)

    if not embeds:
        print("Nothing new to announce.")
        return 0

    header = ("**Free on Epic right now** 🎉" if len(embeds) > 1
              else "**Free on Epic right now** 🎉")

    if dry:
        print("\n--- dry run, not posting ---")
        print(json.dumps(embeds, indent=2)[:2000])
        return 0

    try:
        post(webhook, embeds[:10], content=header)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:400]
        print(f"Discord rejected the post: HTTP {err.code} {body}", file=sys.stderr)
        return 1

    seen.update(fresh)
    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
