"""Publishing routes: YouTube OAuth authorization + manual publish trigger."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin, require_admin_browser
from app.db.models import Project, ProjectStatus, User
from app.db.session import get_db
from app.schemas.models import MessageOut
from app.services import youtube

router = APIRouter(prefix="/api/publish", tags=["publish"])


@router.get("/status", response_model=dict)
def youtube_status(_: User = Depends(get_current_user)):
    return {"authorized": youtube.is_authorized()}


@router.get("/authorize")
def authorize(request: Request, _: User = Depends(require_admin_browser)):
    """Begin the YouTube OAuth consent flow (admin only).

    Visited directly in a browser, so it takes the JWT from `?token=` as well as
    from the Authorization header.
    """
    redirect_uri = str(request.url_for("oauth_callback"))
    try:
        auth_url, _state = youtube.get_authorization_url(redirect_uri)
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=400,
            detail="Missing client secrets JSON. Set YOUTUBE_CLIENT_SECRETS_FILE.",
        ) from e
    return RedirectResponse(auth_url)


@router.get("/oauth/callback", name="oauth_callback")
def oauth_callback(request: Request):
    redirect_uri = str(request.url_for("oauth_callback"))
    youtube.finish_authorization(redirect_uri, str(request.url))
    return {"detail": "YouTube authorized. You can close this window."}


@router.post("/{project_id}", response_model=MessageOut)
def publish_now(
    project_id: int,
    privacy: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Manually queue an APPROVED project for upload."""
    from app.tasks.pipeline_tasks import publish_project

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != ProjectStatus.APPROVED:
        raise HTTPException(
            status_code=409, detail="Only APPROVED projects can be published"
        )
    publish_project.delay(project_id, privacy)
    return MessageOut(detail="queued for upload")
