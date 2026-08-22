"""FastAPI application entrypoint. Swagger UI at /docs."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import approval, auth, dashboard, projects, publish, topics
from app.bootstrap import create_tables, ensure_admin
from app.core.config import settings
from app.core.logging import logger, setup_logging
from app.core.tracing import setup_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    setup_tracing()
    logger.info("starting AI YouTube Automation API ({} env)", settings.app_env)
    if settings.app_env != "production":
        create_tables()  # in prod, run alembic migrations instead
    try:
        ensure_admin()
    except Exception as e:  # noqa: BLE001
        logger.warning("admin bootstrap skipped: {}", e)
    yield
    logger.info("shutting down API")


app = FastAPI(
    title="AI YouTube Automation",
    description="Discover → research → script → media → render → **human approval** → publish.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(topics.router)
app.include_router(projects.router)
app.include_router(approval.router)
app.include_router(publish.router)
app.include_router(dashboard.router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "env": settings.app_env}
