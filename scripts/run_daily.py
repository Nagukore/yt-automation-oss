"""One-shot daily run for CI (GitHub Actions). No Celery, no Redis, no server.

    discover AI news -> generate video(s) -> upload to YouTube as PRIVATE

The approval gate still exists: uploads are PRIVATE, so you review them in YouTube
Studio and flip to Public yourself. Nothing becomes visible without a human.

Usage:
    python scripts/run_daily.py            # uses MAX_TOPICS_PER_RUN from env
    python scripts/run_daily.py --count 1
    python scripts/run_daily.py --dry-run  # generate but do not upload

Exit codes: 0 = at least one video published, 1 = nothing produced.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.core.logging import logger, setup_logging  # noqa: E402
from app.db import crud  # noqa: E402
from app.db.models import Project, ProjectStatus, VideoFormat  # noqa: E402
from app.db.session import session_scope  # noqa: E402


def _ensure_schema() -> None:
    """Create tables if missing. CI may point at a fresh database."""
    from app.db.models import Base
    from app.db.session import get_engine

    Base.metadata.create_all(bind=get_engine())


SEEN_LIMIT = 500  # keep the file small; old stories fall out of the feeds anyway


def seen_file(content_type: str) -> Path:
    """Separate dedupe history per content type so news and humor don't collide."""
    name = "seen.json" if content_type == "news" else f"seen_{content_type}.json"
    return ROOT / "state" / name


def _seen_key(title: str) -> str:
    """Stable fingerprint of a story, tolerant of small headline edits."""
    import hashlib
    import re

    words = sorted(set(re.findall(r"[a-z0-9]{4,}", title.lower())))
    return hashlib.sha1(" ".join(words).encode()).hexdigest()[:16]


def load_seen(content_type: str) -> list[str]:
    """Story/theme fingerprints already covered.

    Deliberately file-based rather than database-based: CI runners are ephemeral,
    and a committed JSON file gives durable dedupe with no database, no connection
    string and no credentials to manage.
    """
    import json

    path = seen_file(content_type)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("seen", [])
    except (ValueError, OSError) as e:
        logger.warning("could not read {}: {} — treating as empty", path.name, e)
        return []


def save_seen(content_type: str, keys: list[str]) -> None:
    import json

    path = seen_file(content_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen": keys[-SEEN_LIMIT:]}, indent=1), encoding="utf-8")


def pick_topics(count: int) -> list[dict]:
    """Discover fresh stories and reserve unused ones as projects.

    Dedupe lives in the database (Topic.used), so a persistent DATABASE_URL is what
    stops the channel re-covering the same story. With an ephemeral SQLite file the
    run still works, but every day starts with no memory of what was already posted.
    """
    from app.services.trends import discover_topics

    found = discover_topics(limit=max(count * 5, 15))
    logger.info("discovered {} candidate stories", len(found))

    seen = load_seen("news")
    seen_set = set(seen)
    fresh_stories = [t for t in found if _seen_key(t["title"]) not in seen_set]
    logger.info(
        "{} already covered, {} new", len(found) - len(fresh_stories), len(fresh_stories)
    )
    if not fresh_stories:
        return []
    found = fresh_stories

    created: list[dict] = []
    with session_scope() as db:
        # Only consider topics from THIS discovery run. crud.unused_topics() ranks by
        # score across the whole table, so stale rows from other providers (Reddit TIL
        # scores ~10 vs AI news ~3) would outrank today's news forever and the channel
        # would drift off-topic.
        fresh = [
            crud.upsert_topic(
                db, item["title"], item["source"], item["score"], item.get("keywords", [])
            )
            for item in found
        ]
        db.flush()

        candidates = sorted(
            (t for t in fresh if not t.used), key=lambda t: t.score, reverse=True
        )
        if not candidates:
            logger.warning("all {} discovered stories were already covered", len(fresh))

        for topic in candidates[:count]:
            project = crud.create_project(
                db, title=topic.title, topic_id=topic.id, video_format=VideoFormat.SHORT
            )
            topic.used = True
            db.flush()
            created.append({"project_id": project.id, "title": topic.title})

    # Record before generating: if a render crashes we still won't retry the same
    # story tomorrow, which is preferable to looping on one broken headline.
    if created:
        save_seen("news", seen + [_seen_key(c["title"]) for c in created])
        logger.info("recorded {} stories", len(created))
    return created


def pick_humor_themes(count: int) -> list[dict]:
    """Pick dev-humor themes, preferring ones not used recently.

    Themes are a fixed curated list and are intentionally reusable — the model
    writes a fresh joke each run (temperature 0.95) — but we still rotate so the
    same metaphor doesn't appear two days running. When all themes have been used
    the history resets and rotation starts over.
    """
    from app.pipeline.prompts import DEV_HUMOR_THEMES

    from app.services.performance import theme_weights, weighted_sample

    recent = load_seen("dev_humor")
    recent_set = set(recent)
    available = [t for t in DEV_HUMOR_THEMES if _seen_key(t) not in recent_set]
    if len(available) < count:
        logger.info("all humor themes cycled — resetting rotation")
        recent, recent_set, available = [], set(), list(DEV_HUMOR_THEMES)

    # Feedback loop: themes whose past videos measurably earned likes/comments get
    # up to 4x the draw odds; untested themes keep base weight so we still explore.
    chosen = weighted_sample(available, theme_weights(available), min(count, len(available)))

    created: list[dict] = []
    with session_scope() as db:
        for theme in chosen:
            project = crud.create_project(
                db, title=theme, topic_id=None, video_format=VideoFormat.SHORT
            )
            db.flush()
            created.append({"project_id": project.id, "title": theme})

    if created:
        save_seen("dev_humor", recent + [_seen_key(c["title"]) for c in created])
        logger.info("selected {} humor theme(s)", len(created))
    return created


def generate(project_id: int, title: str, content_type: str) -> bool:
    from app.pipeline.graph import run_pipeline

    started = time.perf_counter()
    try:
        run_pipeline(project_id, title, "short", content_type=content_type)
        logger.info("[{}] generated in {:.0f}s", project_id, time.perf_counter() - started)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("[{}] generation failed: {}", project_id, e)
        traceback.print_exc()
        return False


def publish(project_id: int, privacy: str) -> str | None:
    from app.services import youtube

    if not youtube.is_authorized():
        logger.error("YouTube not authorized — is the YOUTUBE_TOKEN_JSON secret set?")
        return None

    with session_scope() as db:
        p = db.get(Project, project_id)
        if not p or not p.final_video_path:
            logger.error("[{}] no rendered video to upload", project_id)
            return None
        payload = {
            "video_path": p.final_video_path,
            "title": p.title,
            "description": p.description or p.title,
            "tags": list(p.hashtags or []),
            "thumbnail_path": p.thumbnail_path,
        }

    try:
        video_id = youtube.upload_video(privacy=privacy, **payload)
    except Exception as e:  # noqa: BLE001
        logger.error("[{}] upload failed: {}", project_id, e)
        if "quotaExceeded" in str(e):
            logger.error("daily YouTube quota exhausted (~6 uploads/day)")
        return None

    with session_scope() as db:
        p = db.get(Project, project_id)
        if p:
            p.youtube_video_id = video_id
            p.status = ProjectStatus.PUBLISHED
            p.published_at = datetime.now(timezone.utc)
    logger.info("[{}] published https://youtu.be/{}", project_id, video_id)
    return video_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=settings.max_topics_per_run)
    parser.add_argument("--privacy", default=settings.youtube_upload_privacy)
    parser.add_argument(
        "--content-type",
        default="news",
        choices=["news", "dev_humor"],
        help="news = AI news from feeds; dev_humor = developer comedy from themes",
    )
    parser.add_argument("--dry-run", action="store_true", help="generate but do not upload")
    args = parser.parse_args()

    setup_logging()
    logger.info(
        "daily run: type={} count={} privacy={}", args.content_type, args.count, args.privacy
    )
    _ensure_schema()

    if args.content_type == "dev_humor":
        topics = pick_humor_themes(args.count)
    else:
        topics = pick_topics(args.count)
    if not topics:
        logger.error("no topics to generate (all recently covered)")
        return 1

    published: list[str] = []
    for item in topics:
        pid, title = item["project_id"], item["title"]
        logger.info("--- project {}: {}", pid, title)
        if not generate(pid, title, args.content_type):
            continue
        if args.dry_run:
            logger.info("[{}] dry-run: skipping upload", pid)
            published.append(f"(dry-run) {title}")
            continue
        if video_id := publish(pid, args.privacy):
            published.append(f"https://youtu.be/{video_id}  {title}")
            # Ledger feeds the weekly stats collector (scripts/collect_stats.py);
            # without it, ephemeral CI runners forget what was ever published.
            from app.services.performance import record_published

            record_published(video_id, args.content_type, title)

    print("\n" + "=" * 60)
    if published:
        print(f"{len(published)}/{len(topics)} published as {args.privacy}:")
        for line in published:
            print("  " + line)
        print("\nReview in YouTube Studio, then switch to Public.")
    else:
        print("Nothing published.")
    print("=" * 60)
    return 0 if published else 1


if __name__ == "__main__":
    raise SystemExit(main())
