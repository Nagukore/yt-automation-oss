"""Celery tasks: heavy pipeline + publishing work runs off the request thread.

- generate_project: run the full LangGraph pipeline for a project.
- publish_project:   upload an APPROVED project to YouTube.
- scheduled_discovery: periodic trend discovery (+ optional auto-generate).
"""

from __future__ import annotations

from celery import shared_task

# Importing the configured Celery app has a REQUIRED side effect: it sets the
# "current app". Without it, @shared_task binds to a default, unconfigured app whose
# broker_url is None, so Celery silently falls back to amqp://localhost:5672 and every
# .delay() fails with "connection refused" — even though the worker itself is fine.
# Any module that dispatches these tasks (API routes, scheduler) must import them
# through here, so keep this import.
from app.core.celery_app import celery  # noqa: F401
from app.core.config import settings
from app.core.logging import logger
from app.db import crud
from app.db.models import Project, ProjectStatus
from app.db.session import session_scope


@shared_task(name="pipeline.generate_project", bind=True, max_retries=1)
def generate_project(self, project_id: int) -> dict:
    from app.pipeline.graph import run_pipeline

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            return {"error": "project not found"}
        topic = project.title
        video_format = project.video_format.value

    try:
        run_pipeline(project_id, topic, video_format)
        return {"project_id": project_id, "status": "pending_approval"}
    except Exception as e:  # noqa: BLE001
        logger.exception("generate_project failed: {}", e)
        return {"project_id": project_id, "error": str(e)}


@shared_task(name="pipeline.publish_project", bind=True, max_retries=2, default_retry_delay=120)
def publish_project(self, project_id: int, privacy: str | None = None) -> dict:
    from app.services import youtube

    with session_scope() as db:
        project = db.get(Project, project_id)
        if not project:
            return {"error": "project not found"}
        if project.status != ProjectStatus.APPROVED:
            return {"error": f"project is {project.status.value}, not approved"}
        if not project.final_video_path:
            return {"error": "no rendered video to publish"}

        project.status = ProjectStatus.PUBLISHING
        payload = {
            "video_path": project.final_video_path,
            "title": project.title,
            "description": project.description or project.title,
            "tags": project.hashtags or [],
            "thumbnail_path": project.thumbnail_path,
        }
        crud.log(db, project_id, "publish", "uploading to YouTube")

    try:
        video_id = youtube.upload_video(privacy=privacy, **payload)
    except Exception as e:  # noqa: BLE001
        logger.exception("publish failed: {}", e)
        with session_scope() as db:
            project = db.get(Project, project_id)
            if project:
                project.status = ProjectStatus.APPROVED  # revert so it can be retried
                project.error = str(e)[:2000]
                crud.log(db, project_id, "publish", f"FAILED: {e}", level="error")
        raise self.retry(exc=e)

    from datetime import datetime, timezone

    with session_scope() as db:
        project = db.get(Project, project_id)
        if project:
            project.status = ProjectStatus.PUBLISHED
            project.youtube_video_id = video_id
            project.published_at = datetime.now(timezone.utc)
            crud.log(db, project_id, "publish", f"published https://youtu.be/{video_id}")
    return {"project_id": project_id, "youtube_video_id": video_id}


@shared_task(name="pipeline.scheduled_discovery")
def scheduled_discovery() -> dict:
    """Discover trends, store them, and (optionally) auto-start generation."""
    from app.services.trends import discover_topics

    found = discover_topics(limit=max(10, settings.max_topics_per_run * 3))
    created = 0
    with session_scope() as db:
        saved = [
            crud.upsert_topic(db, t["title"], t["source"], t["score"], t.get("keywords", []))
            for t in found
        ]
        db.flush()

        if settings.auto_generate:
            for topic in crud.unused_topics(db, settings.max_topics_per_run):
                project = crud.create_project(
                    db, title=topic.title, topic_id=topic.id, video_format="short"
                )
                topic.used = True
                db.flush()
                generate_project.delay(project.id)
                created += 1

    logger.info("scheduled_discovery: {} topics, {} projects queued", len(found), created)
    return {"topics": len(found), "projects_queued": created}
