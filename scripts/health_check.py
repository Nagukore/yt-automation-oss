"""One-shot health check for the whole stack.

Usage:  python scripts/health_check.py

Exits non-zero if anything required for 24/7 operation is down.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "
_failed = False


def report(status: str, name: str, detail: str = "") -> None:
    global _failed
    if status == FAIL:
        _failed = True
    print(f"[{status}] {name:<22} {detail}")


def check_redis() -> None:
    try:
        import redis

        from app.core.config import settings

        r = redis.from_url(settings.redis_url, socket_connect_timeout=5)
        r.ping()
        report(OK, "Redis", f"{settings.redis_url} (v{r.info()['redis_version']})")
    except Exception as e:  # noqa: BLE001
        report(FAIL, "Redis", f"{type(e).__name__}: {str(e)[:70]} -> docker start yt-redis")


def check_worker() -> None:
    try:
        from app.core.celery_app import celery

        workers = (celery.control.inspect(timeout=8).ping() or {}).keys()
        if workers:
            report(OK, "Celery worker", ", ".join(workers))
        else:
            report(FAIL, "Celery worker", "no workers -> start with --pool=solo")
    except Exception as e:  # noqa: BLE001
        report(FAIL, "Celery worker", f"{type(e).__name__}: {str(e)[:70]}")


def check_beat() -> None:
    """Beat leaves no direct signal; report the configured schedule instead."""
    try:
        from app.core.celery_app import celery
        from app.core.config import settings

        sched = celery.conf.beat_schedule or {}
        if sched:
            report(
                OK,
                "Beat schedule",
                f"{len(sched)} job(s), discovery every {settings.discover_cron_hours}h "
                "(confirm a beat process is running)",
            )
        else:
            report(WARN, "Beat schedule", "no periodic jobs configured")
    except Exception as e:  # noqa: BLE001
        report(FAIL, "Beat schedule", str(e)[:70])


def check_db() -> None:
    try:
        from sqlalchemy import func, select

        from app.db.models import Project, ProjectStatus
        from app.db.session import session_scope

        with session_scope() as db:
            rows = db.execute(
                select(Project.status, func.count()).group_by(Project.status)
            ).all()
        counts = {s.value if hasattr(s, "value") else str(s): n for s, n in rows}
        pending = counts.get(ProjectStatus.PENDING_APPROVAL.value, 0)
        report(OK, "Database", f"{sum(counts.values())} projects {counts}")
        if pending:
            report(WARN, "Awaiting approval", f"{pending} project(s) need review")
    except Exception as e:  # noqa: BLE001
        report(FAIL, "Database", f"{type(e).__name__}: {str(e)[:70]}")


def check_llm() -> None:
    try:
        from app.core.config import settings

        if not settings.openrouter_api_key:
            report(FAIL, "LLM (OpenRouter)", "OPENROUTER_API_KEY is empty")
            return
        report(
            OK,
            "LLM (OpenRouter)",
            f"{len(settings.llm_model_chain)} models "
            f"(primary: {settings.llm_model_chain[0]})",
        )
    except Exception as e:  # noqa: BLE001
        report(FAIL, "LLM (OpenRouter)", str(e)[:70])


def check_youtube() -> None:
    try:
        from app.core.config import settings
        from app.services import youtube

        if not Path(settings.youtube_client_secrets_file).exists():
            report(WARN, "YouTube OAuth", "no client secrets -> uploads disabled")
            return
        if youtube.is_authorized():
            import json

            data = json.loads(Path(settings.youtube_token_file).read_text(encoding="utf-8"))
            has_refresh = bool(data.get("refresh_token"))
            report(
                OK if has_refresh else WARN,
                "YouTube OAuth",
                "authorized" + ("" if has_refresh else " but NO refresh token"),
            )
        else:
            report(WARN, "YouTube OAuth", "not authorized -> python scripts/youtube_auth.py")
    except Exception as e:  # noqa: BLE001
        report(WARN, "YouTube OAuth", str(e)[:70])


def check_ffmpeg() -> None:
    try:
        from app.media.video import _ffmpeg_exe

        exe = _ffmpeg_exe()
        report(OK if exe else FAIL, "FFmpeg", Path(exe).name if exe else "not found")
    except Exception as e:  # noqa: BLE001
        report(FAIL, "FFmpeg", str(e)[:70])


def main() -> int:
    print("\nAI YouTube Automation - health check\n" + "-" * 60)
    for check in (
        check_redis,
        check_worker,
        check_beat,
        check_db,
        check_llm,
        check_youtube,
        check_ffmpeg,
    ):
        check()
    print("-" * 60)
    print("NOT healthy - see FAIL lines above.\n" if _failed else "All required services OK.\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
