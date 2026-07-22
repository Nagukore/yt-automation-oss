"""One-time startup helpers: create tables (dev) and the first admin user."""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import logger
from app.core.security import hash_password
from app.db import crud
from app.db.models import Base, User
from app.db.session import get_engine, get_sessionmaker


def create_tables() -> None:
    """Convenience for local/dev runs. In production prefer `alembic upgrade head`."""
    Base.metadata.create_all(bind=get_engine())


def ensure_admin() -> None:
    with get_sessionmaker()() as db:
        if crud.get_user_by_email(db, settings.first_admin_email):
            return
        admin = User(
            email=settings.first_admin_email,
            hashed_password=hash_password(settings.first_admin_password),
            is_admin=True,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        logger.info("bootstrapped admin user: {}", settings.first_admin_email)
