"""SQLAlchemy ORM models — the single source of truth for the schema."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class VideoFormat(str, enum.Enum):
    SHORT = "short"
    LONG = "long"


class ProjectStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    RESEARCHING = "researching"
    SCRIPTING = "scripting"
    GENERATING_MEDIA = "generating_media"
    RENDERING = "rendering"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class AssetType(str, enum.Enum):
    IMAGE = "image"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    VIDEO = "video"
    THUMBNAIL = "thumbnail"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="google_trends")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    keywords: Mapped[list | None] = mapped_column(JSON, default=list)
    used: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    projects: Mapped[list["Project"]] = relationship(back_populates="topic")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    video_format: Mapped[VideoFormat] = mapped_column(Enum(VideoFormat), default=VideoFormat.SHORT)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.DISCOVERED, index=True
    )

    # generated text
    research: Mapped[str | None] = mapped_column(Text)
    script: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list | None] = mapped_column(JSON, default=list)
    thumbnail_prompts: Mapped[list | None] = mapped_column(JSON, default=list)

    # rendered artifact
    final_video_path: Mapped[str | None] = mapped_column(String(1000))
    thumbnail_path: Mapped[str | None] = mapped_column(String(1000))

    # publishing
    youtube_video_id: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # bookkeeping
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    topic: Mapped[Topic | None] = relationship(back_populates="projects")
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    logs: Mapped[list["PipelineLog"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    asset_type: Mapped[AssetType] = mapped_column(Enum(AssetType), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="assets")


class PipelineLog(Base):
    __tablename__ = "pipeline_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    level: Mapped[str] = mapped_column(String(20), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="logs")
