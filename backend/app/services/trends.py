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

import httpx

from app.core.config import settings
from app.core.logging import logger

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
            if topics:
                logger.info("trend provider '{}' returned {} topics", name, len(topics))
                return topics[:limit]
            logger.warning("trend provider '{}' returned nothing usable", name)
        except Exception as e:  # noqa: BLE001
            logger.warning("trend provider '{}' failed: {}", name, e)

    logger.warning("all trend providers failed; using static seed list")
    return _fallback(limit)


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
    """Daily trending searches. News/celebrity heavy — refine before scripting."""
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    with httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = client.get(url)
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    return [
        _topic(t.strip(), "google_trends", limit - i)
        for i, item in enumerate(root.iter("item"))
        if (t := item.findtext("title") or "")
    ]


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


def _is_usable(title: str) -> bool:
    """Filter out topics that are too short, or news the channel shouldn't touch.

    Trending feeds surface tragedies and legal news constantly. An unattended
    pipeline should not be generating upbeat narrated shorts about those.
    """
    if len(title) < 12 or len(title.split()) < 3:
        return False  # bare names like "jason statham" aren't video topics
    return not _BLOCK.search(title)
