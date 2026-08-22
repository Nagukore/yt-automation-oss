"""LangGraph nodes that produce the *text* artifacts of a project.

Each node takes the shared PipelineState, does one job, and returns the keys it
updated. Persistence to the DB happens in the graph runner (graph.py) so nodes
stay pure and testable.

Output from free models is treated as untrusted: it gets sanitized here, because
anything that slips through the script node is narrated aloud in the final video.
"""

from __future__ import annotations

import re

from langsmith import traceable

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import tag_current_run
from app.pipeline import prompts
from app.pipeline.llm import get_llm
from app.pipeline.state import PipelineState

# Narration pace used to size scene counts (~150 wpm is a natural read).
WORDS_PER_MINUTE = 150
# Cut rhythm and scene ceilings live in settings — see SECONDS_PER_SCENE there for
# why the shorts value trades run time for visual pace.

# YouTube hard limits.
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 4900  # true limit is 5000; leave room for appended hashtags


# Typographic characters a chat model emits freely but that hurt downstream.
# Straightened here rather than in the TTS provider so the DB script, the audio and
# the caption text stay the *same string* — the caption punctuation recovery in
# edge_tts_provider matches its tokens against this text, so a divergence there
# would make every alignment bug unreproducible from the stored record.
_SPEECH_TRANSLATIONS = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "}
)
# Emoji survive _clean_text's markdown pass and get read aloud as their CLDR name
# ("fire", "rocket") in the middle of a sentence.
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")


def _clean_text(text: str) -> str:
    """Strip chat-model preamble, fences and markdown, and normalize for speech.

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

    text = text.translate(_SPEECH_TRANSLATIONS)
    # edge-tts treats three periods as a pause far more reliably than U+2026, and
    # the caption phrase-break class accepts either form.
    text = text.replace("…", "...")
    text = re.sub(r"\s*[–—]\s*", " — ", text)  # a spaced dash is read as a beat
    text = _EMOJI.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
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
# near-identical drafts, which makes best-of-N pointless. Creative types run
# hotter than news across the board.
_CANDIDATE_TEMPS = {
    "news": (0.7, 0.85, 1.0),
    "dev_humor": (0.85, 0.95, 1.05),
    "code_heartbreak": (0.9, 1.0, 1.1),
}

# Theme-driven content types share one pipeline shape: no research stage, a
# themed script prompt, and their own judge criteria, metadata prompt, image
# aesthetic and description footer. Adding a stream = new prompts + one entry
# here (news keeps the research-driven path as the default).
_THEMED_KITS: dict[str, dict] = {
    "dev_humor": {
        "system": prompts.DEV_HUMOR_SYSTEM,
        "script_prompt": prompts.DEV_HUMOR_SCRIPT_PROMPT,
        "judge_criteria": prompts.HUMOR_JUDGE_CRITERIA,
        "metadata_prompt": prompts.DEV_HUMOR_METADATA_PROMPT,
        "image_system": prompts.DEV_HUMOR_IMAGE_SYSTEM,
        "footer": prompts.DEV_HUMOR_FOOTER,
    },
    "code_heartbreak": {
        "system": prompts.CODE_HEARTBREAK_SYSTEM,
        "script_prompt": prompts.CODE_HEARTBREAK_SCRIPT_PROMPT,
        "judge_criteria": prompts.HEARTBREAK_JUDGE_CRITERIA,
        "metadata_prompt": prompts.CODE_HEARTBREAK_METADATA_PROMPT,
        "image_system": prompts.CODE_HEARTBREAK_IMAGE_SYSTEM,
        "footer": prompts.CODE_HEARTBREAK_FOOTER,
    },
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


@traceable(run_type="chain", name="script_judge")
def _pick_best_script(
    candidates: list[str], topic: str, content_type: str, project_id, video_format: str = "short"
) -> str:
    """Have an LLM judge rank the drafts and return the winner.

    The gate must never sink a run: any judge failure (bad JSON, out-of-range
    index, model outage) falls back to the first draft.
    """
    if len(candidates) == 1:
        return candidates[0]

    kit = _THEMED_KITS.get(content_type)
    if kit:
        criteria = kit["judge_criteria"]
    elif video_format == "long":
        criteria = prompts.LONG_JUDGE_CRITERIA
    else:
        criteria = prompts.NEWS_JUDGE_CRITERIA
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
        # The verdict is the one piece of pipeline reasoning that decides what gets
        # published, and until now it existed only in a log line that CI throws away.
        tag_current_run(
            judge_winner=winner,
            judge_scores=verdict.get("scores"),
            judge_reason=verdict.get("reason"),
            candidates=len(candidates),
            criteria=content_type or video_format,
        )
        return candidates[winner - 1]
    except Exception as e:  # noqa: BLE001
        logger.warning("[{}] script judge failed ({}); using first draft", project_id, e)
        tag_current_run(judge_outcome="failed_fell_back_to_first", judge_error=str(e)[:200])
        return candidates[0]


# --------------------------------------------------------------------------- nodes
def research_node(state: PipelineState) -> PipelineState:
    # Theme-driven types need no factual research; skip straight to the script.
    if state.get("content_type") in _THEMED_KITS:
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

    if kit := _THEMED_KITS.get(content_type):
        prompt_args = {
            "prompt": kit["script_prompt"].format(topic=state["topic"]),
            "system": kit["system"],
        }
    else:
        is_short = state.get("video_format", "short") == "short"
        template = prompts.SCRIPT_PROMPT_SHORT if is_short else prompts.SCRIPT_PROMPT_LONG
        prompt_args = {
            "prompt": template.format(topic=state["topic"], research=state.get("research", "")),
            "system": prompts.SCRIPT_SYSTEM,
        }

    candidates = _generate_candidates(prompt_args, content_type, project_id)
    script = _pick_best_script(
        candidates,
        state["topic"],
        content_type,
        project_id,
        state.get("video_format", "short"),
    )

    words = len(script.split())
    logger.info(
        "[{}] script done ({} drafts, kept {} words, ~{:.0f}s narration)",
        project_id,
        len(candidates),
        words,
        words / WORDS_PER_MINUTE * 60,
    )
    return {"script": script}


def _is_tech_topic(state: PipelineState) -> bool:
    """True when the story came from the curated AI/tech feeds rather than raw trends.

    Long-form mixes both (see trends.discover_mixed), so branding is decided per video:
    an AI story gets the developer-channel framing, a general trending story ("La Liga:
    five talking points...") must not — claiming to be AI news over a football video
    misleads viewers and muddies the topical signal YouTube reads from descriptions.
    Shorts pass no source and are always AI news, so the default stays tech.
    """
    return state.get("topic_source", "").startswith("ainews:") or not state.get("topic_source")


def metadata_node(state: PipelineState) -> PipelineState:
    kit = _THEMED_KITS.get(state.get("content_type", "news"))
    is_tech = _is_tech_topic(state)
    if kit:
        prompt = kit["metadata_prompt"].format(script=state.get("script", ""))
    else:
        template = (
            prompts.LONG_METADATA_PROMPT
            if state.get("video_format", "short") == "long"
            else prompts.METADATA_PROMPT
        )
        prompt = template.format(topic=state["topic"], script=state.get("script", ""))
    system = prompts.METADATA_SYSTEM if (kit or is_tech) else prompts.GENERAL_METADATA_SYSTEM
    data = _as_dict(
        get_llm().chat_json(prompt, system=system),
        "metadata_node",
    )
    title = _clean_text(str(data.get("title") or state["topic"]))[:MAX_TITLE_CHARS]
    hashtags = _clean_hashtags(data.get("hashtags"))

    # Reserve room for the footer so appending it can never push us past YouTube's limit.
    if kit:
        footer = kit["footer"]
    else:
        footer = prompts.DESCRIPTION_FOOTER if is_tech else prompts.GENERAL_NEWS_FOOTER
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
    if is_short:
        seconds_per_scene, cap = settings.seconds_per_scene, settings.max_scenes_short
    else:
        seconds_per_scene, cap = settings.seconds_per_scene_long, settings.max_scenes_long
    n_scenes = max(3, min(round(est_seconds / max(1, seconds_per_scene)), cap))

    # Themed types use their moody aesthetic instead of literal news imagery.
    kit = _THEMED_KITS.get(state.get("content_type", "news"))
    image_system = kit["image_system"] if kit else prompts.THUMBNAIL_SYSTEM
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
    # Models often return the wrong count; cycle through what came back rather than
    # failing the run. The modulus is taken against the ORIGINAL count: `len(scenes)`
    # grows as we append, and `x % x` is always 0, so reading the list being built
    # silently pinned every padded scene to prompt #1 — a video whose back half was
    # all opening shot, no matter what the narration had moved on to.
    returned = list(scenes)
    while len(scenes) < n_scenes:
        scenes.append(returned[len(scenes) % len(returned)])
    scenes = scenes[:n_scenes]

    thumbnail_prompt = str(data.get("thumbnail_prompt") or "").strip() or scenes[0]
    logger.info("[{}] {} scene prompts ({})", state.get("project_id"), len(scenes), aspect)
    return {"thumbnail_prompt": thumbnail_prompt, "scene_prompts": scenes}
