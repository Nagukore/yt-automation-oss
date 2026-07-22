"""Pluggable text-to-speech providers.

Default (free, keyless, cross-platform): edge-tts. It also emits word-accurate
subtitles as a side effect, which lets the pipeline skip Whisper entirely.
Alternatives: Piper / Kokoro (local, no network), ElevenLabs (paid).

`synthesize` returns the path actually written — providers may choose their own
container (edge-tts writes .mp3), so callers must use the returned path rather
than assuming the one they passed in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.logging import logger


class TTSProvider(Protocol):
    def synthesize(self, text: str, dest: Path) -> Path: ...


def get_tts_provider() -> TTSProvider:
    name = settings.tts_provider.lower()
    try:
        if name in ("edge", "edge_tts"):
            from app.media.tts.edge_tts_provider import EdgeTTSProvider

            return EdgeTTSProvider()
        if name == "piper":
            from app.media.tts.piper_tts import PiperProvider

            return PiperProvider()
        if name == "kokoro":
            from app.media.tts.kokoro_tts import KokoroProvider

            return KokoroProvider()
        if name == "elevenlabs":
            from app.media.tts.elevenlabs_tts import ElevenLabsProvider

            return ElevenLabsProvider()
    except Exception as e:  # noqa: BLE001
        logger.warning("TTS provider '{}' unavailable ({}); trying edge-tts", name, e)

    # edge-tts is keyless and pure-python, so it's the best last resort before silence.
    if name not in ("edge", "edge_tts"):
        try:
            from app.media.tts.edge_tts_provider import EdgeTTSProvider

            return EdgeTTSProvider()
        except Exception as e:  # noqa: BLE001
            logger.warning("edge-tts unavailable too ({}); using silent fallback", e)

    from app.media.tts.fallback import SilenceProvider

    return SilenceProvider()
