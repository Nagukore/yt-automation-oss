"""Weekly stats collection for the performance feedback loop (CI-friendly).

Reads state/published.json (the upload ledger written by run_daily.py), fetches
public view/like/comment counts for each video, and writes:

- state/performance.json       — machine-readable; biases humor theme rotation
- state/performance_report.md  — human-readable summary, committed so you can
                                 read what's working straight on GitHub

Requires YOUTUBE_API_KEY (a plain Google API key with YouTube Data API v3
enabled — no OAuth). The upload token cannot read stats: it only carries the
youtube.upload scope.

Usage:
    python scripts/collect_stats.py

Exit codes: 0 = stats collected (or nothing to collect yet — that's normal),
            1 = API/credential failure.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.logging import logger, setup_logging  # noqa: E402
from app.services import performance  # noqa: E402


def write_report(perf: dict[str, dict]) -> Path:
    """Render performance.json into a small markdown league table."""
    videos = sorted(perf.values(), key=lambda v: v.get("score", 0), reverse=True)
    total_views = sum(v.get("views", 0) for v in videos)
    total_likes = sum(v.get("likes", 0) for v in videos)

    lines = [
        "# Channel performance",
        "",
        f"_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC — "
        f"{len(videos)} videos, {total_views:,} views, {total_likes:,} likes._",
        "",
        "| # | Video | Type | Views | Likes | Comments | Score |",
        "|---|-------|------|------:|------:|---------:|------:|",
    ]
    for i, v in enumerate(videos[:30], 1):
        title = (v.get("title") or v.get("topic", ""))[:60].replace("|", "\\|")
        lines.append(
            f"| {i} | [{title}](https://youtu.be/{v['video_id']}) "
            f"| {v.get('content_type', '?')} | {v.get('views', 0):,} "
            f"| {v.get('likes', 0):,} | {v.get('comments', 0):,} "
            f"| {v.get('score', 0):g} |"
        )

    themes = performance.theme_scores("dev_humor")
    if themes:
        lines += ["", "## Humor themes by engagement", ""]
        by_key = {v["topic_key"]: v.get("topic", "") for v in videos if "topic_key" in v}
        for key, score in sorted(themes.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- **{score:g}** — {by_key.get(key, key)[:90]}")

    lines += [
        "",
        "_Score = likes + 2 x comments + views/100. Theme rotation draws "
        "high scorers up to 4x more often._",
        "",
    ]
    path = performance._state_dir() / "performance_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    setup_logging()

    if not performance.load_ledger():
        # Normal state before the first post-ledger upload — NOT a failure.
        # Still verify credentials with a real API call so a bad key is caught
        # now instead of silently next Monday.
        try:
            probe = performance.fetch_stats(["jNQXAC9IVRw"])  # "Me at the zoo": public forever
            assert probe, "API returned no data for a known public video"
        except Exception as e:  # noqa: BLE001
            logger.error("API credential check failed: {}", e)
            return 1
        print("API key OK. Ledger empty — stats will flow once daily runs publish videos.")
        return 0

    try:
        perf = performance.collect_stats()
    except Exception as e:  # noqa: BLE001
        logger.error("stats collection failed: {}", e)
        return 1
    if not perf:
        # Ledger has videos but none returned stats (all still private/processing).
        logger.info("no public stats yet — videos may still be private")
        return 0

    report = write_report(perf)
    logger.info("report written to {}", report)

    top = max(perf.values(), key=lambda v: v.get("score", 0))
    print(f"\n{len(perf)} videos tracked. Top performer: "
          f"{top.get('title') or top.get('topic')} "
          f"({top.get('views', 0):,} views, {top.get('likes', 0):,} likes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
