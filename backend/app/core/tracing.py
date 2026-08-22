"""LangSmith tracing for the generation pipeline.

Why LangSmith specifically: the pipeline is a real `langgraph.StateGraph`, and
`langchain-core` already hard-requires the `langsmith` SDK — so this adds **no new
dependency**, and every graph node is instrumented by the framework itself the moment
the environment is set. What the framework does *not* see is the LLM client, which
talks to OpenRouter/Gemini over raw httpx rather than through a LangChain chat model;
those calls are traced explicitly with `@traceable` in `app.pipeline.llm`.

Off unless switched on. `LANGSMITH_TRACING=false` (the default) means the decorators
degrade to a plain function call and nothing leaves the machine, which keeps the
zero-infrastructure default of the project intact.

Failure policy: tracing must never break a render. The SDK already batches spans on a
background thread and swallows transport errors — a broken endpoint logs and the
traced function still returns normally — and everything here is written to preserve
that. Nothing in this module may raise into the pipeline.

Note the traces carry full prompts, scripts and node state. That is the point of a
trace, but it means the configured LangSmith project holds the same content as the
repo's private scripts; treat its API key like any other secret.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

from app.core.config import settings
from app.core.logging import logger

_configured = False


def setup_tracing() -> bool:
    """Wire the LangSmith SDK from settings. Returns whether tracing ended up on.

    Idempotent, and safe to call when tracing is disabled or misconfigured — in either
    case it just leaves tracing off rather than raising.
    """
    global _configured
    if _configured:
        return _enabled()

    _configured = True
    if not settings.langsmith_tracing:
        # Explicitly off rather than merely absent: LangChain reads these from the
        # ambient environment, and a stray LANGSMITH_TRACING=true in a shell profile
        # would otherwise start shipping prompts without anything in this repo asking.
        os.environ["LANGSMITH_TRACING"] = "false"
        return False

    if not settings.langsmith_api_key:
        logger.warning(
            "LANGSMITH_TRACING is on but LANGSMITH_API_KEY is empty — tracing disabled. "
            "Set the key or turn tracing off to silence this."
        )
        os.environ["LANGSMITH_TRACING"] = "false"
        return False

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    logger.info(
        "tracing: LangSmith on, project={} endpoint={}",
        settings.langsmith_project,
        settings.langsmith_endpoint,
    )
    return True


def _enabled() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true"


@contextmanager
def trace_run(name: str, **metadata: Any):
    """Group everything one video does under a single root trace.

    Without this each graph run is its own disconnected tree and the LLM calls made
    outside the graph (topic refinement, publishing) float free of it. The metadata is
    what makes the traces searchable later — filtering to "long-form runs that failed"
    is only possible if format and content type are attached here.

    A no-op when tracing is off, and never raises: a tracing failure must not be able
    to take down a render.
    """
    if not _enabled():
        yield
        return

    try:
        from langsmith import tracing_context  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        logger.warning("tracing: langsmith unavailable ({}); continuing untraced", e)
        yield
        return

    clean = {k: v for k, v in metadata.items() if v not in (None, "")}
    # Tags are the axis LangSmith filters on cheaply; metadata holds the detail.
    tags = [str(clean[k]) for k in ("content_type", "video_format") if k in clean]
    # No try/except around the body: `tracing_context` only sets and restores a
    # contextvar, and span delivery happens on the SDK's background thread, so a
    # tracing backend that is down or misconfigured logs there and never reaches
    # here. Wrapping the yield would only risk swallowing a real pipeline error.
    with tracing_context(metadata={"run_name": name, **clean}, tags=tags):
        yield


def tag_current_run(**metadata: Any) -> None:
    """Attach metadata to the span currently executing.

    For facts only known partway through a call — which model in the fallback chain
    finally answered, what the judge scored each draft — that would otherwise be
    invisible in the trace because they are neither the input nor the return value.

    A no-op when tracing is off or when there is no active span, and it swallows its
    own errors: annotating a trace is never worth failing a render over.
    """
    if not _enabled() or not metadata:
        return
    try:
        from langsmith.run_helpers import get_current_run_tree  # noqa: PLC0415

        run = get_current_run_tree()
        if run is not None:
            run.metadata.update({k: v for k, v in metadata.items() if v is not None})
    except Exception as e:  # noqa: BLE001
        logger.debug("tracing: could not tag current run ({})", e)


__all__ = ["setup_tracing", "tag_current_run", "trace_run"]
