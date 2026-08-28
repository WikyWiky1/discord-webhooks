#!/usr/bin/env python3

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

WANTED_PLATFORMS = ("steam", "gog", "epic")
PLATFORM_KEYWORDS = ("prime gaming", "amazon prime")

SEEN_FILE = "seen.json"
BRAND = "FreeGameAlert"

HEADER = "**Free games right now** 🎉"

MIN_PREFIX_MATCH = 12

GAMERTOOL = {
    "title": "GamerTool",
    "url": "https://github.com/WikyWiky1/GamerTool-releases/releases/latest",
    "image": "",
    "description": (
        "A free all-in-one desktop companion for PC gamers - system tools, "
        "game utilities, and a built-in casino (Blackjack, with more on the "
        "table). No ads, no accounts, just download and play."
    ),
    "store": "GamerTool",
    "ends_text": "free forever",
}


def get_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (FreeGameAlert/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def elements(feed):
    try:
        return feed["data"]["Catalog"]["searchStore"]["elements"] or []
    except (KeyError, TypeError):
        return []


def parse_dt(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_free_now(element, now):
    promos = (element.get("promotions") or {}).get("promotionalOffers") or []
    for block in promos:
        for offer in block.get("promotionalOffers") or []:
            start = parse_dt(offer.get("startDate"))
            end = parse_dt(offer.get("endDate"))
            pct = (offer.get("discountSetting") or {}).get("discountPercentage")
            if pct not in (0, None):
                continue
            if start and start > now:
                continue
            if end and end < now:
                continue
            return True, end
    return False, None


def was_already_free(element):
    try:
        original = element["price"]["totalPrice"]["originalPrice"]
        return original == 0
    except (KeyError, TypeError):
        return False


HASH_SLUG = re.compile(r"^[0-9a-f]{24,}$", re.I)


def slug_candidates(element):
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
            fallback = fallback or candidate
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


def make_offer(title, url, description, image, ends, store, via=None,
               ends_text=None):
    return {
        "title": (title or "Untitled").strip(),
        "url": url,
        "description": (description or "").strip(),
        "image": image,
        "ends": ends,
        "store": store,
        "via": via,
        "ends_text": ends_text,
    }


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
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def keys_match(first, second):
    if not first or not second:
        return False
    if first == second:
        return True
    short, long_ = (first, second) if len(first) <= len(second) \
        else (second, first)
    return len(short) >= MIN_PREFIX_MATCH and long_.startswith(short)


def known_keys(seen):
    return {str(entry).split("|")[0] for entry in seen}


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


def wanted_platform(entry):
    platforms = (entry.get("platforms") or "").lower()
    if any(tag in platforms for tag in WANTED_PLATFORMS):
        return True
    blob = f"{entry.get('title', '')} {entry.get('description', '')}".lower()
    return any(word in blob for word in PLATFORM_KEYWORDS)


def parse_gp_date(text):
    if not text or text.strip().upper() in ("N/A", "NA", ""):
        return None
    try:
        naive = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=timezone.utc)
    except ValueError:
        return parse_dt(text)


def gamerpower_offers():
    data = get_json(GAMERPOWER)

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


def gamertool_offer():
    return make_offer(
        title=GAMERTOOL["title"],
        url=GAMERTOOL["url"],
        description=GAMERTOOL["description"],
        image=GAMERTOOL["image"] or None,
        ends=None,
        store=GAMERTOOL["store"],
        ends_text=GAMERTOOL["ends_text"],
    )


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
    if offer.get("ends_text"):
        bits.append(offer["ends_text"])
    elif offer["ends"]:
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
        headers={"Content-Type": "application/json",
                 "User-Agent": "FreeGameAlert/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        print(f"posted ({res.status}) with {len(embeds)} embed(s)")


def load_seen():
    try:
        with open(SEEN_FILE) as fh:
            data = json.load(fh)
            return set(data) if isinstance(data, list) else set()
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as fh:
        json.dump(sorted(seen)[-400:], fh, indent=1)


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

    if "--gamer" in sys.argv:
        embed = build_embed(gamertool_offer())
        print(f"  PUSH: {GAMERTOOL['title']} [{GAMERTOOL['store']}] "
              f"-> {GAMERTOOL['url']}")

        if dry:
            print("\n--- dry run, not posting ---")
            print(json.dumps([embed], indent=2, default=str))
            return 0

        try:
            post(webhook, [embed], content=HEADER)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace")[:400]
            print(f"Discord rejected the post: HTTP {err.code} {body}",
                  file=sys.stderr)
            return 1
        return 0

    offers, failures = [], []
    for label, source in (("Epic", epic_offers),
                          ("GamerPower", gamerpower_offers)):
        try:
            offers.extend(source())
        except Exception as err:
            failures.append(label)
            print(f"  {label} failed: {err}", file=sys.stderr)

    if failures and len(failures) == 2:
        print("Both sources failed; nothing to do.", file=sys.stderr)
        return 1

    seen = set() if force else load_seen()
    known = known_keys(seen)
    fresh, embeds = [], []
    batch = set()

    for offer in offers:
        tkey = title_key(offer["title"])
        stamp = offer["ends"].date().isoformat() if offer["ends"] else "open"
        key = f"{tkey}|{stamp}"

        if any(keys_match(tkey, other) for other in batch):
            print(f"  duplicate in this run, skipping: {offer['title']}")
            batch.add(tkey)
            fresh.append(tkey)
            continue
        if key in seen or any(keys_match(tkey, other) for other in known):
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

    header = HEADER
    if any(e.get("footer", {}).get("text", "").endswith("via GamerPower.com")
           for e in embeds):
        header += f"\n-# Giveaway tracking via [GamerPower]({GAMERPOWER_SITE})"

    if dry:
        print("\n--- dry run, not posting ---")
        print(json.dumps(embeds, indent=2, default=str)[:2500])
        return 0

    try:
        for index in range(0, len(embeds), 10):
            chunk = embeds[index:index + 10]
            post(webhook, chunk, content=header if index == 0 else None)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:400]
        print(f"Discord rejected the post: HTTP {err.code} {body}",
              file=sys.stderr)
        return 1

    seen.update(fresh)
    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
