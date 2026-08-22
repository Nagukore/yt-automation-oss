"""Trending topic discovery from free, keyless sources.

Providers (all no-key, verified working):
- reddit_til:     r/todayilearned top posts. Best default for this system, which
                  produces short educational "surprising fact" videos.
- wikipedia:      most-viewed articles yesterday. Strong real-world interest signal.
- google_trends:  daily trending searches via the RSS feed. Note this is news and
                  celebrity heavy, so it needs the LLM refinement pass to become
                  usable video topics.
- youtube_rss:    YouTube's public most-popular feed.

NOTE: the old pytrends-based implementation was removed — Google retired the
endpoint it used and every call returns HTTP 404. The RSS feed below is the
supported replacement and needs no library.

Each provider returns: {title, source, score, keywords}.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from itertools import zip_longest

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.services.sensitive import rejection_reason

# Wikimedia asks API clients to identify themselves.
_UA = "yt-automation/0.1 (https://github.com/yt-automation)"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}


def discover_topics(limit: int = 10, geo: str | None = None) -> list[dict]:
    """Discover topics using the configured provider, falling back across sources.

    Never raises: topic discovery runs unattended on a schedule, and a dead upstream
    should degrade rather than stall the pipeline.
    """
    provider = (settings.trend_provider or "reddit_til").lower()
    geo = geo or settings.trend_geo

    providers = {
        "ai_news": lambda: _ai_news(limit),
        "reddit_til": lambda: _reddit_til(limit),
        "wikipedia": lambda: _wikipedia(limit),
        "google_trends": lambda: _google_trends_rss(limit, geo),
        "youtube_rss": lambda: _youtube_rss(limit),
    }
    # Try the configured provider first, then the others, then static seeds.
    order = [provider] + [p for p in providers if p != provider]

    for name in order:
        fn = providers.get(name)
        if fn is None:
            logger.warning("unknown trend provider '{}'", name)
            continue
        try:
            # News sources apply their own editorial filter in services.news. The generic
            # one below blocks words like "lawsuit"/"died", which are legitimate subjects
            # in tech journalism — applying it here would silently drop real stories.
            topics = fn()
            if name != "ai_news":
                topics = [t for t in topics if _is_usable(t["title"])]
            # Raw trend terms carry no context beyond the subject itself, so the
            # regulated-domain tier applies here but not to curated news feeds.
            topics = _drop_sensitive(topics, strict=(name != "ai_news"))
            if topics:
                logger.info("trend provider '{}' returned {} topics", name, len(topics))
                return topics[:limit]
            logger.warning("trend provider '{}' returned nothing usable", name)
        except Exception as e:  # noqa: BLE001
            logger.warning("trend provider '{}' failed: {}", name, e)

    logger.warning("all trend providers failed; using static seed list")
    return _fallback(limit)


def discover_mixed(limit: int = 10, geo: str | None = None) -> list[dict]:
    """Interleave AI/tech news with general trending stories. Used by long-form.

    Long-form covers both: AI deep-dives stay on the channel's core subject, general
    trending stories reach past it. Callers can tell which is which from `source`
    ("ainews:*" vs "google_trends") and brand the video accordingly — a football
    story must not ship with the "Daily AI news for developers" footer.

    Interleaved rather than concatenated-and-sorted because the two sources score on
    incomparable scales (ai_news ~1-3, google_trends ~8-15); a plain sort by score
    would let google_trends crowd AI news out of every run.

    Never raises: falls back to the standard provider chain if both sources fail.
    """
    geo = geo or settings.trend_geo
    lanes: list[list[dict]] = []
    for name, fetch in (
        ("ai_news", lambda: _ai_news(limit)),
        ("google_trends", lambda: _google_trends_rss(limit, geo)),
    ):
        try:
            topics = fetch()
            # Same asymmetry as discover_topics: curated tech journalism carries its
            # own editorial filter, raw trend terms need the strict one.
            if name != "ai_news":
                topics = [t for t in topics if _is_usable(t["title"])]
            topics = _drop_sensitive(topics, strict=(name != "ai_news"))
            logger.info("mixed: '{}' contributed {} topics", name, len(topics))
        except Exception as e:  # noqa: BLE001
            logger.warning("mixed: provider '{}' failed: {}", name, e)
            topics = []
        lanes.append(topics)

    out: list[dict] = []
    for row in zip_longest(*lanes):
        out.extend(t for t in row if t)
    if not out:
        logger.warning("mixed: both sources empty; falling back to the standard chain")
        return discover_topics(limit, geo)
    return out[:limit]


# --------------------------------------------------------------------------- providers
def _ai_news(limit: int) -> list[dict]:
    """Daily AI news headlines. See services.news for the feed set."""
    from app.services.news import discover_ai_news

    return discover_ai_news(limit=limit)


def _reddit_til(limit: int) -> list[dict]:
    """Top r/todayilearned posts — already phrased as interesting facts."""
    url = f"https://www.reddit.com/r/todayilearned/top/.rss?t=day&limit={max(limit * 2, 20)}"
    with httpx.Client(timeout=30, headers={"User-Agent": _UA}) as client:
        resp = client.get(url)
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    out: list[dict] = []
    for i, entry in enumerate(root.findall("a:entry", _ATOM)):
        title = (entry.findtext("a:title", namespaces=_ATOM) or "").strip()
        # Strip the "TIL that ..." prefix so the topic reads as a subject, not a post.
        title = re.sub(r"^TIL\s*(that\s+|about\s+|,\s*)?", "", title, flags=re.IGNORECASE)
        title = title.strip().rstrip(".")
        if title:
            out.append(_topic(title, "reddit_til", limit - i))
    return out


def _wikipedia(limit: int) -> list[dict]:
    """Yesterday's most-viewed English Wikipedia articles."""
    # Use 2 days back: the previous day's aggregation isn't always published yet.
    day = (date.today() - timedelta(days=2)).strftime("%Y/%m/%d")
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
        f"en.wikipedia/all-access/{day}"
    )
    with httpx.Client(timeout=30, headers={"User-Agent": _UA}) as client:
        resp = client.get(url)
        resp.raise_for_status()

    skip = ("Main_Page", "Special:", "Wikipedia:", "Portal:", "Category:")
    out: list[dict] = []
    for i, art in enumerate(resp.json()["items"][0]["articles"]):
        name = art["article"]
        if name.startswith(skip):
            continue
        out.append(_topic(name.replace("_", " "), "wikipedia", limit - i))
    return out


def _google_trends_rss(limit: int, geo: str) -> list[dict]:
    """Daily trending searches. News/celebrity heavy — refine before scripting.

    A bare trend term ("virat kohli") is useless as a video subject and gives the
    LLM nothing factual to work from, so prefer the real news headline the feed
    attaches to each trend (ht:news_item). Scripts grounded in an actual headline
    hallucinate far less than ones asked to 'research' a two-word search term.
    """
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    with httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = client.get(url)
        resp.raise_for_status()

    ht = {"ht": "https://trends.google.com/trending/rss"}
    out: list[dict] = []
    for i, item in enumerate(root_items := list(ET.fromstring(resp.text).iter("item"))):
        term = (item.findtext("title") or "").strip()
        # Non-US feeds mix scripts (Hindi/Bengali/... in IN); this channel narrates
        # in English, so take the first English-looking headline for the trend.
        headline = next(
            (
                h.strip()
                for n in item.findall("ht:news_item", namespaces=ht)
                if (h := n.findtext("ht:news_item_title", namespaces=ht) or "")
                and _mostly_latin(h)
            ),
            "",
        )
        title = headline or (term if _mostly_latin(term) else "")
        if not title:
            continue
        topic = _topic(title, "google_trends", len(root_items) - i)
        # Keep the trend term searchable even when the headline replaces it.
        if term and term.lower() not in title.lower():
            topic["keywords"] = ([term.lower()] + topic["keywords"])[:6]
        out.append(topic)
    return out


def _youtube_rss(limit: int) -> list[dict]:
    url = "https://www.youtube.com/feeds/videos.xml?chart=mostPopular"
    with httpx.Client(timeout=30, headers={"User-Agent": _UA}) as client:
        resp = client.get(url)
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    return [
        _topic(t.strip(), "youtube_rss", limit - i)
        for i, entry in enumerate(root.findall("a:entry", _ATOM)[:limit])
        if (t := entry.findtext("a:title", namespaces=_ATOM) or "")
    ]


def _fallback(limit: int) -> list[dict]:
    seeds = [
        "Surprising science facts you never learned in school",
        "How AI is changing everyday life",
        "Space discoveries that rewrote the textbooks",
        "Psychology tricks that actually work",
        "The hidden history behind common objects",
        "Money habits of financially successful people",
        "Weird animal abilities explained",
        "Productivity myths that waste your time",
    ]
    return [_topic(t, "fallback", limit - i) for i, t in enumerate(seeds[:limit])]


# --------------------------------------------------------------------------- helpers
def _mostly_latin(text: str) -> bool:
    """True when the text is predominantly ASCII — i.e. English-narratable.

    Headlines in Devanagari/Bengali/etc. are near-100% non-ASCII, while English
    ones only carry the odd curly quote or em-dash, so a simple ratio separates
    them cleanly.
    """
    if not text:
        return False
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return non_ascii / len(text) < 0.2


def _topic(title: str, source: str, score: float) -> dict:
    return {
        "title": title[:500],
        "source": source,
        "score": float(max(score, 0)),
        "keywords": _keywords(title),
    }


_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "to", "for", "and", "or", "is", "was",
    "were", "that", "this", "with", "by", "at", "from", "as", "it", "its", "his",
    "her", "their", "who", "when", "what", "why", "how", "after", "before",
}


def _keywords(title: str, n: int = 6) -> list[str]:
    words = re.findall(r"[A-Za-z0-9']{3,}", title.lower())
    return [w for w in words if w not in _STOPWORDS][:n]


# Topics that make poor or risky automated videos.
_BLOCK = re.compile(
    r"\b(died|dead|death|killed|murder|shooting|rape|assault|arrested|indicted|"
    r"lawsuit|obituary|nsfw|porn|suicide|verdict|earthquake|hurricane|crash)\b",
    re.IGNORECASE,
)


def _drop_sensitive(topics: list[dict], *, strict: bool) -> list[dict]:
    """Remove finance/legal/medical topics this channel must not auto-narrate.

    See services.sensitive — YouTube demonetizes channels carrying an excessive
    amount of synthetic-persona content on these subjects.
    """
    kept: list[dict] = []
    for t in topics:
        if reason := rejection_reason(t["title"], strict=strict):
            logger.info("dropped sensitive topic ({}): {}", reason, t["title"][:80])
            continue
        kept.append(t)
    return kept


def _is_usable(title: str) -> bool:
    """Filter out topics that are too short, or news the channel shouldn't touch.

    Trending feeds surface tragedies and legal news constantly. An unattended
    pipeline should not be generating upbeat narrated shorts about those.
    """
    if len(title) < 12 or len(title.split()) < 3:
        return False  # bare names like "jason statham" aren't video topics
    return not _BLOCK.search(title)
