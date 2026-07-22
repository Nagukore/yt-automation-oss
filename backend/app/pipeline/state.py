"""Shared state object passed between LangGraph nodes."""

from __future__ import annotations

from typing import TypedDict


class PipelineState(TypedDict, total=False):
    project_id: int
    topic: str
    video_format: str  # "short" | "long"
    content_type: str  # "news" (default) | "dev_humor"

    # generated text
    research: str
    script: str
    title: str
    description: str
    hashtags: list[str]
    thumbnail_prompt: str
    scene_prompts: list[str]

    # media artifacts (filesystem paths)
    image_paths: list[str]
    audio_path: str
    subtitle_path: str
    thumbnail_path: str
    video_path: str

    # control
    error: str
