"""Thin data-access helpers shared by API, tasks and pipeline."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    AssetType,
    PipelineLog,
    Project,
    ProjectStatus,
    Topic,
    User,
)


# --- users ---------------------------------------------------------------
def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


# --- topics --------------------------------------------------------------
def upsert_topic(db: Session, title: str, source: str, score: float, keywords: list) -> Topic:
    existing = db.scalar(select(Topic).where(Topic.title == title))
    if existing:
        existing.score = max(existing.score, score)
        return existing
    topic = Topic(title=title, source=source, score=score, keywords=keywords)
    db.add(topic)
    db.flush()
    return topic


def unused_topics(db: Session, limit: int) -> list[Topic]:
    return list(
        db.scalars(
            select(Topic).where(Topic.used.is_(False)).order_by(Topic.score.desc()).limit(limit)
        )
    )


# --- projects ------------------------------------------------------------
def create_project(db: Session, *, title: str, topic_id: int | None, video_format) -> Project:
    project = Project(title=title, topic_id=topic_id, video_format=video_format)
    db.add(project)
    db.flush()
    return project


def set_status(db: Session, project: Project, status: ProjectStatus, error: str | None = None) -> None:
    project.status = status
    if error is not None:
        project.error = error
    db.add(project)


def add_asset(
    db: Session,
    project_id: int,
    asset_type: AssetType,
    path: str,
    order_index: int = 0,
    meta: dict | None = None,
) -> Asset:
    asset = Asset(
        project_id=project_id,
        asset_type=asset_type,
        path=str(path),
        order_index=order_index,
        meta=meta or {},
    )
    db.add(asset)
    db.flush()
    return asset


def log(db: Session, project_id: int, stage: str, message: str, level: str = "info") -> None:
    db.add(PipelineLog(project_id=project_id, stage=stage, message=message, level=level))
