"""Piper TTS — fast, free, fully local neural TTS (CPU-friendly).

Uses the `piper` CLI if available on PATH, otherwise the `piper-tts` Python
package. Voice models are auto-downloaded by piper on first use.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger


class PiperProvider:
    def __init__(self) -> None:
        self.voice = settings.piper_voice
        self.cli = shutil.which("piper")

    def synthesize(self, text: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if self.cli:
            # piper reads text on stdin and writes a wav file.
            subprocess.run(
                [self.cli, "--model", self.voice, "--output_file", str(dest)],
                input=text.encode("utf-8"),
                check=True,
            )
            return dest

        # Python API fallback
        from piper import PiperVoice  # noqa: PLC0415
        import wave  # noqa: PLC0415

        voice = PiperVoice.load(self.voice)
        with wave.open(str(dest), "wb") as wav:
            voice.synthesize(text, wav)
        logger.debug("piper synthesized {}", dest.name)
        return dest
