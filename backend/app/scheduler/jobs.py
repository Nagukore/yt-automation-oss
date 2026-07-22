"""Standalone APScheduler runner (alternative to Celery beat).

Run with:  python -m app.scheduler.jobs
Useful if you prefer a single scheduler process over `celery beat`. It enqueues
the same Celery task, so workers still do the heavy lifting.
"""

from __future__ import annotations

import signal
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logging import logger, setup_logging


def _run_discovery() -> None:
    from app.tasks.pipeline_tasks import scheduled_discovery

    logger.info("APScheduler: firing scheduled_discovery")
    scheduled_discovery.delay()


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_discovery,
        trigger=IntervalTrigger(hours=settings.discover_cron_hours),
        id="discover_trends",
        replace_existing=True,
        next_run_time=None,  # first run after one interval; call manually to run now
    )
    return scheduler


def main() -> None:
    setup_logging()
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("APScheduler started (discovery every {}h). Ctrl+C to stop.", settings.discover_cron_hours)

    stop = {"flag": False}

    def _handle(signum, frame):  # noqa: ANN001, ARG001
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    try:
        while not stop["flag"]:
            time.sleep(1)
    finally:
        scheduler.shutdown()
        logger.info("APScheduler stopped")


if __name__ == "__main__":
    main()
