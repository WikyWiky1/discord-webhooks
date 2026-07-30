#!/usr/bin/env python3
"""
TOWN CRIER - WikyWiky Studios
Two lanes, one script.

  breaking  (default)  Sweeps wire feeds every ~20 minutes and posts anything
                       that is both recent and genuinely urgent. Hard capped so
                       it can never flood the channel.

  --morning            Posts exactly one interesting article to start the day.
                       One per run, never more.

*** THIS IS NOT AN ALERT SYSTEM. ***
StormWatch is the alert system. This is a news reader. Wire copy lags real
events, the scheduler is best-effort, and the breaking gate is keyword based,
so it will miss things and occasionally post something mundane. Do not treat
silence here as "nothing is happening."

Sources: Reuters and AP both shut down their public RSS feeds years ago, so
those two come through Google News' source-filtered feed, which is public and
needs no key. BBC, NPR and the Guardian still publish real feeds and are used
directly. Only headlines, summaries and links are posted - never full text.

Flags:
  --check     fetch every feed, report item counts and freshness, post nothing
  --dry       print what it would post, send nothing
  --test      send a single "online" message
  --morning   run the morning pick instead of the breaking sweep
"""

import email.utils
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Chicago")
except Exception:
    LOCAL_TZ = timezone.utc

BRAND = "Town Crier"
SEEN_FILE = "crier_seen.json"
USER_AGENT = "TownCrier/1.0 (github.com/WikyWiky1)"

ATOM = "{http://www.w3.org/2005/Atom}"

# ── tuning knobs ────────────────────────────────────────────────────────────
# These are the anti-spam controls. If the channel ever gets noisy, this block
# is the only thing you need to touch.

MAX_BREAKING_PER_RUN = 3        # hard ceiling per sweep, no exceptions
BREAKING_MAX_AGE_MINUTES = 120  # older than this is not breaking, it is news
MORNING_WINDOW_HOURS = 30       # how far back the morning pick may reach
SIMILARITY_THRESHOLD = 0.55     # 0-1; higher = more willing to post near-dupes
SEEN_LIMIT = 800                # article IDs remembered
TITLE_LIMIT = 300               # headline fingerprints remembered
POST_GAP_SECONDS = 1.5          # spacing between messages, Discord rate limit

# ── feeds ───────────────────────────────────────────────────────────────────
# weight  nudges the morning pick toward sources you like. Reuters is highest.
# lanes   which lane the feed feeds. Science/feature feeds are morning-only so
#         they can never trip the breaking sweep.
# suffix  Google News appends " - Publisher" to every headline; strip it.

FEEDS = [
    {
        "name": "Reuters",
        "url": "https://news.google.com/rss/search?q=site%3Areuters.com+when%3A1d"
               "&hl=en-US&gl=US&ceid=US%3Aen",
        "weight": 3.0,
        "color": 0xFF8000,
        "lanes": ("breaking", "morning"),
        "suffix": ("Reuters",),
    },
    {
        "name": "Reuters World",
        "url": "https://news.google.com/rss/search?"
               "q=site%3Areuters.com+world+when%3A1d"
               "&hl=en-US&gl=US&ceid=US%3Aen",
        "weight": 3.0,
        "color": 0xFF8000,
        "lanes": ("breaking", "morning"),
        "suffix": ("Reuters",),
    },
    {
        "name": "AP",
        "url": "https://news.google.com/rss/search?q=site%3Aapnews.com+when%3A1d"
               "&hl=en-US&gl=US&ceid=US%3Aen",
        "weight": 2.5,
        "color": 0xFF322E,
        "lanes": ("breaking", "morning"),
        "suffix": ("AP News", "The Associated Press", "Associated Press", "AP"),
    },
    {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "weight": 2.0,
        "color": 0xBB1919,
        "lanes": ("breaking", "morning"),
    },
    {
        "name": "NPR News",
        "url": "https://feeds.npr.org/1001/rss.xml",
        "weight": 2.0,
        "color": 0x4A90D9,
        "lanes": ("breaking", "morning"),
    },
    {
        "name": "Guardian World",
        "url": "https://www.theguardian.com/world/rss",
        "weight": 1.5,
        "color": 0x052962,
        "lanes": ("breaking", "morning"),
    },
    # Morning-only. These exist to give the daily pick something worth reading
    # that is not a disaster.
    {
        "name": "NPR Science",
        "url": "https://feeds.npr.org/1007/rss.xml",
        "weight": 2.0,
        "color": 0x4A90D9,
        "lanes": ("morning",),
    },
    {
        "name": "BBC Science",
        "url": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
        "weight": 2.0,
        "color": 0xBB1919,
        "lanes": ("morning",),
    },
]

# ── word lists ──────────────────────────────────────────────────────────────

# A headline must contain one of these to clear the breaking gate.
BREAKING_WORDS = (
    "breaking", "urgent", "killed", "dead", "death toll", "explosion",
    "earthquake", "tsunami", "evacuat", "shooting", "gunman", "attack",
    "airstrike", "invasion", "collapse", "state of emergency", "emergency",
    "hostage", "wildfire", "hurricane", "tornado", "derail", "plane crash",
    "mass casualt", "nuclear", "outbreak", "resigns", "steps down", "indicted",
    "impeach", "assassinat", "manhunt", "lockdown", "recall", "shutdown",
    "ceasefire", "coup",
)

# Never posted in either lane. Edit freely - sports lives in another channel.
MUTE_WORDS = (
    "nfl", "nba", "mlb", "nhl", "premier league", "world cup", "olympics",
    "box office", "red carpet", "horoscope", "recap", "highlights", "odds",
    "fantasy football", "celebrity", "kardashian", "royal family",
    "what to watch", "best deals", "prime day", "black friday",
)

# Nudges the morning pick toward things worth thinking about over coffee.
INTEREST_WORDS = (
    "scientist", "researchers", "study finds", "discover", "ancient", "fossil",
    "space", "nasa", "telescope", "asteroid", "orbit", "quantum", "physics",
    "ocean", "deep sea", "archaeolog", "unearth", "breakthrough", "experiment",
    "brain", "species", "first time", "record", "mystery", "rare", "why",
    "how", "century", "manuscript", "engineer", "algorithm", "supply chain",
)

STOPWORDS = {
    "after", "against", "amid", "among", "before", "being", "could", "during",
    "first", "from", "have", "into", "more", "most", "over", "said", "says",
    "some", "than", "that", "their", "them", "there", "these", "they", "this",
    "were", "what", "when", "which", "while", "will", "with", "would", "your",
}


# ── http ────────────────────────────────────────────────────────────────────

def get_bytes(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


# ── text helpers ────────────────────────────────────────────────────────────

def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split())


def clip(text, limit):
    text = " ".join((text or "").split())
    return text[:limit - 3].rstrip() + "..." if len(text) > limit else text


def strip_suffix(title, suffixes):
    for suffix in suffixes or ():
        tail = f" - {suffix}"
        if title.endswith(tail):
            return title[:-len(tail)].rstrip()
    return title


def parse_date(text):
    """RSS uses RFC 822, Atom uses ISO 8601. Accept either, always return
    something timezone aware so age math never blows up."""
    if not text:
        return None
    text = text.strip()
    stamp = None
    try:
        stamp = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        stamp = None
    if stamp is None:
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def fmt_time(stamp):
    if not stamp:
        return None
    return f"{stamp.astimezone(LOCAL_TZ):%-I:%M %p %Z}"


def fingerprint(title):
    """Reduce a headline to its distinctive words so the same story from three
    wires does not post three times."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    keep = sorted({w for w in words if len(w) > 3 and w not in STOPWORDS})
    return " ".join(keep)


def too_similar(fp, others):
    mine = set(fp.split())
    if not mine:
        return False
    for other in others:
        theirs = set(other.split())
        if not theirs:
            continue
        overlap = len(mine & theirs) / len(mine | theirs)
        if overlap >= SIMILARITY_THRESHOLD:
            return True
    return False


# ── feed parsing ────────────────────────────────────────────────────────────

def parse_feed(raw, feed):
    root = ET.fromstring(raw)
    nodes = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    items = []

    for node in nodes:
        if node.tag.endswith("entry"):
            title = node.findtext(f"{ATOM}title") or ""
            link_el = node.find(f"{ATOM}link[@rel='alternate']")
            if link_el is None:
                link_el = node.find(f"{ATOM}link")
            link = link_el.get("href") if link_el is not None else ""
            summary = (node.findtext(f"{ATOM}summary")
                       or node.findtext(f"{ATOM}content") or "")
            published = (node.findtext(f"{ATOM}published")
                         or node.findtext(f"{ATOM}updated"))
            ident = node.findtext(f"{ATOM}id") or link
        else:
            title = node.findtext("title") or ""
            link = node.findtext("link") or ""
            summary = node.findtext("description") or ""
            published = node.findtext("pubDate")
            ident = node.findtext("guid") or link

        title = strip_suffix(strip_html(title), feed.get("suffix"))
        summary = strip_html(summary)

        # Google News fills description with a repeat of the headline. If the
        # summary adds nothing, drop it rather than print the title twice.
        if summary and fingerprint(summary)[:60] == fingerprint(title)[:60]:
            summary = ""

        if not title or not link:
            continue

        items.append({
            "id": (ident or link).strip(),
            "title": title,
            "summary": summary,
            "link": link.strip(),
            "published": parse_date(published),
            "source": feed["name"],
            "weight": feed["weight"],
            "color": feed["color"],
        })

    return items


def collect(lane):
    items = []
    for feed in FEEDS:
        if lane not in feed["lanes"]:
            continue
        try:
            raw = get_bytes(feed["url"])
        except Exception as err:
            print(f"  {feed['name']}: fetch failed ({err})")
            continue
        try:
            found = parse_feed(raw, feed)
        except ET.ParseError as err:
            print(f"  {feed['name']}: unreadable XML ({err})")
            continue
        print(f"  {feed['name']}: {len(found)} item(s)")
        items.extend(found)
    return items


# ── selection ───────────────────────────────────────────────────────────────

def age_minutes(item, now):
    if not item["published"]:
        return None
    return (now - item["published"]).total_seconds() / 60.0


def muted(item):
    text = f"{item['title']} {item['summary']}".lower()
    return any(word in text for word in MUTE_WORDS)


def looks_breaking(item):
    return any(word in item["title"].lower() for word in BREAKING_WORDS)


def score_interest(item):
    text = f"{item['title']} {item['summary']}".lower()
    score = item["weight"]
    score += sum(1.5 for word in INTEREST_WORDS if word in text)
    if len(item["summary"]) > 120:
        score += 0.5
    return score


def pick_breaking(items, state):
    """Newest first, urgent only, deduped against everything already posted,
    and hard capped. The cap is what keeps an outbreak of bad news from
    becoming an outbreak of notifications."""
    now = datetime.now(timezone.utc)
    fingerprints = list(state["titles"])
    picks = []

    ordered = sorted(
        items,
        key=lambda i: i["published"] or (now - timedelta(days=365)),
        reverse=True,
    )

    for item in ordered:
        if item["id"] in state["ids"]:
            continue
        if muted(item):
            continue
        age = age_minutes(item, now)
        if age is None or age > BREAKING_MAX_AGE_MINUTES:
            continue
        if not looks_breaking(item):
            continue

        fp = fingerprint(item["title"])
        if too_similar(fp, fingerprints):
            print(f"  duplicate story, skipping: {clip(item['title'], 60)}")
            continue

        fingerprints.append(fp)
        item["fingerprint"] = fp
        picks.append(item)
        print(f"  BREAKING: [{item['source']}] {clip(item['title'], 70)}")

        if len(picks) >= MAX_BREAKING_PER_RUN:
            print(f"  hit the per-run cap of {MAX_BREAKING_PER_RUN}")
            break

    return picks


def pick_morning(items, state):
    """Exactly one article. Scored for interest, with a small daily random
    nudge so it does not pick the same kind of story every morning."""
    now = datetime.now(timezone.utc)
    rng = random.Random(now.astimezone(LOCAL_TZ).strftime("%Y-%m-%d"))
    scored = []

    for item in items:
        if item["id"] in state["ids"]:
            continue
        if muted(item):
            continue
        age = age_minutes(item, now)
        if age is not None and age > MORNING_WINDOW_HOURS * 60:
            continue
        fp = fingerprint(item["title"])
        if too_similar(fp, state["titles"]):
            continue
        item["fingerprint"] = fp
        scored.append((score_interest(item) + rng.uniform(0, 1.5), item))

    if not scored:
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    for value, item in scored[:5]:
        print(f"  candidate {value:.1f}: [{item['source']}] "
              f"{clip(item['title'], 60)}")

    return [scored[0][1]]


# ── embeds ──────────────────────────────────────────────────────────────────

def build_embed(item):
    embed = {
        "title": clip(item["title"], 240),
        "url": item["link"],
        "color": item["color"],
        "footer": {"text": item["source"]},
        "fields": [],
    }

    summary = clip(item["summary"], 600)
    if summary:
        embed["description"] = summary

    when = fmt_time(item["published"])
    if when:
        embed["fields"].append({"name": "Filed", "value": when, "inline": True})

    if not embed["fields"]:
        del embed["fields"]
    return embed


# ── posting ─────────────────────────────────────────────────────────────────

def retry_after_seconds(err):
    """Discord has reported retry_after in both seconds and milliseconds over
    the years, so treat anything implausibly large as milliseconds."""
    wait = 2.0
    try:
        info = json.loads(err.read().decode("utf-8", "replace"))
        wait = float(info.get("retry_after", wait))
        if wait > 100:
            wait /= 1000.0
    except Exception:
        pass
    return min(max(wait, 0.5), 30.0)


def post(url, embeds, content=None):
    payload = {"username": BRAND, "allowed_mentions": {"parse": []}}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )

    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                print(f"  posted ({res.status})")
                return
        except urllib.error.HTTPError as err:
            if err.code != 429 or attempt == 3:
                raise
            wait = retry_after_seconds(err)
            print(f"  rate limited, waiting {wait:.1f}s")
            time.sleep(wait)


# ── state ───────────────────────────────────────────────────────────────────

def load_state():
    """Oldest first, newest last. Order matters: the trim in save_state drops
    from the front, so it has to be chronological."""
    try:
        with open(SEEN_FILE) as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    ids = [str(x) for x in data.get("ids", []) if isinstance(x, (str, int))]
    titles = [str(x) for x in data.get("titles", []) if isinstance(x, str)]
    return {
        "ids": list(dict.fromkeys(ids)),
        "titles": list(dict.fromkeys(titles)),
    }


def save_state(state):
    with open(SEEN_FILE, "w") as fh:
        json.dump({
            "ids": state["ids"][-SEEN_LIMIT:],
            "titles": state["titles"][-TITLE_LIMIT:],
        }, fh, indent=1)


# ── checks ──────────────────────────────────────────────────────────────────

def check_feeds():
    """Feeds rot. Publishers move URLs, Google changes query handling, and a
    dead feed fails silently forever otherwise."""
    print("Checking feeds...\n")
    now = datetime.now(timezone.utc)
    bad = []

    for feed in FEEDS:
        label = f"{feed['name']} [{'/'.join(feed['lanes'])}]"
        try:
            raw = get_bytes(feed["url"])
            items = parse_feed(raw, feed)
        except Exception as err:
            print(f"  BAD   {label}\n        {err}")
            bad.append(feed["name"])
            continue

        if not items:
            print(f"  EMPTY {label}  ->  parsed 0 items")
            bad.append(feed["name"])
            continue

        stamps = [i["published"] for i in items if i["published"]]
        if stamps:
            newest = (now - max(stamps)).total_seconds() / 60.0
            fresh = f"newest {newest:.0f} min old"
        else:
            fresh = "no timestamps"
        print(f"  OK    {label}  ->  {len(items)} items, {fresh}")
        print(f"        {clip(items[0]['title'], 70)}")

    print()
    if bad:
        print(f"Feeds needing attention: {', '.join(bad)}")
        return 1
    print("All feeds healthy.")
    return 0


# ── main ────────────────────────────────────────────────────────────────────

def main():
    if "--check" in sys.argv:
        return check_feeds()

    dry = "--dry" in sys.argv
    lane = "morning" if "--morning" in sys.argv else "breaking"

    # Its own secret so news can go to a different channel than StormWatch.
    # Falls back to the StormWatch webhook if you'd rather share one channel.
    webhook = (os.environ.get("CRIER_WEBHOOK", "").strip()
               or os.environ.get("DISCORD_WEBHOOK", "").strip())

    if not webhook and not dry:
        print("CRIER_WEBHOOK is not set.", file=sys.stderr)
        return 1

    if "--test" in sys.argv:
        post(webhook, [{
            "title": "Town Crier is online",
            "description": f"Watching {len(FEEDS)} feeds. Breaking sweeps run "
                           "through the day; one pick lands each morning.",
            "color": 0x3BA55D,
        }])
        return 0

    print(f"Lane: {lane}")
    items = collect(lane)
    print(f"{len(items)} item(s) collected.")

    state = load_state()
    picks = pick_morning(items, state) if lane == "morning" \
        else pick_breaking(items, state)

    if not picks:
        print("Nothing worth posting.")
        return 0

    if dry:
        print("\n--- dry run, not posting ---")
        print(json.dumps([build_embed(i) for i in picks], indent=2))
        return 0

    header = "Morning read" if lane == "morning" else "Breaking"

    # State is saved after every successful send. If send 2 of 3 dies, the
    # first one stays recorded and does not repost on the next sweep.
    failed = 0
    try:
        for index, item in enumerate(picks):
            if index:
                time.sleep(POST_GAP_SECONDS)
            post(webhook, [build_embed(item)],
                 content=f"**{header}** — {item['source']}")
            state["ids"].append(item["id"])
            state["titles"].append(item.get("fingerprint")
                                   or fingerprint(item["title"]))
            save_state(state)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:400]
        print(f"Discord rejected the post: HTTP {err.code} {body}",
              file=sys.stderr)
        failed = 1

    return failed


if __name__ == "__main__":
    sys.exit(main())
