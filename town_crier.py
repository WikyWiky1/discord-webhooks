#!/usr/bin/env python3
"""
TOWN CRIER - WikyWiky Studios
Three lanes, one script.

  breaking  (default)  Sweeps wire feeds every ~20 minutes and posts anything
                       that is both recent and genuinely urgent. Up to 17 a
                       sweep, no more than 4 from any one outlet, and nothing
                       at all on a quiet run - the cap is a ceiling, not a
                       quota, and there is no floor under it.

  --pulse               Sweeps the same feeds every few hours for the
                       substantive, non-disaster news Breaking's gate is
                       built to exclude - legislation, courts, the economy,
                       elections, diplomacy, science and space. Up to 6 a
                       sweep, no more than 2 from any one outlet, same no-
                       floor rule as Breaking: a quiet run posts nothing.

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

*** ON THE CEILING AND THE GATE ***
These two move together. The ceiling used to be 3, which meant the keyword
gate could be sloppy - a headline that matched "coup" because it contained
"couple" still had to outrank three real emergencies to reach the channel, and
it almost never did. Raising the ceiling removes that competition, so anything
the gate admits now posts. The gate was rebuilt on word boundaries at the same
time, and the two numbers should not be edited independently: raising the
ceiling again without re-testing the gate against BREAKING_WORDS is how the
channel fills up with deadlines and heart attacks.

*** ON PROCEEDINGS ***
An inquest into an explosion, an autopsy after an attack and a committee
investigating a drone strike all carry a breaking word, and all three are
about an event that happened weeks or months ago. Nothing in the feed says so:
the article really was filed twenty minutes ago, so the age gate passes it.
Three of eleven posts in one sample sweep were this.

The line drawn is process versus outcome. A verdict, a sentencing or a
decision not to charge is itself the event and still posts; the hearing, the
deliberation and the testimony that precede it do not. See PROCESS_WORDS and
BREAKING_OUTCOMES. This is a breaking-lane veto only - an inquest is a fine
morning read, so it is not in MUTE_WORDS.

*** ON DUPLICATES VERSUS DEVELOPMENTS ***
A story that moves is not a story repeated. "Father to be sentenced" in the
morning and "father jailed for 15 years" in the afternoon share four words out
of thirteen - 0.31 overlap, nowhere near the 0.55 dedupe bar - so both post,
correctly. That is working as intended; do not tighten the threshold to
"fix" it.

Linking the second post back to the first was tried and abandoned. Measured on
real pairs, the fingerprint cannot tell a development from a different event of
the same kind: two wildfire evacuation headlines from California and Spain
score 0.43, higher than the sentencing pair above scores against itself. Any
threshold that catches the follow-ups also captions unrelated disasters as
each other. The overlap is still printed to the log when a pick lands in that
band, so the sweep is legible, but nothing goes in the embed on that basis.

Dedupe memory is time boxed rather than counted. Holding the last N headlines
forever meant the window silently stretched from a day and a half in a busy
week to several weeks in a quiet one, and a long-running story could be muted
by a headline nobody remembered. See DEDUPE_WINDOW_HOURS.

*** ON FEED ROT ***
Feeds do not fail loudly. They freeze. Both WSJ and the Telegraph were serving
well-formed XML full of articles from 2024 and reporting themselves healthy.
That is why --check now fails a feed on staleness, not just on errors. Run it
monthly. If something goes STALE, the fix is almost always to move that source
onto the Google News pattern the wires already use.

*** WHY PULSE IS A SEPARATE LANE, NOT A LOOSER BREAKING ***
Breaking's gate is disasters on purpose - that is what BREAKING_WORDS is for,
and it should stay that way. But a channel that only ever posts disasters
reads as if disasters are the only thing happening, which is a distortion of
its own. Pulse is the correction: a second gate (PULSE_WORDS/PULSE_PHRASES),
a slower cadence, and a hard line against the first gate's subject matter -
looks_pulse refuses anything looks_breaking already claims, even on the one
word the two lists share (a ceasefire can be both; it is left to Breaking).
Loosening BREAKING_WORDS instead would have meant either disasters drowning
out the calmer news or the calmer news diluting the urgency of real ones.
Two gates keeps both jobs honest.

Flags:
  --check     fetch every feed, report counts, freshness and images, post none
  --dry       print what it would post, send nothing
  --test      send a single "online" message
  --morning   run the morning pick instead of the breaking sweep
  --pulse     run the pulse sweep instead of the breaking sweep
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
MEDIA = "{http://search.yahoo.com/mrss/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"

# Tracking pixels, spacers and publisher logos masquerading as article images.
JUNK_IMAGE_HINTS = ("1x1", "pixel", "spacer", "favicon", "/logo", "blank.")

# ── tuning knobs ────────────────────────────────────────────────────────────
# These are the anti-spam controls. If the channel ever gets noisy, this block
# is the only thing you need to touch.

# Ceiling, not a target. There is no floor and never was: a sweep that finds
# nothing urgent posts nothing and says so. This number only decides how much
# a genuinely busy two hours is allowed to deliver at once.
#
# Note what it costs. The sweep runs every 20 minutes but looks back 120, so
# the first run after raising this will clear the whole backlog in that window
# in one go - up to the new ceiling, all at once. After that it settles: each
# sweep only sees what is new since the last one, because everything else is
# already in the seen file. If the catch-up burst matters, run --dry first to
# see its size.
MAX_BREAKING_PER_RUN = 17

# Per outlet, per sweep. The old ceiling of 3 was doing two jobs at once, and
# only one of them was flood control - it also stopped any single paper from
# owning the channel, simply by running out of room. At 17 that stops being
# automatic, so it is stated instead. Seventeen stories from seventeen desks
# is a wire feed; seventeen from one desk is that paper's front page.
MAX_PER_OUTLET_PER_RUN = 4
BREAKING_MAX_AGE_MINUTES = 120  # older than this is not breaking, it is news
BREAKING_FRESHNESS_BUCKET_MINUTES = 30  # inside one bucket, source weight wins
MORNING_WINDOW_HOURS = 30       # how far back the morning pick may reach
SIMILARITY_THRESHOLD = 0.55     # 0-1; higher = more willing to post near-dupes
SEEN_LIMIT = 800                # article IDs remembered
POST_MEMORY_LIMIT = 400         # posted headlines remembered
POST_GAP_SECONDS = 1.5          # spacing between messages, Discord rate limit

# How far back the dedupe actually looks. The count above is only a ceiling on
# the file size; this is the real window, and it is in hours on purpose. At
# three posts a sweep the old count-only memory covered a day and a half in a
# heavy news week and a month in a slow one, which meant "have we posted this?"
# quietly meant something different depending on the week. Two days is long
# enough that no outlet's re-file of the same headline slips through and short
# enough that a story running all month can still surface when it moves.
DEDUPE_WINDOW_HOURS = 48

# Pulse: the third lane. Breaking is disasters, on a 20-minute sweep, no
# floor. Morning is one science-flavored pick, once a day. Neither one ever
# reports "Congress passed a budget" or "the Fed held rates" - Pulse exists
# to run every few hours and cover the substantive, non-morbid news the other
# two lanes structurally cannot.
#
# The age window is wider than the sweep cadence on purpose, the same way
# Breaking's 120-minute gate outruns its 20-minute schedule: a missed or
# delayed run should not mean the story never surfaces at all.
PULSE_MAX_PER_RUN = 6
PULSE_MAX_PER_OUTLET_PER_RUN = 2
PULSE_MAX_AGE_MINUTES = 240  # 4h against a 3h cadence

# Log-only. When a pick sits between this and SIMILARITY_THRESHOLD it is
# printed with its score, so a run that looks like a repeat can be checked
# against the number that let it through. See the note above on why this is a
# diagnostic and not a feature: the band is real, but it is not clean enough to
# put in front of readers.
RELATED_NOTE_OVERLAP = 0.30

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
PULSE_IMAGE = "thumbnail"

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
        "lanes": ("breaking", "pulse", "morning"),
        "suffix": ("Reuters", "reuters.com"),
        "paywall": True,
    },
    {
        "name": "Reuters World",
        # Same desk as the entry above, so the per-outlet cap has to see them
        # as one paper or Reuters quietly gets double the allowance.
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

    # ── left of center. Kept, unchanged, just no longer unopposed. ──
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

    # ── center. The ballast. ──
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
#
# Matched on word boundaries, which is not fussiness. Plain substring matching
# fired "coup" on "couple", "dead" on "deadline" and on "deadlocked", and
# "recall" on "Biden recalls his years in the Senate" - all of which posted as
# BREAKING. At a ceiling of three they usually lost the slot to something
# actually urgent and you never saw them. There is no such competition at
# seventeen, so the gate has to be the filter now.
BREAKING_WORDS = (
    "breaking", "urgent", "killed", "kills", "kill", "dead", "deadly",
    "explosion", "earthquake", "quake", "tsunami", "shooting", "gunman",
    "attack", "airstrike", "invasion", "hostage", "wildfire", "wildfires",
    "hurricane", "tornado", "nuclear", "outbreak", "indicted", "manhunt",
    "lockdown", "recall", "shutdown", "ceasefire", "coup", "emergency",
)
# Two deliberate absences, both learned from the sample above.
#
# "kills" and "kill" are new. The list had "killed" and nothing else, so the
# ordinary wire present tense - "Landslide kills 30 in Peru", "Floods kill at
# least 40" - was never eligible to post at all. That gap was invisible at a
# ceiling of three because something else always filled the slot.
#
# "attacks" was removed. As a noun it is real, but as a verb it is the single
# most common political headline in the roster ("Trump attacks rival"), and
# there is no boundary rule that separates them. The singular still matches,
# and a genuine attack story almost always carries a second word from this
# list anyway - killed, dead, death toll.

# Multi-word, matched as phrases on the same boundary rule.
BREAKING_PHRASES = (
    "death toll", "plane crash", "state of emergency", "steps down",
    "mass casualties", "opens fire", "shot dead",
)

# Matched as prefixes, because only the tail varies: evacuate, evacuated,
# evacuation. Keep these genuinely unambiguous - a loose stem here undoes the
# boundary rule above.
BREAKING_STEMS = (
    "evacuat", "assassinat", "derail", "impeach", "collaps", "resign",
    "casualt",
)

# Checked first. These are the phrases that contain a breaking word and are
# reliably not an event - a heart attack is not an attack, an emergency room is
# not an emergency, a dead heat is a poll result. Anything listed here vetoes
# the headline outright, so keep it to cases with no honest exception.
NOT_BREAKING = (
    "heart attack", "panic attack", "attack ad", "attacks on democracy",
    "dead heat", "dead last", "dead end", "walking dead", "dead of night",
    "nuclear option", "nuclear family", "emergency room", "emergency fund",
    "emergency savings", "recalls how", "recalls the", "recalls his",
    "recalls her", "shutdown of the", "under attack from critics",
    # A bill dying in committee is not a casualty event.
    "kills bill", "kills the bill", "kills deal", "kills the deal",
    "kills plan", "kills proposal", "kills measure", "kills amendment",
)

# Proceedings about an event that already happened. These carry a breaking
# word - an inquest into an explosion, an autopsy after an attack - but the
# event is weeks or months cold. The age gate cannot see it, because the
# article really was filed twenty minutes ago; only the event is old.
#
# Breaking lane only, deliberately. An inquest is a perfectly good morning
# read, so this veto lives in looks_breaking rather than in MUTE_WORDS.
PROCESS_WORDS = (
    "inquest", "autopsy", "post-mortem", "coroner", "toxicology",
    "testifies", "testified", "testimony", "takes the stand",
    "court hears", "jury hears", "hearing told", "trial hears",
    "goes on trial", "trial begins", "trial opens", "on trial for",
    "jury selection", "opening statements", "closing arguments",
    "jury deliberat", "pleads not guilty", "arraigned", "arraignment",
    "subpoena", "deposition", "lawsuit", "files suit", "sues over",
    "anniversary of", "years after", "years since", "decade after",
    # Oversight, not police work. The plain verb is far too broad to list -
    # "police investigating shooting at mall" is exactly the kind of fresh
    # event this gate exists to catch - so the actor has to be named.
    "lawmakers investigating", "democrats investigating",
    "republicans investigating", "committee investigating",
    "congress investigating", "senate investigating",
    "house investigating", "senators investigating",
    "opens investigation", "launches investigation",
    "calls for investigation", "demand answers", "demands answers",
)

# ...unless the headline carries one of these, because a decision is itself
# the event. This is the whole distinction: the inquest is process, the
# verdict is news. "Father to be sentenced" and "father jailed for 15 years"
# both survive on this list, which is the point - that pair was the reason
# for drawing the line here rather than muting the category wholesale.
BREAKING_OUTCOMES = (
    "sentenced", "sentencing", "convicted", "conviction", "verdict",
    "acquitted", "cleared of", "jailed", "found guilty", "pleads guilty",
    "no charges", "charges dropped", "charged", "indicted", "arrested",
    "guilty of", "ruling", "ruled", "settles",
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
#
# Note it cannot see through Google News: those links are opaque redirect IDs,
# so the wires are on the honour system here. In practice they do not file
# video repackages the way the tabloids do.
MUTE_URL_PARTS = (
    "/video/", "/videos/", "/gallery/", "/galleries/", "/photos/",
    "/podcast/", "/podcasts/", "/slideshow",
)

# The Pulse lane's gate - same shape as BREAKING_WORDS/PHRASES, same
# word-boundary matcher, deliberately disjoint subject matter. Breaking is
# disasters and only disasters, on purpose; Pulse is everything substantive
# that isn't one - legislation, courts, the economy, elections, diplomacy,
# science and space. Not fluff (that's still MUTE_WORDS's job), not morbid
# (that's the point of a second lane instead of loosening the first one).
#
# The source roster is the same wire/national outlets Breaking reads, so
# "unveils" or "opens" firing on a small paper's local-business piece isn't a
# real risk here the way it would be on an unfiltered feed.
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
    """%-I is a glibc extension - strip the pad by hand so this does not depend
    on which libc the runner happens to ship."""
    if not stamp:
        return None
    return f"{stamp.astimezone(LOCAL_TZ):%I:%M %p %Z}".lstrip("0")


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
    take the largest one offered.

    Every candidate is validated as it is considered, not once at the end. A
    feed that advertises its masthead logo in media:content and the real photo
    in an enclosure used to end up with no image at all: the logo won on width,
    then failed the junk check, and the enclosure was never reached."""
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


# ── selection ───────────────────────────────────────────────────────────────

def age_minutes(item, now):
    """Never negative. A publisher whose clock runs fast, or who post-dates an
    embargoed piece, would otherwise land in a negative freshness bucket and
    hold the top breaking slot on every sweep until it aged into the present.
    The future is treated as 'just now'."""
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
    """Shared by both lane gates. Whole words and phrases get a boundary on
    both ends; stems get one on the left only, so 'evacuat' still catches
    'evacuations' while 'coup' can no longer catch 'couple'."""
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
    """The Pulse gate. Disasters stay Breaking's alone - a headline that
    already clears looks_breaking is left there rather than doubled up here,
    even on the rare word both gates share (a ceasefire, say)."""
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


def note_related(fp, state, now):
    """Log only. Prints the nearest recent headline when a pick clears the
    dedupe but is not a stranger to it, so a post that reads like a repeat can
    be checked against the number that let it through.

    This is deliberately not surfaced to the channel. The band is genuinely
    informative to a human reading a log next to the two headlines, and
    genuinely unreliable as an automatic claim - see the module docstring."""
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
    """Newest first, urgent only, deduped against everything posted inside the
    memory window, and hard capped. The cap is what keeps an outbreak of bad
    news from becoming an outbreak of notifications."""
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
        # Checked before the expensive tests, and only skips this item - the
        # sweep keeps going, so the slot passes to the next paper down rather
        # than being lost.
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
    """Same shape as pick_breaking - gate, freshness/weight rank, per-outlet
    cap, shared dedupe window - with its own numbers and its own gate. The
    dedupe fingerprints and the seen-id set are shared with Breaking on
    purpose: a story Breaking already posted should not resurface here just
    because it also happens to use a Pulse word."""
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
    """Exactly one article. Scored for interest, with a small daily random
    nudge so it does not pick the same kind of story every morning."""
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

    style = {"morning": MORNING_IMAGE, "pulse": PULSE_IMAGE}.get(
        lane, BREAKING_IMAGE)
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
    from the front, so it has to be chronological.

    Two shapes are readable. The current one records each post as a small
    object - fingerprint, headline, source, timestamp - because the dedupe
    window needs the time, and the headline is there so the state file can be
    read by a human wondering why something did or did not post. The old shape
    was a bare list of fingerprints. Old files are migrated on first read and
    rewritten on first save; nothing needs doing by hand."""
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

    # Legacy fingerprints carry no timestamp. Stamping them now means they age
    # out over the next two days rather than all at once, so the migration run
    # cannot repost the last thing the old build sent.
    if not posts:
        now = datetime.now(timezone.utc)
        for fp in data.get("titles", []):
            if isinstance(fp, str) and fp and fp not in seen:
                seen.add(fp)
                posts.append({"fp": fp, "title": "", "source": "",
                              "at": now})

    return {"ids": list(dict.fromkeys(ids)), "posts": posts}


def save_state(state):
    """ids are deduped on the way out because a mirror's id can already be in
    the list from an earlier sweep, and there is no sense spending the cap on
    it twice. 'titles' is written alongside the real record purely so an older
    checkout of this script can still read the file if you ever roll back."""
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
    """Posts inside the window. An entry with an unreadable timestamp is kept
    rather than dropped - erring toward remembering is the safe direction for a
    dedupe, and save_state always writes one, so it self-corrects."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    return [p for p in state["posts"] if p["at"] is None or p["at"] >= cutoff]


def record_post(state, item, now):
    state["ids"].append(item["id"])
    # The mirror has now been shown to the channel. Burn its ID too, or a later
    # sweep could pick it up as a fresh story and post the same thing a second
    # time under a different masthead.
    if item.get("mirror"):
        state["ids"].append(item["mirror"]["id"])
    state["posts"].append({
        "fp": item.get("fingerprint") or fingerprint(item["title"]),
        "title": item["title"],
        "source": item["source"],
        "at": now,
    })


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
            newest = max((now - max(stamps)).total_seconds() / 60.0, 0.0)
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
    if "--morning" in sys.argv:
        lane = "morning"
    elif "--pulse" in sys.argv:
        lane = "pulse"
    else:
        lane = "breaking"

    # Its own secret so news can go to a different channel than StormWatch.
    # Falls back to the StormWatch webhook if you'd rather share one channel.
    webhook = (os.environ.get("CRIER_WEBHOOK", "").strip()
               or os.environ.get("DISCORD_WEBHOOK", "").strip())

    if not webhook and not dry:
        print("CRIER_WEBHOOK is not set.", file=sys.stderr)
        return 1

    if "--test" in sys.argv:
        # --test is the one mode with nothing to preview, so pairing it with
        # --dry used to mean posting to an empty URL.
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

    header = {"morning": "Morning read", "pulse": "Pulse"}.get(
        lane, "Breaking")

    # State is saved after every successful send. If send 2 of 3 dies, the
    # first one stays recorded and does not repost on the next sweep.
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
