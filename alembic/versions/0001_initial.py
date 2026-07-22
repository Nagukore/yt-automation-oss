"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

video_format = sa.Enum("SHORT", "LONG", name="videoformat")
project_status = sa.Enum(
    "DISCOVERED", "RESEARCHING", "SCRIPTING", "GENERATING_MEDIA", "RENDERING",
    "PENDING_APPROVAL", "APPROVED", "REJECTED", "PUBLISHING", "PUBLISHED", "FAILED",
    name="projectstatus",
)
asset_type = sa.Enum("IMAGE", "AUDIO", "SUBTITLE", "VIDEO", "THUMBNAIL", name="assettype")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("is_admin", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "topics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source", sa.String(100)),
        sa.Column("score", sa.Float),
        sa.Column("keywords", sa.JSON),
        sa.Column("used", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_topics_used", "topics", ["used"])

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("topic_id", sa.Integer, sa.ForeignKey("topics.id")),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("video_format", video_format, nullable=False),
        sa.Column("status", project_status, nullable=False),
        sa.Column("research", sa.Text),
        sa.Column("script", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("hashtags", sa.JSON),
        sa.Column("thumbnail_prompts", sa.JSON),
        sa.Column("final_video_path", sa.String(1000)),
        sa.Column("thumbnail_path", sa.String(1000)),
        sa.Column("youtube_video_id", sa.String(100)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text),
        sa.Column("approved_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_projects_status", "projects", ["status"])

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("asset_type", asset_type, nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("order_index", sa.Integer, default=0),
        sa.Column("meta", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_assets_project_id", "assets", ["project_id"])

    op.create_table(
        "pipeline_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("level", sa.String(20), default="info"),
        sa.Column("message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pipeline_logs_project_id", "pipeline_logs", ["project_id"])


def downgrade() -> None:
    op.drop_table("pipeline_logs")
    op.drop_table("assets")
    op.drop_table("projects")
    op.drop_table("topics")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    asset_type.drop(op.get_bind(), checkfirst=True)
    project_status.drop(op.get_bind(), checkfirst=True)
    video_format.drop(op.get_bind(), checkfirst=True)
