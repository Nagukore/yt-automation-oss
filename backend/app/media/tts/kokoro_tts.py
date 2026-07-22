"""Kokoro TTS — free, local, high-quality neural TTS.

Requires: pip install kokoro soundfile  (part of the media extra set).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.core.config import settings
from app.core.logging import logger


class KokoroProvider:
    SAMPLE_RATE = 24000

    def __init__(self) -> None:
        from kokoro import KPipeline  # noqa: PLC0415

        self.voice = settings.kokoro_voice
        self.pipeline = KPipeline(lang_code="a")  # 'a' = American English

    def synthesize(self, text: str, dest: Path) -> Path:
        import soundfile as sf  # noqa: PLC0415

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        chunks = [audio for _, _, audio in self.pipeline(text, voice=self.voice)]
        audio = np.concatenate(chunks) if chunks else np.zeros(self.SAMPLE_RATE, dtype=np.float32)
        sf.write(str(dest), audio, self.SAMPLE_RATE)
        logger.debug("kokoro synthesized {}", dest.name)
        return dest
