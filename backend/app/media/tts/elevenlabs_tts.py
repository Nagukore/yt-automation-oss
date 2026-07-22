"""ElevenLabs TTS (paid, optional). Requires ELEVENLABS_API_KEY."""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.logging import logger


class ElevenLabsProvider:
    def __init__(self) -> None:
        if not settings.elevenlabs_api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set but tts_provider=elevenlabs")
        from elevenlabs.client import ElevenLabs  # noqa: PLC0415

        self.client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        self.voice = settings.elevenlabs_voice_id

    def synthesize(self, text: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # ElevenLabs returns MP3; save as .mp3 next to the requested path.
        mp3_path = dest.with_suffix(".mp3")
        audio = self.client.text_to_speech.convert(
            voice_id=self.voice,
            model_id="eleven_multilingual_v2",
            text=text,
            output_format="mp3_44100_128",
        )
        with open(mp3_path, "wb") as f:
            for chunk in audio:
                f.write(chunk)
        logger.debug("elevenlabs synthesized {}", mp3_path.name)
        return mp3_path
