"""LangGraph state machine + a runner that persists progress to PostgreSQL.

Flow:
    research → script → metadata → thumbnail_prompts
             → images → voiceover → subtitles → video → END

The graph itself is pure (operates on PipelineState). `run_pipeline` executes it,
writing status + assets + logs to the DB after each stage so the dashboard shows
live progress and the work is resumable/inspectable.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.core.logging import logger
from app.db import crud
from app.db.models import AssetType, Project, ProjectStatus
from app.db.session import session_scope
from app.pipeline.nodes import media_nodes, text_nodes
from app.pipeline.state import PipelineState


def build_graph():
    g = StateGraph(PipelineState)

    g.add_node("research", text_nodes.research_node)
    g.add_node("script", text_nodes.script_node)
    g.add_node("metadata", text_nodes.metadata_node)
    g.add_node("thumbnail_prompts", text_nodes.thumbnail_prompt_node)
    g.add_node("images", media_nodes.images_node)
    g.add_node("voiceover", media_nodes.voiceover_node)
    g.add_node("subtitles", media_nodes.subtitles_node)
    g.add_node("video", media_nodes.video_node)

    g.set_entry_point("research")
    g.add_edge("research", "script")
    g.add_edge("script", "metadata")
    g.add_edge("metadata", "thumbnail_prompts")
    g.add_edge("thumbnail_prompts", "images")
    g.add_edge("images", "voiceover")
    g.add_edge("voiceover", "subtitles")
    g.add_edge("subtitles", "video")
    g.add_edge("video", END)
    return g.compile()


# Map each node to the DB status it represents, for live progress reporting.
_STAGE_STATUS = {
    "research": ProjectStatus.RESEARCHING,
    "script": ProjectStatus.SCRIPTING,
    "metadata": ProjectStatus.SCRIPTING,
    "thumbnail_prompts": ProjectStatus.SCRIPTING,
    "images": ProjectStatus.GENERATING_MEDIA,
    "voiceover": ProjectStatus.GENERATING_MEDIA,
    "subtitles": ProjectStatus.GENERATING_MEDIA,
    "video": ProjectStatus.RENDERING,
}


def _persist_stage(project_id: int, stage: str, state: PipelineState) -> None:
    """Write whatever this stage produced back to the DB."""
    with session_scope() as db:
        project = db.get(Project, project_id)
        if project is None:
            return
        if stage in _STAGE_STATUS:
            project.status = _STAGE_STATUS[stage]

        # text
        for field in ("research", "script", "description", "title"):
            if field in state and getattr(project, field, None) != state[field]:
                if field == "title" and state.get("title"):
                    project.title = state["title"]
                elif field != "title":
                    setattr(project, field, state[field])
        if "hashtags" in state:
            project.hashtags = state["hashtags"]
        if "thumbnail_prompt" in state or "scene_prompts" in state:
            project.thumbnail_prompts = [state.get("thumbnail_prompt", "")] + state.get(
                "scene_prompts", []
            )

        # assets
        if "image_paths" in state:
            for i, p in enumerate(state["image_paths"]):
                crud.add_asset(db, project_id, AssetType.IMAGE, p, order_index=i)
        if "thumbnail_path" in state:
            project.thumbnail_path = state["thumbnail_path"]
            crud.add_asset(db, project_id, AssetType.THUMBNAIL, state["thumbnail_path"])
        if "audio_path" in state:
            crud.add_asset(db, project_id, AssetType.AUDIO, state["audio_path"])
        if "subtitle_path" in state:
            crud.add_asset(db, project_id, AssetType.SUBTITLE, state["subtitle_path"])
        if "video_path" in state:
            project.final_video_path = state["video_path"]
            crud.add_asset(db, project_id, AssetType.VIDEO, state["video_path"])

        crud.log(db, project_id, stage, f"completed {stage}")


def run_pipeline(
    project_id: int,
    topic: str,
    video_format: str,
    content_type: str = "news",
    topic_source: str = "",
) -> PipelineState:
    """Execute the full graph for a project, streaming progress to the DB."""
    graph = build_graph()
    state: PipelineState = {
        "project_id": project_id,
        "topic": topic,
        "video_format": video_format,
        "content_type": content_type,
        "topic_source": topic_source,
    }
    logger.info(
        "[{}] pipeline start type={} topic='{}' format={}",
        project_id,
        content_type,
        topic,
        video_format,
    )

    try:
        # stream() yields {node_name: returned_partial_state} after each node runs.
        for event in graph.stream(state):
            for node_name, partial in event.items():
                if not isinstance(partial, dict):
                    continue
                state.update(partial)
                _persist_stage(project_id, node_name, partial)
                logger.info("[{}] stage '{}' persisted", project_id, node_name)

        with session_scope() as db:
            project = db.get(Project, project_id)
            if project:
                project.status = ProjectStatus.PENDING_APPROVAL
                crud.log(db, project_id, "pipeline", "ready for human approval")
        logger.info("[{}] pipeline complete -> PENDING_APPROVAL", project_id)
        return state

    except Exception as e:  # noqa: BLE001
        logger.exception("[{}] pipeline failed: {}", project_id, e)
        with session_scope() as db:
            project = db.get(Project, project_id)
            if project:
                project.status = ProjectStatus.FAILED
                project.error = str(e)[:2000]
                crud.log(db, project_id, "pipeline", f"FAILED: {e}", level="error")
        raise
