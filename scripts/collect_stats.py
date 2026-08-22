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


def _geography_section(geo: dict) -> list[str]:
    """Views by country, with the previous window beside it.

    The comparison column is the point of this table. A country's share moving a
    few points looks like a trend and almost never is: at this channel's volume a
    single Short reaching a different feed is worth several percent, so a share
    quoted on its own invites chasing noise. Printing the previous window and the
    absolute view counts next to it makes that obvious at a glance.
    """
    countries = geo.get("countries") or []
    if not countries:
        return []

    prev = {c["country"]: c for c in geo.get("previous_countries") or []}
    window, previous = geo.get("window", {}), geo.get("previous_window", {})
    total = sum(c["views"] for c in countries)
    prev_total = sum(c["views"] for c in (geo.get("previous_countries") or []))

    lines = [
        "## Audience by country",
        "",
        f"_{window.get('start', '?')} to {window.get('end', '?')}, {total:,} views "
        f"(previous window {previous.get('start', '?')} to "
        f"{previous.get('end', '?')}: {prev_total:,})._",
        "",
        "| Country | Views | Prev views | Change | Share | Prev share | Avg view |",
        "|---------|------:|-----------:|-------:|------:|-----------:|---------:|",
    ]
    for c in countries[:12]:
        p = prev.get(c["country"])
        avg = c.get("avg_view_seconds", 0)
        prev_views = f"{p['views']:,}" if p else "—"
        change = f"{c['views'] - p['views']:+,}" if p else "—"
        prev_share = f"{p['share']:.1f}%" if p else "—"
        lines.append(
            f"| {c['country']} | {c['views']:,} | {prev_views} | {change} "
            f"| {c['share']:.1f}% | {prev_share} | {avg // 60}:{avg % 60:02d} |"
        )

    # Absolute views are listed first, and deliberately, because share is a ratio
    # and a ratio moves when either side does. A country can lose several points of
    # share in a window where its own views did not fall at all — the rest of the
    # channel simply grew — and reading the percentage alone turns that into a
    # phantom problem to chase.
    lines += [
        "",
        "_Read the view columns before the share columns: share also moves when "
        "the channel total moves, so a share change with flat views is arithmetic, "
        "not audience loss._",
        "",
    ]
    return lines


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
    ]

    # Shorts vs long-form, side by side. Long-form costs ~30x the render time of a
    # Short and is only worth it if the watch-time revenue follows, so the averages
    # here are the number that decides whether to keep making them. Entries predating
    # the video_format field are Shorts (long-form had never published back then).
    by_format: dict[str, list[dict]] = {}
    for v in videos:
        by_format.setdefault(v.get("video_format", "short"), []).append(v)
    if len(by_format) > 1:
        lines += [
            "## By format",
            "",
            "| Format | Videos | Avg views | Avg likes | Avg score |",
            "|--------|-------:|----------:|----------:|----------:|",
        ]
        for fmt, vs in sorted(by_format.items()):
            n = len(vs)
            lines.append(
                f"| {fmt} | {n} "
                f"| {sum(v.get('views', 0) for v in vs) / n:,.0f} "
                f"| {sum(v.get('likes', 0) for v in vs) / n:,.1f} "
                f"| {sum(v.get('score', 0) for v in vs) / n:,.1f} |"
            )
        lines.append("")

    lines += _geography_section(performance.load_geography())

    lines += [
        "| # | Video | Type | Format | Views | Likes | Comments | Score |",
        "|---|-------|------|--------|------:|------:|---------:|------:|",
    ]
    for i, v in enumerate(videos[:30], 1):
        title = (v.get("title") or v.get("topic", ""))[:60].replace("|", "\\|")
        lines.append(
            f"| {i} | [{title}](https://youtu.be/{v['video_id']}) "
            f"| {v.get('content_type', '?')} | {v.get('video_format', 'short')} "
            f"| {v.get('views', 0):,} "
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
