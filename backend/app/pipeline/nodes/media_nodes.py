"""LangGraph nodes that produce the *media* artifacts (images, audio, subs, video).

These delegate to the pluggable providers in app.media. Heavy libraries are
imported lazily inside the providers so importing this module is cheap and the
text-only pipeline works even if media extras aren't installed.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import tag_current_run
from app.media.images import get_image_provider, write_placeholder
from app.media.subtitles import get_subtitle_provider
from app.media.tts import get_tts_provider
from app.media.video import assemble_video, pick_music
from app.pipeline.state import PipelineState


def images_node(state: PipelineState) -> PipelineState:
    provider = get_image_provider()
    is_short = state.get("video_format", "short") == "short"
    width = settings.shorts_width if is_short else settings.longform_width
    height = settings.shorts_height if is_short else settings.longform_height
    out_dir = settings.project_media_dir(state["project_id"]) / "images"
    project_id = state["project_id"]

    scene_prompts = list(state.get("scene_prompts", []))
    thumb_path = out_dir / "thumbnail.png"

    # Generation is ~30-40s per image and entirely network-bound, so a serial loop
    # costs minutes per video. Generate concurrently, but bounded: hosted free
    # endpoints throttle (or degrade) under too many parallel requests.
    jobs: list[tuple[str, Path]] = [
        (prompt, out_dir / f"scene_{i:03d}.png") for i, prompt in enumerate(scene_prompts)
    ]
    jobs.append((state.get("thumbnail_prompt") or state.get("title", ""), thumb_path))

    started = time.perf_counter()
    workers = max(1, min(settings.image_concurrency, len(jobs)))
    failed: list[tuple[str, Path]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(provider.generate, prompt, dest, width, height): (prompt, dest)
            for prompt, dest in jobs
        }
        for future in as_completed(futures):
            prompt, dest = futures[future]
            try:
                future.result()
            except Exception as e:  # noqa: BLE001
                logger.error("[{}] image failed for {}: {}", project_id, dest.name, e)
                failed.append((prompt, dest))

    # Only now fall back to placeholders, so the count above reflects reality.
    for prompt, dest in failed:
        write_placeholder(dest, width, height, prompt)

    paths = [str(dest) for _, dest in jobs[:-1] if dest.exists() and dest.stat().st_size > 0]
    if not paths:
        raise RuntimeError("image generation produced no usable scenes")

    real = len(jobs) - len(failed)
    logger.info(
        "[{}] images: {}/{} real, {} placeholder, {:.0f}s ({} workers)",
        project_id,
        real,
        len(jobs),
        len(failed),
        time.perf_counter() - started,
        workers,
    )
    # Placeholder count is the number that predicts a bad video, and it appears
    # nowhere in the node's output state — the paths look identical either way.
    tag_current_run(
        image_provider=settings.image_provider,
        requested=len(jobs),
        real=real,
        placeholders=len(failed),
        workers=workers,
        seconds=round(time.perf_counter() - started, 1),
    )
    # A video made mostly of gradients isn't worth a human's review slot.
    if real < len(jobs) / 2:
        raise RuntimeError(
            f"image generation mostly failed ({real}/{len(jobs)} real); "
            "provider is likely rate-limiting — lower IMAGE_CONCURRENCY or retry later"
        )
    return {"image_paths": paths, "thumbnail_path": str(thumb_path)}


def voiceover_node(state: PipelineState) -> PipelineState:
    provider = get_tts_provider()
    dest = settings.project_media_dir(state["project_id"]) / "audio" / "voiceover.wav"
    # Providers choose their own container (edge-tts returns .mp3), so trust the
    # returned path rather than the one we requested.
    written = provider.synthesize(state["script"], dest)
    logger.info("[{}] voiceover done -> {}", state["project_id"], Path(written).name)
    # The graph records the state in and out; which provider served it, and whether
    # the word-timing sidecar that drives the captions actually landed, is not in
    # either — and a missing sidecar silently downgrades captions to static SRT.
    words = Path(written).with_name(Path(written).stem + ".words.json")
    tag_current_run(
        tts_provider=settings.tts_provider,
        voice=settings.edge_tts_voice,
        script_chars=len(state.get("script") or ""),
        audio_kb=round(Path(written).stat().st_size / 1024) if Path(written).exists() else 0,
        word_timings=words.exists(),
    )
    return {"audio_path": str(written)}


def subtitles_node(state: PipelineState) -> PipelineState:
    provider = get_subtitle_provider()
    dest = settings.project_media_dir(state["project_id"]) / "audio" / "subtitles.srt"
    provider.transcribe(state["audio_path"], dest)
    logger.info("[{}] subtitles done -> {}", state["project_id"], dest.name)
    tag_current_run(
        subtitle_provider=settings.subtitle_provider,
        srt_bytes=dest.stat().st_size if dest.exists() else 0,
    )
    return {"subtitle_path": str(dest)}


def video_node(state: PipelineState) -> PipelineState:
    is_short = state.get("video_format", "short") == "short"
    content_type = state.get("content_type", "news")
    dest = settings.project_media_dir(state["project_id"]) / "video" / "final.mp4"
    music = pick_music(content_type)
    if music:
        logger.info("[{}] music bed: {}", state["project_id"], Path(music).name)
    assemble_video(
        image_paths=state["image_paths"],
        audio_path=state["audio_path"],
        subtitle_path=state.get("subtitle_path"),
        output_path=dest,
        vertical=is_short,
        music_path=music,
        # Heartbreak renders its phrase cues as a centered on-screen quote
        # (the reel aesthetic); other types keep bottom captions.
        subtitle_style="quote" if content_type == "code_heartbreak" else "default",
    )
    logger.info("[{}] video assembled -> {}", state["project_id"], dest)
    tag_current_run(
        vertical=is_short,
        subtitle_style="quote" if content_type == "code_heartbreak" else "default",
        animated_captions=settings.animated_captions,
        music=Path(music).name if music else None,
        scenes=len(state.get("image_paths") or []),
        video_mb=round(dest.stat().st_size / 1048576, 1) if dest.exists() else 0,
    )
    return {"video_path": str(dest)}
