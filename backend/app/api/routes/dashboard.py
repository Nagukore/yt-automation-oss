"""Dashboard summary stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Project, ProjectStatus, Topic, User
from app.db.session import get_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
def stats(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    by_status = dict(
        db.execute(select(Project.status, func.count()).group_by(Project.status)).all()
    )
    return {
        "total_projects": db.scalar(select(func.count()).select_from(Project)) or 0,
        "total_topics": db.scalar(select(func.count()).select_from(Topic)) or 0,
        "pending_approval": by_status.get(ProjectStatus.PENDING_APPROVAL, 0),
        "published": by_status.get(ProjectStatus.PUBLISHED, 0),
        "failed": by_status.get(ProjectStatus.FAILED, 0),
        "by_status": {s.value: c for s, c in by_status.items()},
    }
