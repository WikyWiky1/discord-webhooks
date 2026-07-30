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

Sources: Reuters, AP, WSJ and the Telegraph come through Google News'
source-filtered feed - the first two shut their public RSS years ago, and the
other two still serve feeds that no editor has filed to since 2024. Everyone
else publishes a live feed and is read directly. The roster is deliberately
mixed: wires in the middle, BBC/NPR/Guardian on the left, WSJ/Telegraph/Fox/
Post/Examiner on the right, Sky/Hill/Monitor filling the center, with the
right-of-center outlets weighted at or above their counterparts so they win
the tie when two papers file the same story. Only headlines, summaries and
links are posted - never full text.

Paywalls: several of the best sources meter their articles. When a metered one
wins a slot, the run's leftovers are searched for the same story from a source
that opens free, and that link is attached to the same embed as one extra
field. One mirror, on the existing post - never a second message.

Images: most feeds ship an article image in the XML. Anything routed through
Google News does not, so a bare item first tries to borrow the photo off
another outlet's copy of the same story - credited in the footer - and failing
that has its og:image pulled from the article page.

*** ON FEED ROT ***
Feeds do not fail loudly. They freeze. Both WSJ and the Telegraph were serving
well-formed XML full of articles from 2024 and reporting themselves healthy.
That is why --check now fails a feed on staleness, not just on errors. Run it
monthly. If something goes STALE, the fix is almost always to move that source
onto the Google News pattern the wires already use.

Flags:
  --check     fetch every feed, report counts, freshness and images, post none
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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Chicago")
except Exception:
    LOCAL_TZ = timezone.utc

BRAND = "Town Crier"
SEEN_FILE = "crier_seen.json"
USER_AGENT = "TownCrier/1.0 (github.com/WikyWiky1)"

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"

# Tracking pixels, spacers and publisher logos masquerading as article images.
JUNK_IMAGE_HINTS = ("1x1", "pixel", "spacer", "favicon", "/logo", "blank.")

# ── tuning knobs ────────────────────────────────────────────────────────────
# These are the anti-spam controls. If the channel ever gets noisy, this block
# is the only thing you need to touch.

MAX_BREAKING_PER_RUN = 3        # hard ceiling per sweep, no exceptions
BREAKING_MAX_AGE_MINUTES = 120  # older than this is not breaking, it is news
BREAKING_FRESHNESS_BUCKET_MINUTES = 30  # inside one bucket, source weight wins
MORNING_WINDOW_HOURS = 30       # how far back the morning pick may reach
SIMILARITY_THRESHOLD = 0.55     # 0-1; higher = more willing to post near-dupes
SEEN_LIMIT = 800                # article IDs remembered
TITLE_LIMIT = 300               # headline fingerprints remembered
POST_GAP_SECONDS = 1.5          # spacing between messages, Discord rate limit

# A feed whose newest article is older than this has stopped being maintained,
# whatever its HTTP status says. Only --check uses it. Set generously: the
# slowest live feed on the roster files roughly daily, and the two dead ones
# were 35 and 549 days cold, so there is no ambiguity to split.
STALE_FEED_HOURS = 36

# Article images. "image" is a full-width banner, "thumbnail" is a small square
# in the corner, None turns it off. Flip BREAKING_IMAGE to "image" if you want
# the breaking posts to be as loud visually as the morning read is.
MORNING_IMAGE = "image"
BREAKING_IMAGE = "thumbnail"

# Anything through Google News arrives with no picture. Two fallbacks, in
# order: borrow the photo from another outlet's copy of the same story (free,
# already in memory, credited in the footer), then scrape og:image off the
# article page. Only ever runs on items already chosen for posting.
BORROW_IMAGES = True
FETCH_MISSING_IMAGES = True
OG_TIMEOUT_SECONDS = 8
OG_READ_BYTES = 200_000  # <head> lives at the top; no reason to read the rest
OG_USER_AGENT = "Mozilla/5.0 (compatible; TownCrier/1.0; +github.com/WikyWiky1)"

# Same-story matching, used by both the free mirror and the borrowed photo.
# The similarity bar is deliberately no looser than the dedupe's, because a
# link to the wrong story is worse than no link at all. The age gap is a sanity
# check: two papers filing the same event do it within hours, so a "match" from
# two days ago is a different story wearing similar words.
MIRROR_SIMILARITY = 0.55
MIRROR_MAX_AGE_GAP_HOURS = 12

# ── feeds ───────────────────────────────────────────────────────────────────
# weight  nudges the morning pick toward sources you like, and breaks ties in
#         the breaking sweep. This is the balance dial - see breaking_rank.
# lanes   which lane the feed feeds. Science, feature, opinion and any feed
#         too slow to ever clear the 120-minute gate are morning-only.
# suffix  Google News appends " - Publisher" to every headline; strip it. It
#         is inconsistent about whether that is the brand or the domain, so
#         list both.
# paywall True if the article is metered or walled. Does not affect selection
#         at all - a walled source can still win the slot on merit. It only
#         tells attach_free_mirrors to go looking for a readable second link.

GOOGLE_NEWS = ("https://news.google.com/rss/search?q=site%3A{site}+when%3A1d"
               "&hl=en-US&gl=US&ceid=US%3Aen")

FEEDS = [
    # ── wires. Neutral by trade, top of the board on purpose. ──
    {
        "name": "Reuters",
        "url": GOOGLE_NEWS.format(site="reuters.com"),
        "weight": 3.0,
        "color": 0xFF8000,
        "lanes": ("breaking", "morning"),
        "suffix": ("Reuters", "reuters.com"),
        "paywall": True,
    },
    {
        "name": "Reuters World",
        "url": "https://news.google.com/rss/search?"
               "q=site%3Areuters.com+world+when%3A1d"
               "&hl=en-US&gl=US&ceid=US%3Aen",
        "weight": 3.0,
        "color": 0xFF8000,
        "lanes": ("breaking", "morning"),
        "suffix": ("Reuters", "reuters.com"),
        "paywall": True,
    },
    {
        "name": "AP",
        "url": GOOGLE_NEWS.format(site="apnews.com"),
        "weight": 2.5,
        "color": 0xFF322E,
        "lanes": ("breaking", "morning"),
        "suffix": ("AP News", "The Associated Press", "Associated Press",
                   "apnews.com", "AP"),
    },

    # ── left of center. Kept, unchanged, just no longer unopposed. ──
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

    # ── right of center. Weighted at or above their opposite numbers so the
    # tie-break in breaking_rank actually goes their way. WSJ matches Reuters,
    # Telegraph and Fox clear AP, and nothing on the left sits above 2.0.
    #
    # WSJ and Telegraph run through Google News because their own feeds died:
    # feeds.a.dj.com last filed 549 days ago and telegraph.co.uk/news/rss.xml
    # 35 days ago, both while returning a healthy wall of stale articles. Cost
    # of the workaround is no image in the XML, which is what BORROW_IMAGES
    # exists to paper over. ──
    {
        "name": "WSJ",
        "url": GOOGLE_NEWS.format(site="wsj.com"),
        "weight": 3.0,
        "color": 0x0274B6,
        "lanes": ("breaking", "morning"),
        "suffix": ("The Wall Street Journal", "Wall Street Journal",
                   "wsj.com", "WSJ"),
        "paywall": True,
    },
    {
        "name": "Telegraph",
        "url": GOOGLE_NEWS.format(site="telegraph.co.uk"),
        "weight": 2.6,
        "color": 0x122B49,
        "lanes": ("breaking", "morning"),
        "suffix": ("The Telegraph", "Telegraph.co.uk", "telegraph.co.uk"),
        "paywall": True,
    },
    {
        "name": "Fox News World",
        "url": "https://feeds.foxnews.com/foxnews/world",
        "weight": 2.5,
        "color": 0x003366,
        "lanes": ("breaking", "morning"),
    },
    {
        "name": "New York Post",
        "url": "https://nypost.com/feed/",
        "weight": 2.0,
        "color": 0xC60800,
        "lanes": ("breaking", "morning"),
    },
    {
        "name": "Washington Examiner",
        "url": "https://www.washingtonexaminer.com/feed",
        "weight": 2.0,
        "color": 0x0A2240,
        "lanes": ("breaking", "morning"),
    },

    # ── center. The ballast. ──
    {
        "name": "Sky News",
        "url": "https://feeds.skynews.com/feeds/rss/home.xml",
        "weight": 2.2,
        "color": 0xC70000,
        "lanes": ("breaking", "morning"),
    },
    {
        "name": "The Hill",
        "url": "https://thehill.com/news/feed/",
        "weight": 2.0,
        "color": 0x2E4B33,
        "lanes": ("breaking", "morning"),
    },

    # Morning-only. These exist to give the daily pick something worth reading
    # that is not a disaster.
    #
    # The Monitor is here rather than on the breaking lane because it files
    # roughly once a day - its freshest item measured 19 hours old, so it could
    # never clear the 120-minute gate anyway. Leaving it on the sweep was a
    # wasted HTTP request every twenty minutes.
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
    # Right-of-center commentary. Morning-only deliberately - these are essays
    # and argument, not wire copy, and have no business tripping a breaking
    # sweep no matter what verbs end up in the headline.
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
# The tabloid entries carry more of a load now that the Post is on the roster.
MUTE_WORDS = (
    "nfl", "nba", "mlb", "nhl", "premier league", "world cup", "olympics",
    "box office", "red carpet", "horoscope", "recap", "highlights", "odds",
    "fantasy football", "celebrity", "kardashian", "royal family",
    "what to watch", "best deals", "prime day", "black friday",
)

# Muted by URL, not by words. This exists because the Post filed a video
# repackage of a two-day-old Japanese earthquake with a fresh pubDate, and it
# took the top breaking slot - the age gate trusts the timestamp, and the
# timestamp was lying. Clip reels and galleries get re-dated constantly; the
# written story does not. Cheapest possible fix, and it generalises.
MUTE_URL_PARTS = (
    "/video/", "/videos/", "/gallery/", "/galleries/", "/photos/",
    "/podcast/", "/podcasts/", "/slideshow",
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
    """Longest match first, so listing both "AP News" and "AP" cannot end with
    the short one winning and leaving " News" glued to the headline."""
    for suffix in sorted(suffixes or (), key=len, reverse=True):
        tail = f" - {suffix}"
        if title.lower().endswith(tail.lower()):
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


def title_echo(summary, title):
    """True when the summary is just the headline again. Google News does this
    on every item, with the publisher name tacked on the end."""
    words = set(fingerprint(summary).split())
    head = set(fingerprint(title).split())
    if not words or not head:
        return False
    return len(words & head) / len(head) >= 0.8


def overlap(fp_a, fp_b):
    """Jaccard on the two fingerprints. Pulled out of too_similar so the mirror
    and photo searches can ask 'how alike?' instead of just 'alike enough?'"""
    a, b = set(fp_a.split()), set(fp_b.split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def too_similar(fp, others):
    return any(overlap(fp, other) >= SIMILARITY_THRESHOLD for other in others)


# ── images ──────────────────────────────────────────────────────────────────

def usable_image(url):
    """The Post's feed hands back image URLs with the path slashes percent
    encoded - wp-content%2Fuploads%2F... - which Discord will not render. Only
    unquote when the path itself looks encoded, so CDN wrappers that carry a
    legitimate ?url=https%3A%2F%2F... parameter are left alone."""
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
    """Publishers advertise images four different ways, and half of them bury
    the tags inside a media:group wrapper. Try them in order of reliability and
    take the largest one offered."""
    best, best_width = None, -1

    for tag in (f"{MEDIA}content", f"{MEDIA}thumbnail"):
        candidates = node.findall(tag) + node.findall(f"{MEDIA}group/{tag}")
        for el in candidates:
            url = el.get("url")
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
                best = el.get("url")
                break

    if not best:
        blob = node.findtext(f"{CONTENT}encoded") or node.findtext("description")
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', blob or "", re.I)
        if match:
            best = match.group(1)

    return usable_image(best)


def fetch_og_image(url):
    """Last resort for items that reach posting with no picture. Reads only the
    top of the page, since og:image lives in <head>, and swallows every error:
    a missing image is not worth losing the post over.

    Google News links are refused outright. They resolve through a JavaScript
    redirect shell that carries no meta tags, so following one buys nothing and
    costs up to OG_TIMEOUT_SECONDS - twice a run, every run, forever."""
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

    # Attribute order is not guaranteed, so check both arrangements before
    # falling back to the Twitter card.
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


# ── same-story matching ─────────────────────────────────────────────────────

def same_story(item, pool, accept):
    """Everything in pool that is plausibly the same event as item and passes
    accept(), best match first. Source weight is only a tiebreak - never let a
    heavyweight outlet win on a looser match. Costs no network; the pool was
    already fetched."""
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
    """Several of the heaviest-weighted sources are metered, so they will keep
    winning slots that some readers cannot open. When that happens, hand back
    exactly one of the copies the dedupe was going to discard.

    Strictly one mirror per post, attached to the existing message. This is the
    opposite of posting a story from every outlet that carried it, and nothing
    here is allowed to grow into a second message."""
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
    """Google News strips article images, which is most of the top of the
    weight ladder - Reuters, AP, WSJ and the Telegraph would all post bare.
    Borrow the photo from another outlet's copy of the same event.

    The lender is named in the footer. An uncredited photo under someone else's
    masthead would read as that outlet's own, which it is not."""
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


# ── selection ───────────────────────────────────────────────────────────────

def age_minutes(item, now):
    if not item["published"]:
        return None
    return (now - item["published"]).total_seconds() / 60.0


def muted(item):
    text = f"{item['title']} {item['summary']}".lower()
    if any(word in text for word in MUTE_WORDS):
        return True
    link = item["link"].lower()
    return any(part in link for part in MUTE_URL_PARTS)


def looks_breaking(item):
    return any(word in item["title"].lower() for word in BREAKING_WORDS)


def score_interest(item):
    text = f"{item['title']} {item['summary']}".lower()
    score = item["weight"]
    score += sum(1.5 for word in INTEREST_WORDS if word in text)
    if len(item["summary"]) > 120:
        score += 0.5
    return score


def breaking_rank(item, now):
    """Sort key for the breaking sweep, and the whole balance mechanism.

    Stories are bucketed by how fresh they are, and inside a bucket the higher
    weighted source wins. So a genuinely newer story still beats an older one,
    but when two papers file the same thing in the same half hour, the heavier
    weight takes the slot - and since the first one through claims it, the
    duplicate check then drops the other copy rather than the other way round.

    That is why the weights read the way they do. The wires sit at the top
    because they are the least slanted thing on the roster, WSJ matches them,
    Telegraph and Fox clear AP, and nothing left of center rises above 2.0.
    Change the balance by changing weights, not by deleting sources."""
    age = age_minutes(item, now)
    if age is None:
        age = BREAKING_MAX_AGE_MINUTES
    bucket = int(age // BREAKING_FRESHNESS_BUCKET_MINUTES)
    return (bucket, -item["weight"], age)


def pick_breaking(items, state):
    """Newest first, urgent only, deduped against everything already posted,
    and hard capped. The cap is what keeps an outbreak of bad news from
    becoming an outbreak of notifications."""
    now = datetime.now(timezone.utc)
    fingerprints = list(state["titles"])
    picks = []

    ordered = sorted(items, key=lambda i: breaking_rank(i, now))

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

    # Sits beside the filing time as a masked link. One line, one link, no
    # second message.
    mirror = item.get("mirror")
    if mirror:
        embed["fields"].append({
            "name": "Free version",
            "value": f"[{mirror['source']}]({mirror['link']})",
            "inline": True,
        })

    style = MORNING_IMAGE if lane == "morning" else BREAKING_IMAGE
    if style in ("image", "thumbnail") and item.get("image"):
        embed[style] = {"url": item["image"]}

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
    """Feeds rot, and they rot quietly. A dead feed keeps returning well-formed
    XML full of articles - WSJ was serving a tidy 20 items dated 549 days ago
    and passing every check this function used to make. So freshness is a pass
    condition, not a footnote. The image count is here so you can see which
    sources depend on the borrow-and-scrape fallbacks."""
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
            newest = (now - max(stamps)).total_seconds() / 60.0
            if newest > STALE_FEED_HOURS * 60:
                print(f"  STALE {label}  ->  {len(items)} items, but the "
                      f"newest is {newest / 1440:.1f} days old")
                print("        parses fine, nobody is filing to it - "
                      "move this source to the Google News pattern")
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

    # After selection, before building embeds, so the dry run shows exactly
    # what the channel would get. Order matters: mirrors first, because
    # borrow_images prefers the mirror's photo when there is one, and the
    # network call is last so it only runs on what nothing local could fix.
    attach_free_mirrors(picks, items)
    if BORROW_IMAGES:
        borrow_images(picks, items)
    if FETCH_MISSING_IMAGES:
        backfill_images(picks)

    if dry:
        print("\n--- dry run, not posting ---")
        print(json.dumps([build_embed(i, lane) for i in picks], indent=2))
        return 0

    header = "Morning read" if lane == "morning" else "Breaking"

    # State is saved after every successful send. If send 2 of 3 dies, the
    # first one stays recorded and does not repost on the next sweep.
    failed = 0
    try:
        for index, item in enumerate(picks):
            if index:
                time.sleep(POST_GAP_SECONDS)
            post(webhook, [build_embed(item, lane)],
                 content=f"**{header}** — {item['source']}")
            state["ids"].append(item["id"])
            state["titles"].append(item.get("fingerprint")
                                   or fingerprint(item["title"]))
            # The mirror has now been shown to the channel. Burn its ID too, or
            # a later sweep could pick it up as a fresh story and post the same
            # thing a second time under a different masthead.
            if item.get("mirror"):
                state["ids"].append(item["mirror"]["id"])
            save_state(state)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:400]
        print(f"Discord rejected the post: HTTP {err.code} {body}",
              file=sys.stderr)
        failed = 1

    return failed


if __name__ == "__main__":
    sys.exit(main())
