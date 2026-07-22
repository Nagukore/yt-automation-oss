"""Shared FastAPI dependencies: DB session + current authenticated user."""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db import crud
from app.db.models import User
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        email = payload.get("sub")
        if not email:
            raise cred_exc
    except jwt.PyJWTError as e:
        raise cred_exc from e

    user = crud.get_user_by_email(db, email)
    if user is None or not user.is_active:
        raise cred_exc
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


def require_admin_browser(
    request: Request,
    token: str | None = Query(
        default=None,
        description="JWT access token. Needed because a browser navigation cannot "
        "send an Authorization header.",
    ),
    db: Session = Depends(get_db),
) -> User:
    """Admin auth for endpoints a *browser* navigates to directly.

    The OAuth consent flow is entered by following a link, and a plain navigation
    carries no Authorization header — so header-only auth returns 401 before the
    redirect can happen. Accept the token from the `?token=` query string as well,
    falling back to the header when present (e.g. calls from the dashboard).
    """
    raw = token
    if not raw:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            raw = header[7:]
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Append ?token=<your JWT> to this URL.",
        )

    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials"
    )
    try:
        email = decode_access_token(raw).get("sub")
    except jwt.PyJWTError as e:
        raise cred_exc from e
    if not email:
        raise cred_exc

    user = crud.get_user_by_email(db, email)
    if user is None or not user.is_active:
        raise cred_exc
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user
