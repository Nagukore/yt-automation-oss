"""Subtitle generation.

Two strategies:
1. "edge" (default) — reuse the word-boundary timings edge-tts already produced
   while synthesizing the voiceover. Free, instant and exact: the timings come
   from the source text, so there are no transcription errors and no ~2GB torch
   download. The SRT is written next to the audio file by the TTS provider.
2. "faster_whisper" — real ASR over the rendered audio. Needed only when the TTS
   provider can't supply timings (Piper / Kokoro / ElevenLabs).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.logging import logger


class SubtitleProvider(Protocol):
    def transcribe(self, audio_path: str, dest: Path) -> Path: ...


class SidecarSubtitleProvider:
    """Picks up the .srt that edge-tts wrote alongside the audio."""

    def transcribe(self, audio_path: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        sidecar = Path(audio_path).with_suffix(".srt")

        if sidecar.exists() and sidecar.stat().st_size > 0:
            if sidecar.resolve() != dest.resolve():
                shutil.copyfile(sidecar, dest)
            logger.info("subtitles: reused TTS word timings ({})", dest.name)
            return dest

        logger.warning("no sidecar SRT beside {}; falling back to ASR", Path(audio_path).name)
        return _whisper_or_empty().transcribe(audio_path, dest)


def _whisper_or_empty() -> SubtitleProvider:
    try:
        from app.media.subtitles.whisper_subs import FasterWhisperProvider

        return FasterWhisperProvider()
    except Exception as e:  # noqa: BLE001
        logger.warning("faster-whisper unavailable ({}); subtitles will be empty", e)
        from app.media.subtitles.whisper_subs import EmptySubtitleProvider

        return EmptySubtitleProvider()


def get_subtitle_provider() -> SubtitleProvider:
    name = (settings.subtitle_provider or "edge").lower()
    if name in ("edge", "edge_tts", "sidecar"):
        return SidecarSubtitleProvider()
    return _whisper_or_empty()
