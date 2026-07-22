"""Topic discovery routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import crud
from app.db.models import Topic, User
from app.db.session import get_db
from app.schemas.models import DiscoverRequest, MessageOut, TopicOut
from app.services.trends import discover_topics

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.get("", response_model=list[TopicOut])
def list_topics(
    only_unused: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Topic).order_by(Topic.created_at.desc()).limit(limit)
    if only_unused:
        stmt = stmt.where(Topic.used.is_(False))
    return list(db.scalars(stmt))


@router.post("/discover", response_model=list[TopicOut])
def run_discovery(
    req: DiscoverRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Fetch trending topics now and store them. (Also runs on a schedule.)"""
    found = discover_topics(limit=req.limit)
    saved = [
        crud.upsert_topic(db, t["title"], t["source"], t["score"], t.get("keywords", []))
        for t in found
    ]
    db.commit()

    if req.auto_generate:
        from app.tasks.pipeline_tasks import generate_project

        for topic in saved[: min(3, len(saved))]:
            project = crud.create_project(
                db, title=topic.title, topic_id=topic.id, video_format=req.video_format
            )
            topic.used = True
            db.commit()
            generate_project.delay(project.id)

    return saved


@router.delete("/{topic_id}", response_model=MessageOut)
def delete_topic(topic_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    topic = db.get(Topic, topic_id)
    if topic:
        db.delete(topic)
        db.commit()
    return MessageOut(detail="deleted")
