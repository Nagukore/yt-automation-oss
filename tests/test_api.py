"""API tests using an in-memory SQLite DB and dependency overrides."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def client(monkeypatch):
    # Isolated in-memory DB shared across connections.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.db.models import Base, User

    Base.metadata.create_all(bind=engine)

    from app.core.security import hash_password

    with TestingSession() as db:
        db.add(
            User(
                email="admin@example.com",
                hashed_password=hash_password("pw"),
                is_admin=True,
                is_active=True,
            )
        )
        db.commit()

    # Neutralize startup side-effects (they target the real Postgres engine).
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "create_tables", lambda: None)
    monkeypatch.setattr(main_mod, "ensure_admin", lambda: None)

    from app.db.session import get_db

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    main_mod.app.dependency_overrides[get_db] = override_get_db
    with TestClient(main_mod.app) as c:
        yield c
    main_mod.app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_and_me(client):
    r = client.post("/api/auth/token", data={"username": "admin@example.com", "password": "pw"})
    assert r.status_code == 200
    token = r.json()["access_token"]

    r2 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json()["email"] == "admin@example.com"


def test_login_wrong_password(client):
    r = client.post("/api/auth/token", data={"username": "admin@example.com", "password": "nope"})
    assert r.status_code == 401


def test_projects_require_auth(client):
    assert client.get("/api/projects").status_code == 401
