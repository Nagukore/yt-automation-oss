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
import json
import re
import unicodedata
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger

# Trades off nothing: the service is free either way.
DEFAULT_VOICE = "en-US-AriaNeural"


# --------------------------------------------------------------------------- #
# punctuation recovery
#
# WordBoundary events carry the bare orthographic word: the service reports
# "rates", never "rates,". That costs the captions more than it looks. Beyond the
# missing punctuation on screen, the caption builder breaks phrases on sentence-final
# punctuation — a test that can never pass on this data, so captions straddle
# sentence boundaries. Both are fixed by matching the tokens back against the source
# text (which we have in full) and re-attaching what the service dropped.
# --------------------------------------------------------------------------- #

# Closing marks only. An opening bracket or quote belongs to the *next* word, and
# `\ { }` must never appear — the caption renderer reads them as ASS markup.
_CLOSERS = ".,!?…;:)]}\"'”’»"
_CLOSER_RUN = re.compile(rf"[{re.escape(_CLOSERS)}]{{1,3}}")
# How far past the cursor to look for a token before giving up on it. Generous
# enough to step over an expansion ("$1.2B" -> "1.2 billion dollars"), short enough
# that a genuinely absent token doesn't match some later repeat of itself.
_SEARCH_WINDOW = 48


def _fold(s: str) -> str:
    """Case/width-fold for comparison, one character in, one character out.

    Length must be preserved: the spans found in the folded text index back into the
    original, so a fold that expanded 'ß' to 'ss' or 'ﬁ' to 'fi' would silently shift
    every later offset. Characters that don't fold 1:1 are left alone — failing to
    match one unusual token is harmless, mis-slicing the text is not.
    """
    out = []
    for ch in s:
        folded = unicodedata.normalize("NFKC", ch).casefold()
        out.append(folded if len(folded) == 1 else ch)
    return "".join(out)


def _attach_punctuation(text: str, tokens: list[str], *, min_match: float = 0.8) -> list[str]:
    """Re-attach source punctuation to bare word-boundary tokens.

    Returns exactly `len(tokens)` items in the same order; each is the original token,
    optionally with a run of punctuation appended. Words are never dropped, split,
    merged or reordered — the timings are keyed by position, so losing one would
    desynchronise every caption after it.

    Works by locating each token in `text` and attaching only what provably sits
    *between* it and the token after it. The obvious alternative — match a token, then
    absorb whatever trails it — breaks whenever the service expands a token, because
    then there is no way to know where the next one starts and the absorb swallows the
    wrong span. Requiring both neighbours means an expanded token simply gets nothing.

    Returns `tokens` unchanged if fewer than `min_match` of them could be located:
    half-punctuated output is worse than none, because the caption phrase breaks would
    land at arbitrary points and read as a bug rather than a style.
    """
    if not tokens:
        return []

    folded = _fold(text)
    spans: list[tuple[int, int] | None] = []
    cursor = 0
    for token in tokens:
        ft = _fold(token)
        if not ft:
            spans.append(None)
            continue
        start = cursor
        while start < len(folded) and folded[start].isspace():
            start += 1
        if folded.startswith(ft, start):
            found = start
        else:
            # Search forward only. Rewinding, or searching globally, is what would
            # mis-resolve a repeated word — "No, no, no." must match left to right.
            limit = start + len(ft) + _SEARCH_WINDOW
            found = folded.find(ft, start, limit)
        if found < 0:
            spans.append(None)
            continue
        spans.append((found, found + len(ft)))
        cursor = found + len(ft)

    matched = sum(1 for s in spans if s is not None)
    if matched < min_match * len(tokens):
        logger.warning(
            "edge-tts: only {}/{} caption tokens matched the script; "
            "leaving punctuation off rather than guessing",
            matched,
            len(tokens),
        )
        return list(tokens)

    out: list[str] = []
    for i, token in enumerate(tokens):
        span = spans[i]
        if span is None:
            out.append(token)
            continue
        nxt = next((spans[j] for j in range(i + 1, len(spans)) if spans[j] is not None), None)
        gap = text[span[1] : nxt[0]] if nxt else text[span[1] :]
        # Only the leading run of closers, stopping at the first whitespace. That is
        # what keeps an *opening* quote off the previous word: a closing quote is glued
        # to its punctuation ('."'), an opening one always follows a space ('. "').
        m = _CLOSER_RUN.match(gap)
        out.append(token + m.group(0) if m else token)
    return out


def _repunctuate(text: str, chunks: list[dict]) -> list[dict]:
    """Rewrite each boundary chunk's `text` in place. Count and order preserved."""
    restored = _attach_punctuation(text, [str(c.get("text") or "") for c in chunks])
    for chunk, value in zip(chunks, restored, strict=True):
        chunk["text"] = value
    return chunks


class EdgeTTSProvider:
    def __init__(self, voice: str | None = None) -> None:
        import edge_tts  # noqa: F401  (fail fast if the dep is missing)

        self.voice = voice or settings.edge_tts_voice or DEFAULT_VOICE
        self.rate = settings.edge_tts_rate
        self.pitch = settings.edge_tts_pitch

    def synthesize(self, text: str, dest: Path) -> Path:
        """Synthesize `text`; returns the audio path actually written (.mp3).

        Also writes a sibling .srt with word-accurate timings, and a
        `.words.json` sidecar holding the raw per-word offsets that the animated
        caption renderer needs (the SRT alone is too coarse — its cues can span a
        whole sentence).
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        audio_path = dest.with_suffix(".mp3")
        srt_path = dest.with_suffix(".srt")

        text = (text or "").strip()
        if not text:
            raise ValueError("edge-tts received empty text")

        words_path = audio_path.with_name(audio_path.stem + ".words.json")
        # Per-word offsets are what make karaoke captions land on the beat, but the
        # service only emits them for voices that support it and errors out for the
        # rest — so ask for words first and drop back to sentences if refused.
        try:
            cues = asyncio.run(
                self._stream(text, audio_path, srt_path, words_path, "WordBoundary")
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("edge-tts word boundaries unavailable ({}); using sentences", e)
            cues = asyncio.run(
                self._stream(text, audio_path, srt_path, words_path, "SentenceBoundary")
            )
        logger.info(
            "edge-tts wrote {} ({:.0f} KB), {} subtitle cues",
            audio_path.name,
            audio_path.stat().st_size / 1024,
            cues,
        )
        return audio_path

    async def _stream(
        self, text: str, audio_path: Path, srt_path: Path, words_path: Path, boundary: str
    ) -> int:
        """Stream audio to disk, collecting boundary cues. Returns the cue count."""
        import edge_tts

        communicate = edge_tts.Communicate(
            text, self.voice, rate=self.rate, pitch=self.pitch, boundary=boundary
        )
        submaker = edge_tts.SubMaker()
        boundaries: list[dict] = []

        with open(audio_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                # The service emits SentenceBoundary by default and WordBoundary for some
                # voices/settings. Keep both: sentence-level cues read better as subtitles,
                # and accepting either means we never silently produce an empty SRT.
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    boundaries.append(chunk)

        # Buffer first, then restore punctuation, then feed SubMaker — so the SRT gets
        # the repaired text too. It is not a spare: the video step burns the SRT
        # whenever the animated caption build declines.
        if boundaries and boundary == "WordBoundary":
            # SentenceBoundary cues already carry their punctuation; re-running the
            # match over them would be a no-op at best.
            boundaries = _repunctuate(text, boundaries)

        raw = [
            # Offsets are in 100-nanosecond ticks.
            {
                "s": c["offset"] / 1e7,
                "e": (c["offset"] + c["duration"]) / 1e7,
                "t": c["text"],
            }
            for c in boundaries
        ]
        for c in boundaries:
            # SubMaker refuses a mix of boundary types; the raw list above is what the
            # animated captions use, so a rejected cue is not fatal.
            try:
                submaker.feed(c)
            except ValueError:
                pass

        if raw:
            words_path.write_text(json.dumps(raw), encoding="utf-8")

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
