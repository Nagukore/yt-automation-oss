"""Free, keyless, high-quality neural TTS via Microsoft Edge's speech service.

Why this is the default:
- No API key, no cost, no GPU, and it works on Windows (unlike Piper's wheels).
- 300+ neural voices that sound far better than local Piper/eSpeak.
- It returns *word-boundary timings*, so we get perfectly-aligned subtitles for
  free — no Whisper/torch download (~2GB) and no ASR transcription errors, because
  the timings come from the source text rather than from recognizing the audio.

Writes MP3 (what the service returns) and, alongside it, an SRT file.
`synthesize` returns the actual audio path written, which may differ in extension
from the requested one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger

# Trades off nothing: the service is free either way.
DEFAULT_VOICE = "en-US-AriaNeural"


class EdgeTTSProvider:
    def __init__(self, voice: str | None = None) -> None:
        import edge_tts  # noqa: F401  (fail fast if the dep is missing)

        self.voice = voice or settings.edge_tts_voice or DEFAULT_VOICE
        self.rate = settings.edge_tts_rate
        self.pitch = settings.edge_tts_pitch

    def synthesize(self, text: str, dest: Path) -> Path:
        """Synthesize `text`; returns the audio path actually written (.mp3).

        Also writes a sibling .srt with word-accurate timings.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        audio_path = dest.with_suffix(".mp3")
        srt_path = dest.with_suffix(".srt")

        text = (text or "").strip()
        if not text:
            raise ValueError("edge-tts received empty text")

        cues = asyncio.run(self._stream(text, audio_path, srt_path))
        logger.info(
            "edge-tts wrote {} ({:.0f} KB), {} subtitle cues",
            audio_path.name,
            audio_path.stat().st_size / 1024,
            cues,
        )
        return audio_path

    async def _stream(self, text: str, audio_path: Path, srt_path: Path) -> int:
        """Stream audio to disk, collecting boundary cues. Returns the cue count."""
        import edge_tts

        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
        submaker = edge_tts.SubMaker()

        with open(audio_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                # The service emits SentenceBoundary by default and WordBoundary for some
                # voices/settings. Feed both: sentence-level cues read better as subtitles,
                # and accepting either means we never silently produce an empty SRT.
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    submaker.feed(chunk)

        try:
            srt = submaker.get_srt()
            if srt.strip():
                srt_path.write_text(srt, encoding="utf-8")
                return len(submaker.cues)
            logger.warning("edge-tts returned no subtitle cues for {}", audio_path.name)
        except Exception as e:  # noqa: BLE001
            # Subtitles are a bonus here; never fail the voiceover over them.
            logger.warning("edge-tts subtitle generation failed: {}", e)
        return 0


async def list_voices(prefix: str = "en-") -> list[str]:
    """Helper for choosing a voice: `python -c "..."` or the /voices endpoint."""
    import edge_tts

    voices = await edge_tts.list_voices()
    return sorted(v["ShortName"] for v in voices if v["ShortName"].startswith(prefix))
