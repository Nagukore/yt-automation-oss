"""Published-video ledger + engagement stats — the channel's feedback loop.

The pipeline publishes videos but never learned which ones performed. This module
closes the loop:

1. `record_published()`  — every upload is appended to state/published.json
   (a committed file, because CI runners are ephemeral and the SQLite DB dies
   with the runner; same pattern as the seen.json dedupe files).
2. `collect_stats()`     — fetches public view/like/comment counts for ledger
   videos and merges them into state/performance.json.
3. `theme_weights()`     — turns measured engagement into sampling weights so
   theme rotation drifts toward what the audience actually rewards, while
   always keeping exploration weight for untested themes.

Stats are fetched with a plain API key (`YOUTUBE_API_KEY`): public statistics
need no OAuth, and the upload token deliberately has only the youtube.upload
scope, which cannot read stats. Falls back to the OAuth token if no key is set
(works only if that token was minted with a readonly-capable scope).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger

LEDGER_LIMIT = 500  # entries; old videos age out of relevance anyway


def _state_dir() -> Path:
    p = Path(settings.state_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def ledger_file() -> Path:
    return _state_dir() / "published.json"


def performance_file() -> Path:
    return _state_dir() / "performance.json"


def topic_key(title: str) -> str:
    """Stable fingerprint of a topic/theme, tolerant of small edits.

    Same algorithm as run_daily's seen-key so ledger entries and dedupe history
    fingerprint identically.
    """
    words = sorted(set(re.findall(r"[a-z0-9]{4,}", title.lower())))
    return hashlib.sha1(" ".join(words).encode()).hexdigest()[:16]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        logger.warning("could not read {}: {} — treating as empty", path.name, e)
        return {}


# ------------------------------------------------------------------- ledger
def load_ledger() -> list[dict]:
    return _read_json(ledger_file()).get("videos", [])


def record_published(video_id: str, content_type: str, topic: str) -> None:
    """Append an upload to the committed ledger (idempotent per video_id)."""
    videos = load_ledger()
    if any(v.get("video_id") == video_id for v in videos):
        return
    videos.append(
        {
            "video_id": video_id,
            "content_type": content_type,
            "topic": topic,
            "topic_key": topic_key(topic),
            "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    ledger_file().write_text(
        json.dumps({"videos": videos[-LEDGER_LIMIT:]}, indent=1), encoding="utf-8"
    )
    logger.info("ledger: recorded {} ({})", video_id, content_type)


# -------------------------------------------------------------------- stats
def _stats_service():
    from googleapiclient.discovery import build  # noqa: PLC0415

    if settings.youtube_api_key:
        return build(
            "youtube", "v3", developerKey=settings.youtube_api_key, cache_discovery=False
        )
    # Last resort: the upload OAuth token. Only works if it was authorized with a
    # scope that can read (youtube / youtube.readonly); a pure upload token 403s.
    from app.services.youtube import _load_credentials  # noqa: PLC0415

    logger.warning("YOUTUBE_API_KEY not set — trying the OAuth token (may lack scope)")
    return build("youtube", "v3", credentials=_load_credentials(), cache_discovery=False)


def fetch_stats(video_ids: list[str]) -> dict[str, dict]:
    """Public statistics + title for each video id. Missing/private ids are skipped."""
    service = _stats_service()
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):  # API max 50 ids per call
        batch = video_ids[i : i + 50]
        resp = (
            service.videos()
            .list(part="statistics,snippet", id=",".join(batch), maxResults=50)
            .execute()
        )
        for item in resp.get("items", []):
            s = item.get("statistics", {})
            out[item["id"]] = {
                "title": item.get("snippet", {}).get("title", ""),
                "views": int(s.get("viewCount", 0)),
                "likes": int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
            }
    return out


def _video_score(v: dict) -> float:
    """Single engagement number per video.

    Likes and comments dominate views on purpose: for Shorts, raw views are mostly
    an impression count decided by the feed algorithm, while likes/comments measure
    whether *this* content earned a reaction. Comments weigh double — they signal
    the strongest intent and boost distribution most.
    """
    return v.get("likes", 0) + 2 * v.get("comments", 0) + 0.01 * v.get("views", 0)


def collect_stats() -> dict:
    """Fetch stats for every ledger video and merge into performance.json."""
    ledger = load_ledger()
    if not ledger:
        logger.info("ledger empty — nothing to collect (publish some videos first)")
        return {}

    stats = fetch_stats([v["video_id"] for v in ledger])
    perf = _read_json(performance_file()).get("videos", {})
    for entry in ledger:
        vid = entry["video_id"]
        if vid not in stats:  # private, deleted, or not yet processed
            continue
        perf[vid] = {**entry, **stats[vid], "score": round(_video_score(stats[vid]), 2)}

    performance_file().write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "videos": perf,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    logger.info("performance: {} of {} ledger videos have stats", len(perf), len(ledger))
    return perf


def load_performance() -> dict[str, dict]:
    return _read_json(performance_file()).get("videos", {})


# ----------------------------------------------------------------- weighting
# Untested themes keep this base weight so rotation always explores; a theme at
# the top of the measured range gets base + BOOST, i.e. 4x more likely per draw.
_BASE_WEIGHT = 1.0
_SCORE_BOOST = 3.0


def theme_scores(content_type: str = "dev_humor") -> dict[str, float]:
    """Mean engagement score per topic_key, from collected performance data."""
    by_key: dict[str, list[float]] = {}
    for v in load_performance().values():
        if v.get("content_type") != content_type:
            continue
        by_key.setdefault(v["topic_key"], []).append(float(v.get("score", 0.0)))
    return {k: sum(s) / len(s) for k, s in by_key.items()}


def theme_weights(themes: list[str], content_type: str = "dev_humor") -> list[float]:
    """Sampling weight per theme: proven performers get up to 4x the draw odds."""
    scores = theme_scores(content_type)
    max_score = max(scores.values(), default=0.0)
    if max_score <= 0:
        return [_BASE_WEIGHT] * len(themes)
    return [
        _BASE_WEIGHT + _SCORE_BOOST * (scores.get(topic_key(t), 0.0) / max_score)
        for t in themes
    ]


def weighted_sample(items: list[str], weights: list[float], k: int) -> list[str]:
    """Sample k distinct items with probability proportional to weight."""
    import random

    pool = list(zip(items, weights))
    chosen: list[str] = []
    for _ in range(min(k, len(pool))):
        total = sum(w for _, w in pool)
        r = random.uniform(0, total)
        acc = 0.0
        for i, (item, w) in enumerate(pool):
            acc += w
            if r <= acc:
                chosen.append(item)
                pool.pop(i)
                break
    return chosen
