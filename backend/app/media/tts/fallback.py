"""Last-resort TTS: writes silence sized to the text so the pipeline never breaks.

Keeps the video renderer working (with subtitles) even when no TTS engine is
installed — useful for dev/CI. ~0.4s of silence per word.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

from app.core.logging import logger


class SilenceProvider:
    SAMPLE_RATE = 24000

    def synthesize(self, text: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        seconds = max(3.0, len(text.split()) * 0.4)
        n = int(self.SAMPLE_RATE * seconds)
        with wave.open(str(dest), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(struct.pack("<" + "h" * n, *([0] * n)))
        logger.warning("wrote {:.1f}s of SILENCE (no TTS engine available) -> {}", seconds, dest.name)
        return dest
