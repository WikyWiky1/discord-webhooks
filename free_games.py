#!/usr/bin/env python3
"""
FREE GAME ALERT - WikyWiky Studios
Posts free PC game giveaways to a Discord webhook, with claim links.

Sources:
  1. Epic Games Store, direct from their own promotions endpoint (most detail)
  2. GamerPower, which aggregates Steam, GOG and more

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

# Which GamerPower platforms to accept. Add "itch" and "drm-free" back if the
# channel feels too quiet; they roughly quadruple the volume with micro-indies.
WANTED_PLATFORMS = ("steam", "gog", "epic")

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
        return f"{STORE}/{kind}/{candidate}"

    if fallback:
        return f"{STORE}/{kind}/{fallback}"
    return f"{STORE}/free-games"


def artwork(element):
    images = element.get("keyImages") or []
    preferred = ("OfferImageWide", "DieselStoreFrontWide", "featuredMedia",
                 "OfferImageTall", "Thumbnail")
    for want in preferred:
        for image in images:
            if image.get("type") == want and image.get("url"):
                return image["url"]
    return images[0].get("url") if images and images[0].get("url") else None


# ── normalised offer ────────────────────────────────────────────────────────

def make_offer(title, url, description, image, ends, store, via=None):
    """One shape for every source, so the posting path stays simple."""
    return {
        "title": (title or "Untitled").strip(),
        "url": url,
        "description": (description or "").strip(),
        "image": image,
        "ends": ends,
        "store": store,
        "via": via,
    }


# GamerPower appends store tags to titles. Stripping them makes cross-source
# dedup work and reads better. Exact matches only - an unrecognised
# parenthetical stays put, since a long title beats a butchered one.
STORE_TAGS = {
    "steam", "epic", "epic games", "epic games store", "gog", "gog.com",
    "itch.io", "itchio", "itch", "indiegala", "indie gala", "drm-free", "drmfree",
    "origin", "uplay", "ubisoft", "ubisoft connect", "ea", "ea app",
    "xbox", "ps4", "ps5", "playstation", "switch", "nintendo", "android", "ios", "pc",
    "prime gaming", "amazon prime", "amazon", "humble", "humble bundle", "alienware",
    "fanatical", "twitch", "microsoft store", "battle.net", "rockstar",
    "legacy games", "gamesplanet", "gleam", "keyhub", "gamehag",
}

PARENTHETICAL = re.compile(r"\s*\(([^()]+)\)")
TRAILING_GIVEAWAY = re.compile(r"\s*(?:(?:key|steam|pc)\s+)?giveaway\s*$", re.I)


def clean_title(raw):
    def drop(match):
        inner = " ".join(match.group(1).split()).lower().strip(" .")
        return "" if inner in STORE_TAGS else match.group(0)
    title = PARENTHETICAL.sub(drop, raw or "")
    title = TRAILING_GIVEAWAY.sub("", title)
    return " ".join(title.split()).strip(" -–—:") or (raw or "Untitled")


def title_key(title):
    """Loose key so the same game from two sources collapses to one post."""
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


# ── source 1: Epic, direct ──────────────────────────────────────────────────

def epic_offers():
    feed = get_json(FEED)
    items = elements(feed)

    if not items:
        print("  Epic returned no elements; their shape may have changed.",
              file=sys.stderr)
        print(f"  top-level keys: {list(feed.keys())}", file=sys.stderr)
        return []

    print(f"  Epic: {len(items)} catalog entries")
    now = datetime.now(timezone.utc)
    offers = []

    for element in items:
        free, end = is_free_now(element, now)
        if not free or was_already_free(element):
            continue
        offers.append(make_offer(
            title=element.get("title"),
            url=store_link(element),
            description=element.get("description"),
            image=artwork(element),
            ends=end,
            store="Epic Games Store",
        ))

    return offers


# ── source 2: GamerPower, aggregated ────────────────────────────────────────

def wanted_platform(entry):
    platforms = (entry.get("platforms") or "").lower()
    if any(tag in platforms for tag in WANTED_PLATFORMS):
        return True
    blob = f"{entry.get('title', '')} {entry.get('description', '')}".lower()
    return any(word in blob for word in PLATFORM_KEYWORDS)


def parse_gp_date(text):
    """GamerPower uses 'YYYY-MM-DD HH:MM:SS', and literal 'N/A' for open-ended."""
    if not text or text.strip().upper() in ("N/A", "NA", ""):
        return None
    try:
        naive = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=timezone.utc)
    except ValueError:
        return parse_dt(text)


def gamerpower_offers():
    data = get_json(GAMERPOWER)

    # 201 means "no active giveaways" and comes back as an object, not a list.
    if not isinstance(data, list):
        print(f"  GamerPower: no active giveaways ({data})")
        return []

    print(f"  GamerPower: {len(data)} game giveaways")
    now = datetime.now(timezone.utc)
    offers = []

    for entry in data:
        if not wanted_platform(entry):
            continue
        if (entry.get("status") or "Active").lower() != "active":
            continue

        ends = parse_gp_date(entry.get("end_date"))
        if ends and ends < now:
            continue

        offers.append(make_offer(
            title=clean_title(entry.get("title")),
            url=entry.get("open_giveaway_url") or entry.get("gamerpower_url"),
            description=entry.get("description"),
            image=entry.get("image") or entry.get("thumbnail"),
            ends=ends,
            store=entry.get("platforms") or "PC",
            via="GamerPower",
        ))

    return offers


# ── output ──────────────────────────────────────────────────────────────────

def build_embed(offer):
    desc = offer["description"]
    if len(desc) > 280:
        desc = desc[:277].rstrip() + "..."

    embed = {
        "title": f"🎮 {offer['title']} — FREE",
        "url": offer["url"],
        "description": desc,
        "color": 0x0074E4,
    }

    if offer["image"]:
        embed["image"] = {"url": offer["image"]}

    bits = [offer["store"]]
    if offer["ends"]:
        local = offer["ends"].astimezone(LOCAL_TZ)
        bits.append(f"claim before {local:%a %b %d, %-I:%M %p %Z}")
    else:
        bits.append("no listed deadline")
    if offer["via"]:
        bits.append("via GamerPower.com")

    embed["footer"] = {"text": " · ".join(bits)}
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
        json.dump(sorted(seen)[-400:], fh, indent=1)


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

    # Each source is independent. One failing must not silence the other.
    offers, failures = [], []
    for label, source in (("Epic", epic_offers), ("GamerPower", gamerpower_offers)):
        try:
            offers.extend(source())
        except Exception as err:
            failures.append(label)
            print(f"  {label} failed: {err}", file=sys.stderr)

    if failures and len(failures) == 2:
        print("Both sources failed; nothing to do.", file=sys.stderr)
        return 1

    seen = set() if force else load_seen()
    fresh, embeds = [], []
    batch = set()          # collapses duplicates inside this single run

    # Epic is processed first, so its richer entry wins any overlap.
    for offer in offers:
        tkey = title_key(offer["title"])
        stamp = offer["ends"].date().isoformat() if offer["ends"] else "open"
        key = f"{tkey}|{stamp}"

        if tkey in batch:
            print(f"  duplicate in this run, skipping: {offer['title']}")
            continue
        if key in seen or tkey in seen:
            print(f"  already announced: {offer['title']}")
            batch.add(tkey)
            continue

        print(f"  NEW: {offer['title']} [{offer['store']}] -> {offer['url']}")
        embeds.append(build_embed(offer))
        fresh.extend([key, tkey])
        batch.add(tkey)

    if not embeds:
        print("Nothing new to announce.")
        return 0

    header = "**Free games right now** 🎉"
    if any(e.get("footer", {}).get("text", "").endswith("via GamerPower.com")
           for e in embeds):
        header += f"\n-# Giveaway tracking via [GamerPower]({GAMERPOWER_SITE})"

    if dry:
        print("\n--- dry run, not posting ---")
        print(json.dumps(embeds, indent=2, default=str)[:2500])
        return 0

    # Discord caps a message at 10 embeds.
    try:
        for index in range(0, len(embeds), 10):
            chunk = embeds[index:index + 10]
            post(webhook, chunk, content=header if index == 0 else None)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:400]
        print(f"Discord rejected the post: HTTP {err.code} {body}", file=sys.stderr)
        return 1

    seen.update(fresh)
    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
