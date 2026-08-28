#!/usr/bin/env python3

import email.utils
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
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
USER_AGENT = "TownCrier/1.0"

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"

JUNK_IMAGE_HINTS = ("1x1", "pixel", "spacer", "favicon", "/logo", "blank.")

MAX_BREAKING_PER_RUN = 17
MAX_PER_OUTLET_PER_RUN = 4
BREAKING_MAX_AGE_MINUTES = 120
BREAKING_FRESHNESS_BUCKET_MINUTES = 30
MORNING_WINDOW_HOURS = 30
SIMILARITY_THRESHOLD = 0.55
CONTAINMENT_THRESHOLD = 0.75
CONTAINMENT_MIN_WORDS = 3
SEEN_LIMIT = 800
POST_MEMORY_LIMIT = 400
POST_GAP_SECONDS = 1.5

DEDUPE_WINDOW_HOURS = 48

PULSE_MAX_PER_RUN = 6
PULSE_MAX_PER_OUTLET_PER_RUN = 2
PULSE_MAX_AGE_MINUTES = 240

RELATED_NOTE_OVERLAP = 0.30

STALE_FEED_HOURS = 36

MORNING_IMAGE = "image"
BREAKING_IMAGE = "thumbnail"
PULSE_IMAGE = "thumbnail"

BORROW_IMAGES = True
FETCH_MISSING_IMAGES = True
OG_TIMEOUT_SECONDS = 8
OG_READ_BYTES = 200_000
OG_USER_AGENT = "Mozilla/5.0 (compatible; TownCrier/1.0)"

MIRROR_SIMILARITY = 0.55
MIRROR_MAX_AGE_GAP_HOURS = 12

GOOGLE_NEWS = ("https://news.google.com/rss/search?q=site%3A{site}+when%3A1d"
               "&hl=en-US&gl=US&ceid=US%3Aen")

FEEDS = [
    {
        "name": "Reuters",
        "url": GOOGLE_NEWS.format(site="reuters.com"),
        "weight": 3.0,
        "color": 0xFF8000,
        "lanes": ("breaking", "pulse", "morning"),
        "suffix": ("Reuters", "reuters.com"),
        "paywall": True,
    },
    {
        "name": "Reuters World",
        "outlet": "Reuters",
        "url": "https://news.google.com/rss/search?"
               "q=site%3Areuters.com+world+when%3A1d"
               "&hl=en-US&gl=US&ceid=US%3Aen",
        "weight": 3.0,
        "color": 0xFF8000,
        "lanes": ("breaking", "pulse", "morning"),
        "suffix": ("Reuters", "reuters.com"),
        "paywall": True,
    },
    {
        "name": "AP",
        "url": GOOGLE_NEWS.format(site="apnews.com"),
        "weight": 2.5,
        "color": 0xFF322E,
        "lanes": ("breaking", "pulse", "morning"),
        "suffix": ("AP News", "The Associated Press", "Associated Press",
                   "apnews.com", "AP"),
    },
    {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "weight": 2.0,
        "color": 0xBB1919,
        "lanes": ("breaking", "pulse", "morning"),
    },
    {
        "name": "NPR News",
        "url": "https://feeds.npr.org/1001/rss.xml",
        "weight": 2.0,
        "color": 0x4A90D9,
        "lanes": ("breaking", "pulse", "morning"),
    },
    {
        "name": "Guardian World",
        "url": "https://www.theguardian.com/world/rss",
        "weight": 1.5,
        "color": 0x052962,
        "lanes": ("breaking", "pulse", "morning"),
    },
    {
        "name": "WSJ",
        "url": GOOGLE_NEWS.format(site="wsj.com"),
        "weight": 3.0,
        "color": 0x0274B6,
        "lanes": ("breaking", "pulse", "morning"),
        "suffix": ("The Wall Street Journal", "Wall Street Journal",
                   "wsj.com", "WSJ"),
        "paywall": True,
    },
    {
        "name": "Telegraph",
        "url": GOOGLE_NEWS.format(site="telegraph.co.uk"),
        "weight": 2.6,
        "color": 0x122B49,
        "lanes": ("breaking", "pulse", "morning"),
        "suffix": ("The Telegraph", "Telegraph.co.uk", "telegraph.co.uk"),
        "paywall": True,
    },
    {
        "name": "Fox News World",
        "url": "https://feeds.foxnews.com/foxnews/world",
        "weight": 2.5,
        "color": 0x003366,
        "lanes": ("breaking", "pulse", "morning"),
    },
    {
        "name": "New York Post",
        "url": "https://nypost.com/feed/",
        "weight": 2.0,
        "color": 0xC60800,
        "lanes": ("breaking", "pulse", "morning"),
    },
    {
        "name": "Washington Examiner",
        "url": "https://www.washingtonexaminer.com/feed",
        "weight": 2.0,
        "color": 0x0A2240,
        "lanes": ("breaking", "pulse", "morning"),
    },
    {
        "name": "Sky News",
        "url": "https://feeds.skynews.com/feeds/rss/home.xml",
        "weight": 2.2,
        "color": 0xC70000,
        "lanes": ("breaking", "pulse", "morning"),
    },
    {
        "name": "The Hill",
        "url": "https://thehill.com/news/feed/",
        "weight": 2.0,
        "color": 0x2E4B33,
        "lanes": ("breaking", "pulse", "morning"),
    },
    {
        "name": "CS Monitor World",
        "url": "https://rss.csmonitor.com/feeds/world",
        "weight": 2.0,
        "color": 0x9E1B32,
        "lanes": ("morning",),
        "paywall": True,
    },
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
    {
        "name": "National Review",
        "url": "https://www.nationalreview.com/feed/",
        "weight": 2.0,
        "color": 0x1B365D,
        "lanes": ("morning",),
        "paywall": True,
    },
    {
        "name": "Reason",
        "url": "https://reason.com/feed/",
        "weight": 1.8,
        "color": 0xE31B23,
        "lanes": ("morning",),
    },
]

BREAKING_WORDS = (
    "breaking", "urgent", "killed", "kills", "kill", "dead", "deadly",
    "explosion", "earthquake", "quake", "tsunami", "shooting", "gunman",
    "attack", "airstrike", "invasion", "hostage", "wildfire", "wildfires",
    "hurricane", "tornado", "nuclear", "outbreak", "indicted", "manhunt",
    "lockdown", "recall", "shutdown", "ceasefire", "coup", "emergency",
)

BREAKING_PHRASES = (
    "death toll", "plane crash", "state of emergency", "steps down",
    "mass casualties", "opens fire", "shot dead",
)

BREAKING_STEMS = (
    "evacuat", "assassinat", "derail", "impeach", "collaps", "resign",
    "casualt",
)

NOT_BREAKING = (
    "heart attack", "panic attack", "attack ad", "attacks on democracy",
    "dead heat", "dead last", "dead end", "walking dead", "dead of night",
    "nuclear option", "nuclear family", "emergency room", "emergency fund",
    "emergency savings", "recalls how", "recalls the", "recalls his",
    "recalls her", "shutdown of the", "under attack from critics",
    "kills bill", "kills the bill", "kills deal", "kills the deal",
    "kills plan", "kills proposal", "kills measure", "kills amendment",
)

PROCESS_WORDS = (
    "inquest", "autopsy", "post-mortem", "coroner", "toxicology",
    "testifies", "testified", "testimony", "takes the stand",
    "court hears", "jury hears", "hearing told", "trial hears",
    "goes on trial", "trial begins", "trial opens", "on trial for",
    "jury selection", "opening statements", "closing arguments",
    "jury deliberat", "pleads not guilty", "arraigned", "arraignment",
    "subpoena", "deposition", "lawsuit", "files suit", "sues over",
    "anniversary of", "years after", "years since", "decade after",
    "lawmakers investigating", "democrats investigating",
    "republicans investigating", "committee investigating",
    "congress investigating", "senate investigating",
    "house investigating", "senators investigating",
    "opens investigation", "launches investigation",
    "calls for investigation", "demand answers", "demands answers",
)

BREAKING_OUTCOMES = (
    "sentenced", "sentencing", "convicted", "conviction", "verdict",
    "acquitted", "cleared of", "jailed", "found guilty", "pleads guilty",
    "no charges", "charges dropped", "charged", "indicted", "arrested",
    "guilty of", "ruling", "ruled", "settles",
)

MUTE_WORDS = (
    "nfl", "nba", "mlb", "nhl", "premier league", "world cup", "olympics",
    "box office", "red carpet", "horoscope", "recap", "highlights", "odds",
    "fantasy football", "celebrity", "kardashian", "royal family",
    "what to watch", "best deals", "prime day", "black friday",
)

MUTE_URL_PARTS = (
    "/video/", "/videos/", "/gallery/", "/galleries/", "/photos/",
    "/podcast/", "/podcasts/", "/slideshow",
)

PULSE_WORDS = (
    "congress", "senate", "legislation", "budget", "election", "elects",
    "primary", "runoff", "nominates", "nomination", "treaty", "sanctions",
    "summit", "stocks", "nasdaq", "inflation", "unemployment", "earnings",
    "tariff", "unveils", "discovery", "discovers", "mission", "spacex",
    "rover", "vaccine", "nobel", "ceasefire",
)

PULSE_PHRASES = (
    "house passes", "senate passes", "passes bill", "signs bill",
    "signs into law", "supreme court rules", "court rules", "trade deal",
    "peace deal", "agreement reached", "talks resume", "stock market",
    "wall street", "interest rate", "federal reserve", "jobs report",
    "clinical trial", "sworn in", "wins election", "wins nobel",
    "breaks ground", "spending bill", "trade war", "vaccine approved",
)

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


def get_bytes(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


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
    for suffix in sorted(suffixes or (), key=len, reverse=True):
        tail = f" - {suffix}"
        if title.lower().endswith(tail.lower()):
            return title[:-len(tail)].rstrip()
    return title


def parse_date(text):
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
    return f"{stamp.astimezone(LOCAL_TZ):%I:%M %p %Z}".lstrip("0")


def fingerprint(title):
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    keep = sorted({w for w in words if len(w) > 3 and w not in STOPWORDS})
    return " ".join(keep)


def title_echo(summary, title):
    words = set(fingerprint(summary).split())
    head = set(fingerprint(title).split())
    if not words or not head:
        return False
    return len(words & head) / len(head) >= 0.8


def overlap(fp_a, fp_b):
    a, b = set(fp_a.split()), set(fp_b.split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(fp_a, fp_b):
    a, b = set(fp_a.split()), set(fp_b.split())
    if len(a) < CONTAINMENT_MIN_WORDS or len(b) < CONTAINMENT_MIN_WORDS:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def too_similar(fp, others):
    for other in others:
        if overlap(fp, other) >= SIMILARITY_THRESHOLD:
            return True
        if containment(fp, other) >= CONTAINMENT_THRESHOLD:
            return True
    return False


def usable_image(url):
    if not url:
        return None
    url = html.unescape(url.strip())

    path, sep, query = url.partition("?")
    if "%2f" in path.lower():
        path = urllib.parse.unquote(path)
        query = urllib.parse.unquote(query)
        url = path + sep + query if sep else path

    if not url.lower().startswith("http"):
        return None
    if any(hint in url.lower() for hint in JUNK_IMAGE_HINTS):
        return None
    return url


def find_image(node):
    best, best_width = None, -1

    for tag in (f"{MEDIA}content", f"{MEDIA}thumbnail"):
        candidates = node.findall(tag) + node.findall(f"{MEDIA}group/{tag}")
        for el in candidates:
            url = usable_image(el.get("url"))
            if not url:
                continue
            medium = (el.get("medium") or "").lower()
            mime = (el.get("type") or "").lower()
            if medium and medium != "image":
                continue
            if mime and not mime.startswith("image/"):
                continue
            try:
                width = int(el.get("width") or 0)
            except ValueError:
                width = 0
            if width > best_width:
                best, best_width = url, width

    if not best:
        for el in node.findall("enclosure"):
            if (el.get("type") or "").lower().startswith("image/"):
                best = usable_image(el.get("url"))
                if best:
                    break

    if not best:
        blob = node.findtext(f"{CONTENT}encoded") or node.findtext("description")
        for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']',
                                 blob or "", re.I):
            best = usable_image(match.group(1))
            if best:
                break

    return best


def fetch_og_image(url):
    if "news.google.com" in url.lower():
        return None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": OG_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=OG_TIMEOUT_SECONDS) as res:
            if "html" not in (res.headers.get("Content-Type") or "").lower():
                return None
            head = res.read(OG_READ_BYTES).decode("utf-8", "replace")
    except Exception:
        return None

    patterns = (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, head, re.I)
        if match:
            found = usable_image(html.unescape(match.group(1)).strip())
            if found:
                return found
    return None


def backfill_images(picks):
    for item in picks:
        if item.get("image"):
            continue
        found = fetch_og_image(item["link"])
        if found:
            item["image"] = found
            print(f"  pulled og:image for [{item['source']}]")


def same_story(item, pool, accept):
    mine = item.get("fingerprint") or fingerprint(item["title"])
    found = []

    for other in pool:
        if other["id"] == item["id"] or other["source"] == item["source"]:
            continue
        if not accept(other):
            continue

        score = overlap(mine, fingerprint(other["title"]))
        if score < MIRROR_SIMILARITY:
            continue

        if item["published"] and other["published"]:
            gap = abs((item["published"] - other["published"]).total_seconds())
            if gap > MIRROR_MAX_AGE_GAP_HOURS * 3600:
                continue

        found.append((score, other["weight"], other))

    found.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return found


def attach_free_mirrors(picks, pool):
    for item in picks:
        if not item.get("paywall"):
            continue
        found = same_story(item, pool, lambda o: not o.get("paywall"))
        if not found:
            continue
        score, _, mirror = found[0]
        item["mirror"] = mirror
        print(f"  free mirror for [{item['source']}]: "
              f"{mirror['source']} ({score:.2f} match)")


def borrow_images(picks, pool):
    for item in picks:
        if item.get("image"):
            continue

        mirror = item.get("mirror")
        if mirror and mirror.get("image"):
            item["image"] = mirror["image"]
            item["photo_credit"] = mirror["source"]
            print(f"  borrowed photo for [{item['source']}] "
                  f"from {mirror['source']}")
            continue

        found = same_story(item, pool, lambda o: bool(o.get("image")))
        if not found:
            continue
        score, _, donor = found[0]
        item["image"] = donor["image"]
        item["photo_credit"] = donor["source"]
        print(f"  borrowed photo for [{item['source']}] "
              f"from {donor['source']} ({score:.2f} match)")


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

        if summary and title_echo(summary, title):
            summary = ""

        if not title or not link:
            continue

        items.append({
            "id": (ident or link).strip(),
            "title": title,
            "summary": summary,
            "link": link.strip(),
            "image": find_image(node),
            "published": parse_date(published),
            "source": feed["name"],
            "outlet": feed.get("outlet") or feed["name"],
            "weight": feed["weight"],
            "color": feed["color"],
            "paywall": bool(feed.get("paywall")),
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


def age_minutes(item, now):
    if not item["published"]:
        return None
    return max((now - item["published"]).total_seconds() / 60.0, 0.0)


def muted(item):
    text = f"{item['title']} {item['summary']}".lower()
    if any(word in text for word in MUTE_WORDS):
        return True
    link = item["link"].lower()
    return any(part in link for part in MUTE_URL_PARTS)


def _word_pattern(words=(), phrases=(), stems=()):
    exact = [re.escape(w) for w in tuple(words) + tuple(phrases)]
    stem_esc = [re.escape(s) for s in stems]
    parts = []
    if exact:
        parts.append(r"\b(?:" + "|".join(exact) + r")\b")
    if stem_esc:
        parts.append(r"\b(?:" + "|".join(stem_esc) + r")")
    return re.compile("|".join(parts), re.I)


BREAKING_RE = _word_pattern(BREAKING_WORDS, BREAKING_PHRASES, BREAKING_STEMS)
PULSE_RE = _word_pattern(PULSE_WORDS, PULSE_PHRASES)


def looks_breaking(item):
    title = item["title"].lower()
    if any(phrase in title for phrase in NOT_BREAKING):
        return False
    if any(phrase in title for phrase in PROCESS_WORDS) \
            and not any(word in title for word in BREAKING_OUTCOMES):
        return False
    return bool(BREAKING_RE.search(title))


def looks_pulse(item):
    if looks_breaking(item):
        return False
    return bool(PULSE_RE.search(item["title"].lower()))


def score_interest(item):
    text = f"{item['title']} {item['summary']}".lower()
    score = item["weight"]
    score += sum(1.5 for word in INTEREST_WORDS if word in text)
    if len(item["summary"]) > 120:
        score += 0.5
    return score


def breaking_rank(item, now):
    age = age_minutes(item, now)
    if age is None:
        age = BREAKING_MAX_AGE_MINUTES
    bucket = int(age // BREAKING_FRESHNESS_BUCKET_MINUTES)
    return (bucket, -item["weight"], age)


def note_related(fp, state, now):
    best = None
    for prior in recent_posts(state, DEDUPE_WINDOW_HOURS, now):
        if not prior["title"]:
            continue
        score = overlap(fp, prior["fp"])
        if score >= RELATED_NOTE_OVERLAP and (best is None or score > best[0]):
            best = (score, prior)
    if best:
        print(f"    (near {best[0]:.2f}: [{best[1]['source']}] "
              f"{clip(best[1]['title'], 55)})")


def pick_breaking(items, state):
    now = datetime.now(timezone.utc)
    fingerprints = [p["fp"] for p in recent_posts(state, DEDUPE_WINDOW_HOURS,
                                                  now)]
    picks = []
    per_outlet = {}

    ordered = sorted(items, key=lambda i: breaking_rank(i, now))

    for item in ordered:
        if item["id"] in state["ids"]:
            continue
        if muted(item):
            continue
        if per_outlet.get(item["outlet"], 0) >= MAX_PER_OUTLET_PER_RUN:
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
        per_outlet[item["outlet"]] = per_outlet.get(item["outlet"], 0) + 1
        picks.append(item)
        print(f"  BREAKING: [{item['source']}] {clip(item['title'], 70)}")
        note_related(fp, state, now)

        if len(picks) >= MAX_BREAKING_PER_RUN:
            print(f"  hit the per-run cap of {MAX_BREAKING_PER_RUN}")
            break

    if picks:
        spread = ", ".join(f"{name} {count}" for name, count
                           in sorted(per_outlet.items()))
        print(f"  {len(picks)} to post ({spread})")
    return picks


def pick_pulse(items, state):
    now = datetime.now(timezone.utc)
    fingerprints = [p["fp"] for p in recent_posts(state, DEDUPE_WINDOW_HOURS,
                                                  now)]
    picks = []
    per_outlet = {}

    ordered = sorted(items, key=lambda i: breaking_rank(i, now))

    for item in ordered:
        if item["id"] in state["ids"]:
            continue
        if muted(item):
            continue
        if per_outlet.get(item["outlet"], 0) >= PULSE_MAX_PER_OUTLET_PER_RUN:
            continue
        age = age_minutes(item, now)
        if age is None or age > PULSE_MAX_AGE_MINUTES:
            continue
        if not looks_pulse(item):
            continue

        fp = fingerprint(item["title"])
        if too_similar(fp, fingerprints):
            print(f"  duplicate story, skipping: {clip(item['title'], 60)}")
            continue

        fingerprints.append(fp)
        item["fingerprint"] = fp
        per_outlet[item["outlet"]] = per_outlet.get(item["outlet"], 0) + 1
        picks.append(item)
        print(f"  PULSE: [{item['source']}] {clip(item['title'], 70)}")
        note_related(fp, state, now)

        if len(picks) >= PULSE_MAX_PER_RUN:
            print(f"  hit the per-run cap of {PULSE_MAX_PER_RUN}")
            break

    if picks:
        spread = ", ".join(f"{name} {count}" for name, count
                           in sorted(per_outlet.items()))
        print(f"  {len(picks)} to post ({spread})")
    return picks


def pick_morning(items, state):
    now = datetime.now(timezone.utc)
    rng = random.Random(now.astimezone(LOCAL_TZ).strftime("%Y-%m-%d"))
    fingerprints = [p["fp"] for p in recent_posts(state, DEDUPE_WINDOW_HOURS,
                                                  now)]
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
        if too_similar(fp, fingerprints):
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


def build_embed(item, lane="breaking"):
    credit = item.get("photo_credit")
    footer = item["source"]
    if credit:
        footer = f"{item['source']}  ·  photo: {credit}"

    embed = {
        "title": clip(item["title"], 240),
        "url": item["link"],
        "color": item["color"],
        "footer": {"text": footer},
        "fields": [],
    }

    summary = clip(item["summary"], 600)
    if summary:
        embed["description"] = summary

    when = fmt_time(item["published"])
    if when:
        embed["fields"].append({"name": "Filed", "value": when, "inline": True})

    mirror = item.get("mirror")
    if mirror:
        embed["fields"].append({
            "name": "Free version",
            "value": f"[{mirror['source']}]({mirror['link']})",
            "inline": True,
        })

    style = {"morning": MORNING_IMAGE, "pulse": PULSE_IMAGE}.get(
        lane, BREAKING_IMAGE)
    if style in ("image", "thumbnail") and item.get("image"):
        embed[style] = {"url": item["image"]}

    if not embed["fields"]:
        del embed["fields"]
    return embed


def retry_after_seconds(err):
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


def load_state():
    try:
        with open(SEEN_FILE) as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    ids = [str(x) for x in data.get("ids", []) if isinstance(x, (str, int))]

    posts, seen = [], set()
    for entry in data.get("posts", []):
        if not isinstance(entry, dict):
            continue
        fp = str(entry.get("fp") or "")
        if not fp or fp in seen:
            continue
        seen.add(fp)
        posts.append({
            "fp": fp,
            "title": str(entry.get("title") or ""),
            "source": str(entry.get("source") or ""),
            "at": parse_date(entry.get("at")),
        })

    if not posts:
        now = datetime.now(timezone.utc)
        for fp in data.get("titles", []):
            if isinstance(fp, str) and fp and fp not in seen:
                seen.add(fp)
                posts.append({"fp": fp, "title": "", "source": "",
                              "at": now})

    return {"ids": list(dict.fromkeys(ids)), "posts": posts}


def save_state(state):
    posts = state["posts"][-POST_MEMORY_LIMIT:]
    with open(SEEN_FILE, "w") as fh:
        json.dump({
            "ids": list(dict.fromkeys(state["ids"]))[-SEEN_LIMIT:],
            "posts": [{
                "fp": p["fp"],
                "title": p["title"],
                "source": p["source"],
                "at": p["at"].isoformat() if p["at"] else None,
            } for p in posts],
            "titles": [p["fp"] for p in posts],
        }, fh, indent=1)


def recent_posts(state, hours, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    return [p for p in state["posts"] if p["at"] is None or p["at"] >= cutoff]


def record_post(state, item, now):
    state["ids"].append(item["id"])
    if item.get("mirror"):
        state["ids"].append(item["mirror"]["id"])
    state["posts"].append({
        "fp": item.get("fingerprint") or fingerprint(item["title"]),
        "title": item["title"],
        "source": item["source"],
        "at": now,
    })


def check_feeds():
    print("Checking feeds...\n")
    now = datetime.now(timezone.utc)
    bad = []

    for feed in FEEDS:
        label = f"{feed['name']} [{'/'.join(feed['lanes'])}] w{feed['weight']}"
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
        pics = sum(1 for i in items if i["image"])

        if stamps:
            newest = max((now - max(stamps)).total_seconds() / 60.0, 0.0)
            if newest > STALE_FEED_HOURS * 60:
                print(f"  STALE {label}  ->  {len(items)} items, but the "
                      f"newest is {newest / 1440:.1f} days old")
                bad.append(feed["name"])
                continue
            fresh = f"newest {newest:.0f} min old"
        else:
            fresh = "no timestamps"

        print(f"  OK    {label}  ->  {len(items)} items, "
              f"{pics} with image, {fresh}")
        print(f"        {clip(items[0]['title'], 70)}")

    print()
    if bad:
        print(f"Feeds needing attention: {', '.join(bad)}")
        return 1
    print("All feeds healthy.")
    return 0


def main():
    if "--check" in sys.argv:
        return check_feeds()

    dry = "--dry" in sys.argv
    if "--morning" in sys.argv:
        lane = "morning"
    elif "--pulse" in sys.argv:
        lane = "pulse"
    else:
        lane = "breaking"

    webhook = (os.environ.get("CRIER_WEBHOOK", "").strip()
               or os.environ.get("DISCORD_WEBHOOK", "").strip())

    if not webhook and not dry:
        print("CRIER_WEBHOOK is not set.", file=sys.stderr)
        return 1

    if "--test" in sys.argv:
        if dry:
            print("--test sends the only message it has; --dry cancels it.")
            return 0
        post(webhook, [{
            "title": "Town Crier is online",
            "description": f"Watching {len(FEEDS)} feeds. Breaking sweeps run "
                           "through the day, Pulse covers the substantive "
                           "non-disaster news every few hours, and one pick "
                           "lands each morning.",
            "color": 0x3BA55D,
        }])
        return 0

    print(f"Lane: {lane}")
    items = collect(lane)
    print(f"{len(items)} item(s) collected.")

    state = load_state()
    picker = {"morning": pick_morning, "pulse": pick_pulse}.get(
        lane, pick_breaking)
    picks = picker(items, state)

    if not picks:
        print("Nothing worth posting.")
        return 0

    attach_free_mirrors(picks, items)
    if BORROW_IMAGES:
        borrow_images(picks, items)
    if FETCH_MISSING_IMAGES:
        backfill_images(picks)

    if dry:
        print("\n--- dry run, not posting ---")
        print(json.dumps([build_embed(i, lane) for i in picks], indent=2))
        return 0

    header = {"morning": "Morning read", "pulse": "Pulse"}.get(
        lane, "Breaking")

    failed = 0
    try:
        for index, item in enumerate(picks):
            if index:
                time.sleep(POST_GAP_SECONDS)
            post(webhook, [build_embed(item, lane)],
                 content=f"**{header}** — {item['source']}")
            record_post(state, item, datetime.now(timezone.utc))
            save_state(state)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:400]
        print(f"Discord rejected the post: HTTP {err.code} {body}",
              file=sys.stderr)
        failed = 1

    return failed


if __name__ == "__main__":
    sys.exit(main())
