"""Celery application. Broker + result backend = Redis."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import logger, setup_logging

setup_logging()

celery = Celery(
    "yt_automation",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.pipeline_tasks"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=60 * 60,        # hard 1h per task (video render can be long)
    task_soft_time_limit=55 * 60,
    worker_max_tasks_per_child=20,  # recycle workers to release GPU/RAM
    result_expires=60 * 60 * 24,
)

# Celery beat schedule = the 24/7 heartbeat.
#
# DISCOVER_AT_HOUR/MINUTE (UTC) pins discovery to a fixed daily time, which is what a
# daily news channel wants: stories are freshest in the morning, and a predictable
# upload slot matters more than raw frequency. Set DISCOVER_CRON_HOURS instead to fall
# back to a simple every-N-hours interval.
if settings.discover_daily:
    _schedule = crontab(hour=settings.discover_at_hour, minute=settings.discover_at_minute)
    _desc = f"daily at {settings.discover_at_hour:02d}:{settings.discover_at_minute:02d} UTC"
else:
    _schedule = settings.discover_cron_hours * 3600
    _desc = f"every {settings.discover_cron_hours}h"

celery.conf.beat_schedule = {
    "discover-trends": {
        "task": "pipeline.scheduled_discovery",
        "schedule": _schedule,
    },
}
logger.info("beat: discovery scheduled {}", _desc)

__all__ = ["celery"]
