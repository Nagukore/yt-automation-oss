"""LangGraph nodes that produce the *text* artifacts of a project.

Each node takes the shared PipelineState, does one job, and returns the keys it
updated. Persistence to the DB happens in the graph runner (graph.py) so nodes
stay pure and testable.

Output from free models is treated as untrusted: it gets sanitized here, because
anything that slips through the script node is narrated aloud in the final video.
"""

from __future__ import annotations

import re

from app.core.config import settings
from app.core.logging import logger
from app.pipeline import prompts
from app.pipeline.llm import get_llm
from app.pipeline.state import PipelineState

# Narration pace used to size scene counts (~150 wpm is a natural read).
WORDS_PER_MINUTE = 150
# One image per ~8s keeps visuals moving without excessive generation cost.
SECONDS_PER_SCENE = 8

# YouTube hard limits.
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 4900  # true limit is 5000; leave room for appended hashtags


def _clean_text(text: str) -> str:
    """Strip chat-model preamble, fences and markdown from narration text.

    Free models routinely ignore "output only the narration" and prefix replies with
    "Sure, here's the script:" or wrap them in code fences. Left in, that text ends
    up in the voiceover.
    """
    text = (text or "").strip()

    fence = re.search(r"```(?:\w+)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    lines = text.splitlines()
    if lines and re.match(
        r"^\s*(here'?s?|sure|okay|certainly|of course|below)\b.*:\s*$", lines[0], re.IGNORECASE
    ):
        lines = lines[1:]
    text = "\n".join(lines).strip()

    if len(text) > 1 and text[0] in "\"'" and text[-1] == text[0]:
        text = text[1:-1].strip()

    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # headers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)  # bold/italic
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)  # bullets
    return text.strip()


def _clean_hashtags(raw) -> list[str]:
    """Normalize to bare, deduped, alphanumeric tags YouTube will accept."""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for tag in raw:
        tag = re.sub(r"[^0-9A-Za-z_]", "", str(tag).lstrip("#"))
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:15]


def _as_dict(data, node: str) -> dict:
    """chat_json may return a list if the model wraps its object; unwrap or fail loudly."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    raise ValueError(f"{node}: expected a JSON object, got {type(data).__name__}")


# Temperature spread per candidate draft: identical temperatures produce
# near-identical drafts, which makes best-of-N pointless. Comedy runs hotter
# than news across the board.
_CANDIDATE_TEMPS = {
    "news": (0.7, 0.85, 1.0),
    "dev_humor": (0.85, 0.95, 1.05),
}


def _generate_candidates(
    build_prompt_args: dict, content_type: str, project_id
) -> list[str]:
    """Generate up to `script_candidates` script drafts at varied temperatures.

    Individual draft failures are tolerated (free models flake); only zero
    usable drafts is fatal.
    """
    n = max(1, settings.script_candidates)
    temps = _CANDIDATE_TEMPS.get(content_type, _CANDIDATE_TEMPS["news"])
    candidates: list[str] = []
    for i in range(n):
        try:
            raw = get_llm().chat(temperature=temps[i % len(temps)], **build_prompt_args)
        except Exception as e:  # noqa: BLE001
            logger.warning("[{}] candidate {} failed: {}", project_id, i + 1, e)
            continue
        text = _clean_text(raw)
        if text and text not in candidates:
            candidates.append(text)
    if not candidates:
        raise ValueError("script node produced no usable candidates")
    return candidates


def _pick_best_script(candidates: list[str], topic: str, content_type: str, project_id) -> str:
    """Have an LLM judge rank the drafts and return the winner.

    The gate must never sink a run: any judge failure (bad JSON, out-of-range
    index, model outage) falls back to the first draft.
    """
    if len(candidates) == 1:
        return candidates[0]

    criteria = (
        prompts.HUMOR_JUDGE_CRITERIA
        if content_type == "dev_humor"
        else prompts.NEWS_JUDGE_CRITERIA
    )
    block = "\n\n".join(
        f"--- CANDIDATE {i + 1} ---\n{c}" for i, c in enumerate(candidates)
    )
    try:
        verdict = _as_dict(
            get_llm().chat_json(
                prompts.SCRIPT_JUDGE_PROMPT.format(
                    n=len(candidates), topic=topic, criteria=criteria, candidates=block
                ),
                system=prompts.SCRIPT_JUDGE_SYSTEM,
            ),
            "script_judge",
        )
        winner = int(verdict.get("winner", 1))
        if not 1 <= winner <= len(candidates):
            raise ValueError(f"winner index {winner} out of range")
        logger.info(
            "[{}] judge picked candidate {}/{} (scores={}, reason={!r})",
            project_id,
            winner,
            len(candidates),
            verdict.get("scores"),
            verdict.get("reason"),
        )
        return candidates[winner - 1]
    except Exception as e:  # noqa: BLE001
        logger.warning("[{}] script judge failed ({}); using first draft", project_id, e)
        return candidates[0]


# --------------------------------------------------------------------------- nodes
def research_node(state: PipelineState) -> PipelineState:
    # Comedy needs no factual research; skip straight to the script.
    if state.get("content_type") == "dev_humor":
        return {"research": ""}
    brief = get_llm().chat(
        prompts.RESEARCH_PROMPT.format(
            topic=state["topic"], video_format=state.get("video_format", "short")
        ),
        system=prompts.RESEARCH_SYSTEM,
    )
    logger.info("[{}] research done ({} chars)", state.get("project_id"), len(brief))
    return {"research": brief}


def script_node(state: PipelineState) -> PipelineState:
    """Generate N script drafts at varied temperature, judge, keep the best.

    Best-of-N with an LLM judge is the pipeline's quality gate: single-shot free
    models produce very uneven drafts, and the script decides retention, likes
    and shares more than anything downstream.
    """
    project_id = state.get("project_id")
    content_type = state.get("content_type", "news")

    if content_type == "dev_humor":
        prompt_args = {
            "prompt": prompts.DEV_HUMOR_SCRIPT_PROMPT.format(topic=state["topic"]),
            "system": prompts.DEV_HUMOR_SYSTEM,
        }
    else:
        is_short = state.get("video_format", "short") == "short"
        template = prompts.SCRIPT_PROMPT_SHORT if is_short else prompts.SCRIPT_PROMPT_LONG
        prompt_args = {
            "prompt": template.format(topic=state["topic"], research=state.get("research", "")),
            "system": prompts.SCRIPT_SYSTEM,
        }

    candidates = _generate_candidates(prompt_args, content_type, project_id)
    script = _pick_best_script(candidates, state["topic"], content_type, project_id)

    words = len(script.split())
    logger.info(
        "[{}] script done ({} drafts, kept {} words, ~{:.0f}s narration)",
        project_id,
        len(candidates),
        words,
        words / WORDS_PER_MINUTE * 60,
    )
    return {"script": script}


def metadata_node(state: PipelineState) -> PipelineState:
    is_humor = state.get("content_type") == "dev_humor"
    prompt = (
        prompts.DEV_HUMOR_METADATA_PROMPT.format(script=state.get("script", ""))
        if is_humor
        else prompts.METADATA_PROMPT.format(topic=state["topic"], script=state.get("script", ""))
    )
    data = _as_dict(
        get_llm().chat_json(prompt, system=prompts.METADATA_SYSTEM),
        "metadata_node",
    )
    title = _clean_text(str(data.get("title") or state["topic"]))[:MAX_TITLE_CHARS]
    hashtags = _clean_hashtags(data.get("hashtags"))

    # Reserve room for the footer so appending it can never push us past YouTube's limit.
    footer = prompts.DEV_HUMOR_FOOTER if is_humor else prompts.DESCRIPTION_FOOTER
    body = str(data.get("description") or "").strip()[: MAX_DESCRIPTION_CHARS - len(footer)]
    description = body + footer
    logger.info("[{}] metadata: {!r}, {} tags", state.get("project_id"), title, len(hashtags))
    return {"title": title, "description": description, "hashtags": hashtags}


def thumbnail_prompt_node(state: PipelineState) -> PipelineState:
    is_short = state.get("video_format", "short") == "short"
    script = state.get("script", "")
    topic = state["topic"]
    aspect = "9:16 vertical" if is_short else "16:9 horizontal"

    # Scale scene count to actual narration length rather than a fixed number, so a
    # 6-minute long-form video doesn't sit on 8 static images.
    est_seconds = max(len(script.split()) / WORDS_PER_MINUTE * 60, 10)
    n_scenes = max(3, min(round(est_seconds / SECONDS_PER_SCENE), 8 if is_short else 30))

    # Humor uses the moody lonely-developer aesthetic instead of literal news imagery.
    image_system = (
        prompts.DEV_HUMOR_IMAGE_SYSTEM
        if state.get("content_type") == "dev_humor"
        else prompts.THUMBNAIL_SYSTEM
    )
    data = _as_dict(
        get_llm().chat_json(
            prompts.THUMBNAIL_PROMPT.format(
                video_format=state.get("video_format", "short"),
                title=state.get("title", topic),
                script=script,
                aspect=aspect,
                n_scenes=n_scenes,
            ),
            system=image_system,
        ),
        "thumbnail_prompt_node",
    )

    scenes = [str(p).strip() for p in (data.get("scene_prompts") or []) if str(p).strip()]
    if not scenes:  # guarantee image gen always has usable input
        scenes = [f"cinematic {aspect} illustration of {topic}"]
    # Models often return the wrong count; cycle to fill rather than fail the run.
    while len(scenes) < n_scenes:
        scenes.append(scenes[len(scenes) % len(scenes)])
    scenes = scenes[:n_scenes]

    thumbnail_prompt = str(data.get("thumbnail_prompt") or "").strip() or scenes[0]
    logger.info("[{}] {} scene prompts ({})", state.get("project_id"), len(scenes), aspect)
    return {"thumbnail_prompt": thumbnail_prompt, "scene_prompts": scenes}
