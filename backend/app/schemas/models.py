"""Pydantic request/response schemas for the API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.db.models import AssetType, ProjectStatus, VideoFormat


# --- auth ---
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    is_admin: bool
    is_active: bool


# --- topics ---
class TopicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    source: str
    score: float
    keywords: list[str] | None = None
    used: bool
    created_at: datetime


class DiscoverRequest(BaseModel):
    limit: int = 10
    auto_generate: bool = False
    video_format: VideoFormat = VideoFormat.SHORT


# --- projects ---
class CreateProjectRequest(BaseModel):
    topic: str
    video_format: VideoFormat = VideoFormat.SHORT
    topic_id: int | None = None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    asset_type: AssetType
    path: str
    order_index: int
    meta: dict | None = None


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    stage: str
    level: str
    message: str
    created_at: datetime


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    video_format: VideoFormat
    status: ProjectStatus
    description: str | None = None
    hashtags: list[str] | None = None
    thumbnail_prompts: list[str] | None = None
    final_video_path: str | None = None
    thumbnail_path: str | None = None
    youtube_video_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    research: str | None = None
    script: str | None = None
    assets: list[AssetOut] = []
    logs: list[LogOut] = []


class ApprovalRequest(BaseModel):
    approve: bool
    publish_now: bool = True
    privacy: str | None = None  # override default upload privacy


class MessageOut(BaseModel):
    detail: str
