"""faster-whisper subtitle generation -> SRT file."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger

_model = None


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel  # noqa: PLC0415

        logger.info("loading faster-whisper '{}' ({})", settings.whisper_model, settings.whisper_device)
        _model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
    return _model


def _fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class FasterWhisperProvider:
    def __init__(self) -> None:
        _load()  # fail fast if unavailable so the factory can fall back

    def transcribe(self, audio_path: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        model = _load()
        # Word timestamps cost little on top of the decode and are what the
        # animated captions need; without them we could only interpolate.
        segments, _ = model.transcribe(str(audio_path), beam_size=5, word_timestamps=True)
        lines: list[str] = []
        words: list[dict] = []
        for i, seg in enumerate(segments, start=1):
            lines.append(str(i))
            lines.append(f"{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}")
            lines.append(seg.text.strip())
            lines.append("")
            for w in getattr(seg, "words", None) or []:
                text = (w.word or "").strip()
                if text:
                    words.append({"s": float(w.start), "e": float(w.end), "t": text})
        dest.write_text("\n".join(lines), encoding="utf-8")

        if words:
            # Same sidecar contract as edge-tts, beside the *audio* file, which is
            # where the caption builder looks.
            audio = Path(audio_path)
            audio.with_name(audio.stem + ".words.json").write_text(
                json.dumps(words), encoding="utf-8"
            )
        logger.debug("subtitles written {} ({} words)", dest.name, len(words))
        return dest


class EmptySubtitleProvider:
    """Writes a valid but empty SRT so the video step can proceed."""

    def transcribe(self, audio_path: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("", encoding="utf-8")
        return dest
