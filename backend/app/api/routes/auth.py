"""Authentication routes: JWT login + current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import create_access_token, verify_password
from app.db import crud
from app.db.models import User
from app.db.session import get_db
from app.schemas.models import Token, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password flow. `username` field = email."""
    user = crud.get_user_by_email(db, form.username)
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is disabled")
    return Token(access_token=create_access_token(subject=user.email))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
