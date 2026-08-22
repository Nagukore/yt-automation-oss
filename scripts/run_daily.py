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
from app.core.tracing import setup_tracing  # noqa: E402
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


def record_seen(content_type: str, title: str) -> None:
    """Mark a topic/theme as covered — called only AFTER a successful render.

    Recording on success (rather than at selection time) means an infra failure —
    an exhausted LLM quota, a network blip — no longer burns a topic that never
    became a video; it is simply retried next run. A genuinely broken topic can
    recur, but trending feeds churn daily and the themed lists rotate, so it will
    not loop forever.
    """
    keys = load_seen(content_type)
    key = _seen_key(title)
    if key not in keys:
        keys.append(key)
        save_seen(content_type, keys)
        logger.info("recorded '{}' as covered", title[:60])


def pick_topics(count: int, video_format: VideoFormat = VideoFormat.SHORT) -> list[dict]:
    """Discover fresh stories and reserve unused ones as projects.

    Dedupe lives in the database (Topic.used), so a persistent DATABASE_URL is what
    stops the channel re-covering the same story. With an ephemeral SQLite file the
    run still works, but every day starts with no memory of what was already posted.
    """
    from app.services.trends import discover_mixed, discover_topics

    # Long-form covers AI deep-dives AND general trending news, so it draws on both
    # feeds interleaved. Shorts stay on the configured provider (ai_news) alone —
    # the daily streams are the channel's AI-news core and shouldn't drift.
    if video_format == VideoFormat.LONG:
        found = discover_mixed(limit=max(count * 5, 15))
    else:
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
                db, title=topic.title, topic_id=topic.id, video_format=video_format
            )
            topic.used = True
            db.flush()
            created.append(
                {"project_id": project.id, "title": topic.title, "source": topic.source}
            )

    # NB: seen.json is recorded on SUCCESSFUL render (see record_seen / main),
    # not here — an infra failure shouldn't burn a story that never got made.
    if created:
        logger.info("selected {} stories", len(created))
    return created


def pick_themes(content_type: str, count: int) -> list[dict]:
    """Pick themes for a theme-driven content type, preferring ones not used recently.

    Themes are a fixed curated list and are intentionally reusable — the model
    writes a fresh piece each run (high temperature) — but we still rotate so the
    same metaphor doesn't appear two days running. When all themes have been used
    the history resets and rotation starts over.
    """
    from app.pipeline.prompts import CODE_HEARTBREAK_THEMES, DEV_HUMOR_THEMES

    from app.services.performance import theme_weights, weighted_sample

    theme_list = {
        "dev_humor": DEV_HUMOR_THEMES,
        "code_heartbreak": CODE_HEARTBREAK_THEMES,
    }[content_type]

    recent = load_seen(content_type)
    recent_set = set(recent)
    available = [t for t in theme_list if _seen_key(t) not in recent_set]
    if len(available) < count:
        logger.info("all {} themes cycled — resetting rotation", content_type)
        recent, recent_set, available = [], set(), list(theme_list)

    # Feedback loop: themes whose past videos measurably earned likes/comments get
    # up to 4x the draw odds; untested themes keep base weight so we still explore.
    chosen = weighted_sample(
        available, theme_weights(available, content_type), min(count, len(available))
    )

    created: list[dict] = []
    with session_scope() as db:
        for theme in chosen:
            project = crud.create_project(
                db, title=theme, topic_id=None, video_format=VideoFormat.SHORT
            )
            db.flush()
            created.append({"project_id": project.id, "title": theme})

    # As with news: recorded on successful render (record_seen / main), not here,
    # so a failed run doesn't consume a theme from the curated rotation.
    if created:
        logger.info("selected {} {} theme(s)", len(created), content_type)
    return created


def generate(
    project_id: int,
    title: str,
    content_type: str,
    video_format: str = "short",
    topic_source: str = "",
) -> bool:
    from app.core.tracing import trace_run
    from app.pipeline.graph import run_pipeline

    started = time.perf_counter()
    # One video = one root trace. Without this the graph run, and any LLM call made
    # outside it, land as separate top-level trees and there is no single thing to
    # open when you want to know what happened to a given upload.
    with trace_run(
        f"{content_type}/{video_format}",
        project_id=project_id,
        topic=title,
        content_type=content_type,
        video_format=video_format,
        topic_source=topic_source,
    ):
        try:
            run_pipeline(
                project_id,
                title,
                video_format,
                content_type=content_type,
                topic_source=topic_source,
            )
            logger.info("[{}] generated in {:.0f}s", project_id, time.perf_counter() - started)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("[{}] generation failed: {}", project_id, e)
            traceback.print_exc()
            return False


def publish(
    project_id: int, privacy: str, content_type: str = "news", slot_index: int = 0
) -> str | None:
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

    # Scheduling only makes sense for a video meant to go public: scheduling a
    # deliberately private test upload would flip it public at the slot, which is
    # the opposite of what --privacy private asks for.
    publish_at = None
    if privacy == "public":
        publish_at = youtube.next_publish_slot(
            offset_minutes=slot_index * settings.youtube_publish_stagger_minutes
        )

    try:
        video_id = youtube.upload_video(
            privacy=privacy,
            publish_at=publish_at,
            category_id=settings.category_for(content_type),
            **payload,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[{}] upload failed: {}", project_id, e)
        if "quotaExceeded" in str(e):
            logger.error("daily YouTube quota exhausted (~6 uploads/day)")
        if "invalid_grant" in str(e):
            logger.error(EXPIRED_GRANT_HELP)
        return None

    # When scheduled, the video is uploaded now but goes live at the slot — record
    # the moment it actually reaches viewers, not the moment the runner finished.
    live_at = (
        datetime.strptime(publish_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if publish_at
        else datetime.now(timezone.utc)
    )
    with session_scope() as db:
        p = db.get(Project, project_id)
        if p:
            p.youtube_video_id = video_id
            p.status = ProjectStatus.PUBLISHED
            p.published_at = live_at
    logger.info(
        "[{}] {} https://youtu.be/{}",
        project_id,
        f"scheduled for {publish_at}" if publish_at else "published",
        video_id,
    )
    return video_id


EXPIRED_GRANT_HELP = (
    "The YouTube refresh token is dead (invalid_grant). Almost always this means the "
    "Google Cloud OAuth consent screen is still in 'Testing': Google expires those "
    "refresh tokens after 7 days. Fix it once:\n"
    "  1. console.cloud.google.com -> APIs & Services -> OAuth consent screen -> "
    "Publish app (status must read 'In production').\n"
    "  2. python scripts/youtube_auth.py   (mints a fresh secrets/youtube_token.json)\n"
    "  3. Paste that file's contents into the YOUTUBE_TOKEN_JSON repo secret."
)


def _preflight_youtube() -> None:
    """Verify the OAuth grant still refreshes before generating anything.

    Costs no API quota (see youtube.check_credentials) and turns a 9-minute
    render followed by a dead-token upload into a 2-second failure.
    """
    from app.services import youtube

    if not youtube.is_authorized():
        raise SystemExit(
            "preflight: YouTube not authorized — is the YOUTUBE_TOKEN_JSON secret set?"
        )
    try:
        youtube.check_credentials()
    except Exception as e:  # noqa: BLE001
        if "invalid_grant" in str(e):
            raise SystemExit(f"preflight: {EXPIRED_GRANT_HELP}") from e
        # Anything else (a transient network blip refreshing the token) is not
        # worth aborting a run over — the upload retries it at the end anyway.
        logger.warning("preflight: couldn't verify YouTube token ({}); continuing", e)
        return
    logger.info("preflight: YouTube token refreshes ok")


def preflight(dry_run: bool = False) -> None:
    """Fail fast on misconfiguration BEFORE spending scarce LLM/image quota.

    Two checks: the YouTube grant (skipped on a dry run, which never uploads)
    and the edge-TTS voice name. An unknown voice only surfaces at
    the voiceover stage — after minutes of LLM drafting and image generation —
    as NoAudioReceived (this bit us once with a voice that didn't exist). Listing
    voices is free and touches no LLM quota, so validating up front is cheap
    insurance. A network failure fetching the list is non-fatal; we abort only on
    a voice that is definitively not in the catalog.

    LLM reachability is deliberately NOT probed here: a live call would spend one
    of the scarce free-tier requests, and the pipeline's own failover already
    absorbs a transient outage.
    """
    if not dry_run:
        _preflight_youtube()
    if (settings.tts_provider or "").lower() != "edge":
        return
    voice = settings.edge_tts_voice
    if not voice:
        return
    try:
        import asyncio  # noqa: PLC0415
        import edge_tts  # noqa: PLC0415

        names = {v["ShortName"] for v in asyncio.run(edge_tts.list_voices())}
    except Exception as e:  # noqa: BLE001
        logger.warning("preflight: couldn't fetch edge-tts voices ({}); skipping check", e)
        return
    if voice not in names:
        raise SystemExit(
            f"preflight: EDGE_TTS_VOICE '{voice}' is not a valid edge-tts voice — "
            "run `python scripts/list_voices.py` for valid names."
        )
    logger.info("preflight: edge-tts voice '{}' ok", voice)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=settings.max_topics_per_run)
    parser.add_argument("--privacy", default=settings.youtube_upload_privacy)
    parser.add_argument(
        "--content-type",
        default="news",
        choices=["news", "dev_humor", "code_heartbreak"],
        help=(
            "news = AI news from feeds; dev_humor = developer comedy from themes; "
            "code_heartbreak = sad one-sided-love coding quotes from themes"
        ),
    )
    parser.add_argument(
        "--format",
        default="short",
        choices=["short", "long"],
        help="short = vertical Shorts; long = 16:9 4-6 minute video (news only)",
    )
    parser.add_argument("--dry-run", action="store_true", help="generate but do not upload")
    args = parser.parse_args()

    # Themed types are written as shorts (a 5-minute heartbreak quote isn't a thing).
    if args.format == "long" and args.content_type != "news":
        parser.error("--format long is only supported with --content-type news")

    setup_logging()
    # Before any LLM client or graph is built: LangChain reads the tracing environment
    # when a run starts, so configuring it later would leave the first stages untraced.
    setup_tracing()

    # Long-form draws on Gemini's free tier (when a key is configured) so it never
    # competes with Shorts for the OpenRouter daily budget. Set before get_llm() is
    # first called, so the client picks the right provider. Shorts never touch
    # Gemini — the guard is on video_format, not just key presence.
    if args.format == "long" and settings.gemini_api_key:
        settings.llm_provider = "gemini"
        logger.info("long-form run: LLM provider = gemini")
    elif args.format == "long":
        logger.info("long-form run: no GEMINI_API_KEY set — using OpenRouter")

    # Long-form's bigger prompts need a different OpenRouter chain order and a longer
    # timeout than Shorts. Scoped here rather than changed globally so the three daily
    # Shorts streams keep the exact model that has produced their 80+ videos. Applied
    # before get_llm() caches its client, same as the provider switch above.
    if args.format == "long":
        settings.llm_models = settings.llm_models_long
        settings.llm_timeout_seconds = settings.llm_timeout_seconds_long
        settings.llm_chat_budget_seconds = settings.llm_chat_budget_seconds_long
        logger.info(
            "long-form run: model chain = {} (timeout {}s, budget {}s)",
            settings.llm_model_chain,
            settings.llm_timeout_seconds,
            settings.llm_chat_budget_seconds,
        )

    logger.info(
        "daily run: type={} count={} privacy={}", args.content_type, args.count, args.privacy
    )
    preflight(args.dry_run)  # cheap config check before any LLM/image work
    _ensure_schema()

    if args.content_type in ("dev_humor", "code_heartbreak"):
        topics = pick_themes(args.content_type, args.count)
    else:
        fmt = VideoFormat.LONG if args.format == "long" else VideoFormat.SHORT
        topics = pick_topics(args.count, fmt)
    if not topics:
        logger.error("no topics to generate (all recently covered)")
        return 1

    # CI cancels the job at timeout-minutes and that kills the upload step outright,
    # so a run that overruns publishes nothing at all — even work that had already
    # rendered. Stop starting new videos once there isn't plausibly time to finish
    # one, and exit normally instead.
    budget = settings.run_budget_minutes * 60
    run_started = time.perf_counter()
    slowest = 0.0

    published: list[str] = []
    for index, item in enumerate(topics):
        pid, title = item["project_id"], item["title"]
        remaining = budget - (time.perf_counter() - run_started)
        # First video always gets its chance; later ones need room for a project at
        # least as slow as the slowest so far (floor: 12 min, a normal render).
        if index and remaining < max(slowest, 12 * 60):
            logger.warning(
                "run budget nearly spent ({:.0f}s left of {}min) — skipping the "
                "remaining {} topic(s) rather than being cancelled mid-render",
                remaining,
                settings.run_budget_minutes,
                len(topics) - index,
            )
            break
        # Themed runs (pick_themes) carry no source; they're always the tech branding.
        source = item.get("source", "")
        logger.info("--- project {}: {} [{}]", pid, title, source or "theme")
        project_started = time.perf_counter()
        ok = generate(pid, title, args.content_type, args.format, source)
        slowest = max(slowest, time.perf_counter() - project_started)
        if not ok:
            continue
        # Render succeeded — only now is it safe to mark the topic/theme covered.
        record_seen(args.content_type, title)
        if args.dry_run:
            logger.info("[{}] dry-run: skipping upload", pid)
            published.append(f"(dry-run) {title}")
            continue
        # slot_index staggers each video in this run away from the previous one, so
        # a 2-video news run doesn't drop both Shorts into the same instant.
        if video_id := publish(pid, args.privacy, args.content_type, len(published)):
            published.append(f"https://youtu.be/{video_id}  {title}")
            # Ledger feeds the weekly stats collector (scripts/collect_stats.py);
            # without it, ephemeral CI runners forget what was ever published.
            from app.services.performance import record_published

            record_published(video_id, args.content_type, title, args.format)

    print("\n" + "=" * 60)
    if published:
        slot = settings.youtube_publish_slot
        how = f"scheduled for {slot} UTC" if (slot and args.privacy == "public") else args.privacy
        print(f"{len(published)}/{len(topics)} published as {how}:")
        for line in published:
            print("  " + line)
    else:
        print("Nothing published.")
    print("=" * 60)
    return 0 if published else 1


if __name__ == "__main__":
    raise SystemExit(main())
