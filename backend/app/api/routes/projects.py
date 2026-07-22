"""Project routes: create/generate, list, detail, retry, serve media."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.db import crud
from app.db.models import Project, ProjectStatus, User
from app.db.session import get_db
from app.schemas.models import (
    CreateProjectRequest,
    MessageOut,
    ProjectDetail,
    ProjectOut,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(
    status: ProjectStatus | None = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Project).order_by(Project.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Project.status == status)
    return list(db.scalars(stmt))


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    req: CreateProjectRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Create a project and kick off the full generation pipeline."""
    from app.tasks.pipeline_tasks import generate_project

    project = crud.create_project(
        db, title=req.topic, topic_id=req.topic_id, video_format=req.video_format
    )
    db.commit()
    generate_project.delay(project.id)
    return project


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.assets), selectinload(Project.logs))
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/{project_id}/retry", response_model=MessageOut)
def retry_project(project_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    from app.tasks.pipeline_tasks import generate_project

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.status = ProjectStatus.DISCOVERED
    project.error = None
    db.commit()
    generate_project.delay(project.id)
    return MessageOut(detail="pipeline restarted")


@router.delete("/{project_id}", response_model=MessageOut)
def delete_project(project_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    project = db.get(Project, project_id)
    if project:
        db.delete(project)
        db.commit()
    return MessageOut(detail="deleted")


@router.get("/{project_id}/media/{asset_id}")
def get_asset_file(
    project_id: int,
    asset_id: int,
    token: str | None = Query(None, description="JWT (for <img>/<video> tags that can't set headers)"),
    db: Session = Depends(get_db),
):
    """Stream an asset file (image/audio/video) to the dashboard for preview.

    Auth via the standard bearer header OR a `?token=` query param, since browser
    media tags cannot attach Authorization headers.
    """
    import jwt as _jwt

    from app.core.security import decode_access_token
    from app.db.models import Asset

    if not token:
        raise HTTPException(status_code=401, detail="token required")
    try:
        decode_access_token(token)
    except _jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail="invalid token") from e

    asset = db.get(Asset, asset_id)
    if not asset or asset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    path = Path(asset.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(str(path))
