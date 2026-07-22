"""AI news discovery from free RSS feeds (no keys, all verified working).

Feeds are grouped by character:
- EDITORIAL: curated tech journalism. Best signal-to-noise for a daily news short.
- AGGREGATE: Google News. High volume, some duplication and low-quality syndication.
- RESEARCH:  arXiv cs.AI/cs.LG. Where genuinely new models/papers surface first,
             but titles are dense academic prose and need an LLM rewrite.

Returns the same {title, source, score, keywords} shape as services.trends, plus
`url` so the script node can cite the story.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.core.logging import logger

_UA = "Mozilla/5.0 (compatible; yt-automation/0.1)"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}

EDITORIAL = {
    "techcrunch": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "venturebeat": "https://venturebeat.com/category/ai/feed/",
    "theverge": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "mit_tech_review": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
}
AGGREGATE = {
    "google_news": (
        "https://news.google.com/rss/search?"
        "q=artificial+intelligence+OR+LLM+OR+%22AI+model%22+when:2d&hl=en-US&gl=US&ceid=US:en"
    ),
}
RESEARCH = {
    "arxiv_ai": "http://export.arxiv.org/rss/cs.AI",
    "arxiv_lg": "http://export.arxiv.org/rss/cs.LG",
}

# Source weighting: editorial stories make better shorts than raw paper titles.
_WEIGHT = {"editorial": 3.0, "aggregate": 2.0, "research": 1.0}

# Headlines that don't make watchable videos.
_BLOCK = re.compile(
    r"\b(webinar|podcast|newsletter|sponsored|deal[s]?\b|coupon|discount|hiring|"
    r"job openings?|advertisement|obituary|died|arrested|indicted)\b",
    re.IGNORECASE,
)

# Financial clickbait syndicated heavily through Google News. These are stock-promo
# listicles, not AI news, and generating confident videos about them would put the
# channel into financial-advice territory by accident.
_STOCK_SPAM = re.compile(
    r"\b(stocks?\s+to\s+buy|no-brainer|unstoppable|millionaire|"
    r"best\s+ai\s+stocks?|should\s+you\s+buy|price\s+target|"
    r"buy\s+now|skyrocket|soar|surge[sd]?\b|billionaires?\s+are\s+buying)\b",
    re.IGNORECASE,
)
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "to", "for", "and", "or", "is", "was", "with",
    "by", "at", "from", "as", "it", "its", "this", "that", "new", "how", "why", "what",
}


def _fetch(url: str) -> list[tuple[str, str, datetime | None]]:
    """Return [(title, link, published)] for an RSS or Atom feed."""
    resp = httpx.get(url, timeout=25, headers={"User-Agent": _UA}, follow_redirects=True)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    out: list[tuple[str, str, datetime | None]] = []
    for item in root.iter("item"):  # RSS 2.0
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        out.append((title, link, _parse_date(item.findtext("pubDate"))))
    if not out:  # Atom
        for entry in root.findall("a:entry", _ATOM):
            title = (entry.findtext("a:title", namespaces=_ATOM) or "").strip()
            link_el = entry.find("a:link", _ATOM)
            link = link_el.get("href", "") if link_el is not None else ""
            out.append((title, link, _parse_date(entry.findtext("a:updated", namespaces=_ATOM))))
    return out


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


def _clean_title(title: str, source: str) -> str:
    # Feeds carry HTML entities (&#8217;, &amp;) that would otherwise be read aloud
    # literally by the TTS engine. Unescape twice: some feeds double-encode.
    title = html.unescape(html.unescape(title or ""))
    # Google News appends " - Publisher"; arXiv suffixes "(arXiv:1234 [cs.AI])".
    if source == "google_news":
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)
    title = re.sub(r"\s*\(arXiv:[^)]+\)\s*$", "", title)
    return re.sub(r"\s+", " ", title).strip().rstrip(".")


def _keywords(title: str, n: int = 6) -> list[str]:
    words = re.findall(r"[A-Za-z0-9'.-]{3,}", title.lower())
    return [w for w in words if w not in _STOPWORDS][:n]


def _normalize(title: str) -> str:
    """Key used to detect the same story syndicated across outlets."""
    return " ".join(sorted(re.findall(r"[a-z0-9]{4,}", title.lower())))[:120]


def discover_ai_news(limit: int = 10, max_age_hours: int = 48) -> list[dict]:
    """Fetch, merge, dedupe and rank recent AI news headlines."""
    groups = [("editorial", EDITORIAL), ("aggregate", AGGREGATE), ("research", RESEARCH)]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    seen: set[str] = set()
    topics: list[dict] = []

    for kind, feeds in groups:
        for name, url in feeds.items():
            try:
                entries = _fetch(url)
            except Exception as e:  # noqa: BLE001
                logger.warning("news feed '{}' failed: {}", name, e)
                continue

            kept = 0
            for rank, (raw_title, link, published) in enumerate(entries[:40]):
                title = _clean_title(raw_title, name)
                if len(title) < 20 or _BLOCK.search(title) or _STOCK_SPAM.search(title):
                    continue
                if published and published < cutoff:
                    continue
                key = _normalize(title)
                if not key or key in seen:
                    continue
                seen.add(key)

                # Fresher and higher up the feed ranks better; editorial outranks research.
                recency = 1.0
                if published:
                    age_h = (datetime.now(timezone.utc) - published).total_seconds() / 3600
                    recency = max(0.2, 1.0 - age_h / max(max_age_hours, 1))
                topics.append(
                    {
                        "title": title[:500],
                        "source": f"ainews:{name}",
                        "score": round(_WEIGHT[kind] * recency * (1 - rank / 100), 3),
                        "keywords": _keywords(title),
                        "url": link,
                    }
                )
                kept += 1
            logger.debug("news feed '{}': {} usable", name, kept)

    topics.sort(key=lambda t: t["score"], reverse=True)
    logger.info("ai news: {} unique stories, returning {}", len(topics), min(limit, len(topics)))
    return topics[:limit]
