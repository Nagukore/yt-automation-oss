"""Human approval gate — the safety step before anything is published."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import crud
from app.db.models import Project, ProjectStatus, User
from app.db.session import get_db
from app.schemas.models import ApprovalRequest, ProjectOut

router = APIRouter(prefix="/api/approval", tags=["approval"])


@router.get("/pending", response_model=list[ProjectOut])
def pending(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    from sqlalchemy import select

    return list(
        db.scalars(
            select(Project)
            .where(Project.status == ProjectStatus.PENDING_APPROVAL)
            .order_by(Project.updated_at.desc())
        )
    )


@router.post("/{project_id}", response_model=ProjectOut)
def decide(
    project_id: int,
    req: ApprovalRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Approve or reject. Approving (with publish_now) queues the YouTube upload."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status not in (ProjectStatus.PENDING_APPROVAL, ProjectStatus.REJECTED):
        raise HTTPException(
            status_code=409, detail=f"Project is {project.status.value}, cannot decide now"
        )

    if not req.approve:
        project.status = ProjectStatus.REJECTED
        crud.log(db, project_id, "approval", f"rejected by {user.email}")
        db.commit()
        return project

    project.status = ProjectStatus.APPROVED
    project.approved_by = user.id
    crud.log(db, project_id, "approval", f"approved by {user.email}")
    db.commit()

    if req.publish_now:
        from app.tasks.pipeline_tasks import publish_project

        publish_project.delay(project.id, req.privacy)
        crud.log(db, project_id, "approval", "queued for YouTube upload")
        db.commit()

    db.refresh(project)
    return project
