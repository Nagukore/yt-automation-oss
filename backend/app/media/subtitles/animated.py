r"""Word-level animated ("karaoke") captions, rendered as ASS rather than SRT.

Why ASS: the burn-in already runs through FFmpeg, whose bundled build has libass
(verified via --enable-libass). libass renders vector text per frame, so smooth
sub-frame animation — colour cross-fades, scale pops — and crisp glyphs come for
free. The alternative, rasterising text per frame through MoviePy's TextClip,
is both slower and dependent on ImageMagick.

Alignment is the fussy part of karaoke captions. If the *active* word changes
size, every word after it shifts and the line visibly wobbles. So the animation
is deliberately split in two:

  * the **phrase** scales up once as it appears. It is anchored with \an5 + \pos,
    so it grows symmetrically about a fixed centre and the anchor never moves.
  * the **active word** only cross-fades to the accent colour, which cannot
    change the line's metrics.

The result reads as word-by-word karaoke while every glyph stays pinned to the
same baseline for the phrase's whole life.

Emphasis (numbers, product names, the closing line) works within that rule rather
than against it: an emphasised word is held at a *constant* larger scale for the
phrase's whole life, never animated mid-phrase, so the line's metrics stay fixed and
nothing re-flows. Two consequences worth knowing:

  * a larger run raises the line box, so with \an5 the baseline sits ~4px higher on
    an emphasised phrase than an unemphasised one at Shorts size. It is constant
    *within* a phrase, and between phrases the text has cut anyway, so it reads as a
    cut rather than a wobble. Keep `emphasis_scale` at or below ~1.12; the shift
    grows linearly with it.
  * if that ever does become visible, `\bord` is the metric-neutral escape hatch —
    libass draws the border outside the advance width, so it never re-flows a line.

Timings come from `<audio>.words.json` (written by the TTS provider from its own
word-boundary events, so they are exact). When that sidecar is missing we fall
back to interpolating word positions inside each SRT cue, which is approximate
but still tracks the speech closely enough to read as aligned.

The speech service reports words *without* punctuation; the TTS provider restores it
against the source script before writing the sidecar. Phrase grouping depends on
that — without it `_ends_sentence` can never fire and captions straddle sentences.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from math import ceil
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str
    emphasis: bool = False


@dataclass(frozen=True)
class CaptionStyle:
    """Look and grouping rules for one caption treatment.

    Sizes are fractions of frame height so a style renders identically at any
    resolution, and `y_ratio` is measured to the *centre* of the text block.
    """

    faces: tuple[tuple[str, str], ...] = ()  # (ASS family name, font filename)
    size_ratio: float = 0.046
    y_ratio: float = 0.60
    max_words: int = 4
    max_chars: int = 18  # only used when the font can't be measured
    accent: str = "FFD60A"  # RGB hex of the highlight colour
    bold: bool = False  # Arial Black is already black; synthetic bold just smears it
    italic: bool = False
    uppercase: bool = True
    pop: bool = True  # scale-in on phrase entry

    # Which punctuation is *drawn*. Grouping always sees the full punctuation either
    # way; this only controls the look. "terminal" keeps the marks that carry sentence
    # intent and drops the ones that are visual lint at display size — at ~88px a
    # comma reads as a speck on the glyph rather than as a comma.
    punctuation: str = "terminal"  # "all" | "terminal" | "none"

    # Key words (numbers, versions, product names, the closing line) held in a second
    # colour and a constant size bump, so the eye lands on them while the karaoke
    # highlight still walks the line normally.
    emphasis: bool = True
    emphasis_colour: str = "00E5FF"  # must differ from white *and* from `accent`
    emphasis_scale: float = 1.12  # constant \fscx/\fscy; 1.0 disables the size bump
    emphasis_min_gap: int = 3  # words between emphasised runs


# Face candidates, tried in order. Renders happen on Windows (dev) and on Ubuntu
# CI runners (production), which share almost no fonts — so each style lists a
# chain ending in a face that is always present. The *same* entry drives both the
# ASS family name and the PIL measurement, so libass and the width check can never
# disagree about which face is being laid out.
_DISPLAY_FACES = (
    ("Anton", "Anton-Regular.ttf"),  # bundled in assets/fonts, if present
    ("Arial Black", "ariblk.ttf"),  # Windows
    ("Archivo Black", "ArchivoBlack-Regular.ttf"),  # fonts-archivo
    ("Liberation Sans Bold", "LiberationSans-Bold.ttf"),  # fonts-liberation
    ("DejaVu Sans", "DejaVuSans-Bold.ttf"),  # always on Ubuntu
)
_TEXT_FACES = (
    ("Segoe UI", "segoeuib.ttf"),  # Windows
    ("Liberation Sans", "LiberationSans-Bold.ttf"),
    ("DejaVu Sans", "DejaVuSans-Bold.ttf"),
)

# Vertical (Shorts) treatments. `y_ratio` 0.60 keeps captions clear of the
# title/handle/action rail that YouTube overlays across the bottom of a Short.
STYLES: dict[str, CaptionStyle] = {
    "default": CaptionStyle(faces=_DISPLAY_FACES),
    # Heartbreak reels read as an on-screen quote: centred, sentence case, calmer
    # highlight, and longer phrases so a line lands as a thought rather than a chant.
    "quote": CaptionStyle(
        faces=_TEXT_FACES,
        size_ratio=0.042,
        y_ratio=0.50,
        max_words=6,
        max_chars=26,
        accent="9AD8FF",
        bold=True,
        italic=True,
        uppercase=False,
        pop=False,
        # A quote reel is set as written prose, commas included, and its whole design
        # is calm — highlighting numbers in cyan would fight everything else here.
        punctuation="all",
        emphasis=False,
    ),
}

# Horizontal overrides: a 1920x1080 frame wants a lower third and longer lines.
_LANDSCAPE = {"size_ratio": 0.058, "y_ratio": 0.82, "max_words": 7, "max_chars": 44}

HOLD = 0.18  # seconds a phrase lingers after its last word
# A phrase stretches to meet the next one across a pause no longer than this.
# Blinking the captions off for a breath between sentences reads as a glitch and
# leaves the frame empty; holding the last phrase keeps the screen occupied.
# Sized to cover a full sentence break: scripts are written to pause there, so this
# is the gap it has to survive, not the gap between two words.
BRIDGE = 1.4
MAX_PHRASE_DUR = 2.6
MAX_GAP = 0.45  # a longer silence than this starts a new phrase


# --------------------------------------------------------------------------- #
# sentence detection
# --------------------------------------------------------------------------- #

_SENT_END = re.compile(r"[.!?…]['\")”’]?$")
# A trailing period that closes an abbreviation, not a sentence. Breaking the phrase
# after "U.S." or "vs." splits a line mid-thought.
_ABBREV = frozenset(
    "mr mrs ms dr prof st vs etc eg ie inc corp ltd jr sr no fig al approx".split()
)
_DOTTED_ACRONYM = re.compile(r"^(?:[A-Za-z]\.)+$")


def _ends_sentence(text: str) -> bool:
    """True when this word ends a sentence, excluding abbreviations.

    Only meaningful once word timings carry punctuation at all — see the TTS
    provider, which restores what the speech service strips.
    """
    if not _SENT_END.search(text):
        return False
    core = text.rstrip("'\")”’")
    if _DOTTED_ACRONYM.match(core):  # U.S., a.k.a.
        return False
    return core.rstrip(".").lower() not in _ABBREV


# --------------------------------------------------------------------------- #
# timing sources
# --------------------------------------------------------------------------- #

_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def load_words(audio_path: str | Path, subtitle_path: str | Path | None = None) -> list[Word]:
    """Exact word timings if the TTS sidecar exists, else interpolated from SRT."""
    sidecar = Path(audio_path).with_name(Path(audio_path).stem + ".words.json")
    if sidecar.exists() and sidecar.stat().st_size > 0:
        try:
            raw = json.loads(sidecar.read_text(encoding="utf-8"))
            cues: list[Word] = []
            for c in raw:
                cues.extend(_split_cue(float(c["s"]), float(c["e"]), str(c["t"])))
            words = _normalise(cues)
            if words:
                logger.info("captions: {} word timings from {}", len(words), sidecar.name)
                return words
        except Exception as e:  # noqa: BLE001
            logger.warning("captions: unreadable word sidecar {} ({})", sidecar.name, e)

    if subtitle_path and Path(subtitle_path).exists():
        words = _words_from_srt(Path(subtitle_path))
        if words:
            logger.info("captions: {} word timings interpolated from SRT", len(words))
            return words

    logger.warning("captions: no usable word timings; animated captions skipped")
    return []


def _split_cue(start: float, end: float, text: str) -> list[Word]:
    """Break a cue into words, sharing its span in proportion to token length.

    A single-word cue passes straight through, so this is a no-op on true
    WordBoundary data. It matters for the sentence-level cues some voices emit
    (and for SRT), where a cue can carry a whole sentence: without splitting,
    an entire sentence would highlight as one step.

    Weighting by character count beats dividing evenly because longer tokens
    genuinely take longer to say.
    """
    tokens = text.split()
    if len(tokens) <= 1 or end <= start:
        return [Word(start, max(end, start + 0.04), text.strip())] if text.strip() else []

    weights = [len(t) + 1 for t in tokens]
    total = sum(weights)
    out: list[Word] = []
    cursor = start
    for tok, w in zip(tokens, weights, strict=True):
        span = (end - start) * w / total
        out.append(Word(cursor, cursor + span, tok))
        cursor += span
    return out


def _words_from_srt(path: Path) -> list[Word]:
    """Interpolate word timings inside each SRT cue."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    out: list[Word] = []
    for block in re.split(r"\n\s*\n", text):
        m = _SRT_TIME.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = " ".join(block[m.end() :].split())
        out.extend(_split_cue(start, end, body))
    return _normalise(out)


def _normalise(words: list[Word]) -> list[Word]:
    """Drop empties, merge stray punctuation back, and force monotonic timing.

    A token that is pure punctuation must not become its own highlight step — it
    belongs to the word before it, both visually and rhythmically.
    """
    out: list[Word] = []
    for w in words:
        text = " ".join(w.text.split())
        if not text:
            continue
        start = max(0.0, w.start)
        end = max(start + 0.04, w.end)
        if out and not re.search(r"\w", text):
            prev = out[-1]
            out[-1] = Word(prev.start, max(prev.end, end), prev.text + text)
            continue
        if out and start < out[-1].end:
            start = out[-1].end
            end = max(start + 0.04, end)
        out.append(Word(start, end, text))
    return out


# --------------------------------------------------------------------------- #
# what gets drawn
# --------------------------------------------------------------------------- #

# Marks that carry sentence intent, kept under `punctuation="terminal"`.
_TERMINAL_KEPT = ".!?…"
_ALL_MARKS = ".,!?…;:\"'”’»)]}"


def _render_text(word: Word, style: CaptionStyle) -> str:
    """The exact string drawn for this word, before case folding and escaping.

    Both the width measurement and the ASS emission go through here. If they ever
    disagreed about which characters are drawn, the width check would be measuring a
    different line than libass lays out and a full-width phrase could wrap off its
    anchor — the same reason `resolve_face` returns one face for both purposes.
    """
    if style.punctuation == "all":
        return word.text
    trail = len(word.text) - len(word.text.rstrip(_ALL_MARKS))
    if not trail:
        return word.text
    core, marks = word.text[:-trail], word.text[-trail:]
    if style.punctuation == "none":
        return core or word.text
    kept = "".join(c for c in marks if c in _TERMINAL_KEPT)
    return (core + kept) or word.text


# --------------------------------------------------------------------------- #
# emphasis
# --------------------------------------------------------------------------- #

_HAS_DIGIT = re.compile(r"\d")
# Two capitals anywhere: GPT, OpenAI, PyTorch, API, JavaScript. Position-independent,
# so it still fires on a proper noun that opens a sentence.
_INTERNAL_CAPS = re.compile(r"[A-Z].*[A-Z]")
_LEADING_CAP = re.compile(r"^[A-Z][a-z]")
# Capitalised mid-sentence but never a proper noun. "I" and its contractions are
# everywhere in the themed streams and would otherwise be emphasised constantly.
_NEVER = frozenset("i i'm i'll i've i'd a an the".split())


def mark_emphasis(words: list[Word], style: CaptionStyle) -> list[Word]:
    """Flag the key words: numbers and versions, acronyms and proper nouns, the punchline.

    Rate-limited to one run every `emphasis_min_gap` words. Without the cap the rules
    fire on a third of a technical script — every acronym, every sentence opener — and
    emphasis that lands everywhere is just a second body colour.
    """
    if not style.emphasis or not words:
        return words

    sentence_start = True
    flags: list[bool] = []
    for w in words:
        core = w.text.strip(_ALL_MARKS)
        hit = bool(core) and core.lower() not in _NEVER and (
            bool(_HAS_DIGIT.search(core))
            or bool(_INTERNAL_CAPS.search(core))
            # A leading capital only means something away from a sentence start.
            or (not sentence_start and bool(_LEADING_CAP.match(core)))
        )
        flags.append(hit)
        sentence_start = _ends_sentence(w.text)

    out: list[Word] = []
    gap = style.emphasis_min_gap
    since = gap  # let the first hit through
    open_run = False
    for i, w in enumerate(words):
        core = w.text.strip(_ALL_MARKS)
        prev = words[i - 1].text.strip(_ALL_MARKS) if i else ""
        # A version number stays welded to its name — "GPT 5", "Claude 4" are one
        # thing and splitting the highlight across them reads as two. Only a numeric
        # pair extends a run; chaining any adjacent hits would light up every acronym
        # in a technical sentence.
        pair = flags[i] and open_run and bool(
            _HAS_DIGIT.search(core) or _HAS_DIGIT.search(prev)
        )
        on = pair or (flags[i] and since >= gap)
        # The final word is the line the video lands on: emphasised on merit, not by
        # rule match, and never rate-limited away.
        on = on or i == len(words) - 1
        out.append(Word(w.start, w.end, w.text, on) if on != w.emphasis else w)
        open_run, since = on, (0 if on else since + 1)
    return out


def font_dir() -> Path:
    """Directory holding fonts bundled with the repo (may not exist)."""
    return Path(settings.caption_font_dir)


def resolve_face(style: CaptionStyle, size: int) -> tuple[str, object | None]:
    """Pick the first candidate face that is actually installed.

    Returns the ASS family name to write into the style line, plus a loaded PIL
    font for width measurement (None if nothing could be loaded, in which case
    grouping falls back to a character budget).

    Both come from the *same* candidate, which is the point: if libass laid out
    one face while we measured another, the width check would be meaningless and
    phrases could wrap.
    """
    try:
        from PIL import ImageFont  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return (style.faces[0][0] if style.faces else "sans-serif"), None

    bundled = font_dir()
    for family, filename in style.faces:
        for candidate in (bundled / filename, Path(filename)):
            try:
                # A bare filename lets PIL search the system font directories.
                return family, ImageFont.truetype(str(candidate), size)
            except OSError:
                continue

    fallback = style.faces[0][0] if style.faces else "sans-serif"
    logger.warning(
        "captions: none of {} installed; libass will substitute and phrase widths "
        "fall back to a character budget",
        [f for f, _ in style.faces],
    )
    return fallback, None


def _phrase_width(phrase: list[Word], style: CaptionStyle, measure) -> float:
    """Width of the rendered line, inflated for any constant-scaled words.

    The joined string is measured exactly as it always was, so a phrase with no
    emphasis is bit-identical to the old behaviour — kerning across the spaces
    included. Only the *extra* width contributed by the scaled runs is estimated, which
    keeps the approximation confined to the delta instead of letting a sum-of-parts
    error perturb every ordinary phrase.
    """
    parts = [_render_text(w, style) for w in phrase]
    if style.uppercase:
        parts = [p.upper() for p in parts]
    base = measure(" ".join(parts))
    if style.emphasis_scale == 1.0:
        return base
    extra = sum(measure(p) for p, w in zip(parts, phrase, strict=True) if w.emphasis)
    return base + (style.emphasis_scale - 1.0) * extra


def group_phrases(
    words: list[Word],
    style: CaptionStyle,
    max_px: float | None = None,
    measure=None,
) -> list[list[Word]]:
    """Chunk words into on-screen phrases, breaking on the cues a reader expects."""

    def too_wide(candidate: list[Word]) -> bool:
        if measure and max_px:
            return _phrase_width(candidate, style, measure) > max_px
        parts = [_render_text(w, style) for w in candidate]
        budget = len(" ".join(parts))
        budget += ceil(
            (style.emphasis_scale - 1.0)
            * sum(len(p) for p, w in zip(parts, candidate, strict=True) if w.emphasis)
        )
        return budget > style.max_chars

    phrases: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            if (
                len(cur) >= style.max_words
                or too_wide([*cur, w])
                or w.start - cur[-1].end > MAX_GAP
                or w.end - cur[0].start > MAX_PHRASE_DUR
                or _ends_sentence(cur[-1].text)
            ):
                phrases.append(cur)
                cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    return phrases


# --------------------------------------------------------------------------- #
# ASS emission
# --------------------------------------------------------------------------- #


def _ts(t: float) -> str:
    """ASS timestamps are H:MM:SS.cc (centiseconds)."""
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _bgr(rgb: str) -> str:
    """`\\c` override form: &HBBGGRR&."""
    return f"&H{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}&"


def _style_colour(rgb: str, alpha: str = "00") -> str:
    """Style-line form: &HAABBGGRR."""
    return f"&H{alpha}{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"


def _esc(text: str) -> str:
    """Neutralise the characters libass reads as markup."""
    return text.replace("\\", "∖").replace("{", "(").replace("}", ")")


def _metrics(style: CaptionStyle, width: int, height: int) -> tuple[int, int, int, int]:
    """(font size, outline, shadow, side margin) in real frame pixels."""
    size = max(12, round(height * style.size_ratio))
    return size, max(2, round(size * 0.11)), max(1, round(size * 0.045)), round(width * 0.07)


def _header(style: CaptionStyle, width: int, height: int, family: str) -> str:
    size, outline, shadow, margin = _metrics(style, width, height)
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            # Matching PlayRes to the real frame keeps libass at 1:1 — no
            # resampling, so glyph edges stay sharp.
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding",
            (
                f"Style: Caption,{family},{size},"
                f"{_style_colour('FFFFFF')},{_style_colour(style.accent)},"
                f"{_style_colour('101010')},{_style_colour('000000', 'A0')},"
                f"{-1 if style.bold else 0},{-1 if style.italic else 0},0,0,100,100,0,0,1,"
                f"{outline},{shadow},5,{margin},{margin},{margin},1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )


def _scale_tag(emphasis: bool, style: CaptionStyle, grow: int) -> str:
    r"""Explicit `\fscx/\fscy` for one run, animated when the phrase is popping.

    Every run in an emphasised line carries one of these, including the separator
    spaces. That is not redundancy: `\fscx` persists until it is overridden, so a
    single emphasised word would otherwise leave every word after it enlarged.

    During the entry pop each run animates from its *own* 92% rather than inheriting
    the line-level tag — an inline scale overrides that tag, which would leave the
    emphasised word frozen at full size while the rest of the line grew around it,
    exactly the mid-phrase re-flow this module exists to avoid.
    """
    hi = max(100, round(style.emphasis_scale * 100)) if emphasis else 100
    if not grow:
        return f"\\fscx{hi}\\fscy{hi}"
    lo = round(hi * 0.92)
    return f"\\fscx{lo}\\fscy{lo}\\t(0,{grow},\\fscx{hi}\\fscy{hi})"


def _phrase_events(
    phrase: list[Word], p_end: float, style: CaptionStyle, cx: int, cy: int
) -> list[str]:
    """One Dialogue line per word, each showing the whole phrase.

    Events are butted end-to-end (each ends exactly where the next begins) so the
    highlight walks the line without a gap or a flicker between words.
    """
    white = _bgr("FFFFFF")
    accent = _bgr(style.accent)
    emph_colour = _bgr(style.emphasis_colour)
    # Scale tags are only emitted when this phrase actually holds an emphasised word,
    # so an ordinary line renders byte-for-byte as it did before.
    scaled = any(w.emphasis for w in phrase) and style.emphasis_scale != 1.0
    events: list[str] = []

    for i, word in enumerate(phrase):
        start = phrase[0].start if i == 0 else word.start
        end = phrase[i + 1].start if i + 1 < len(phrase) else p_end
        if end <= start:
            continue

        # Keep the pop inside the first word so it is finished before the
        # next event replaces the line at full size — otherwise it snaps.
        popping = style.pop and i == 0
        grow = max(60, min(170, int((end - start) * 700))) if popping else 0

        tags = [f"\\pos({cx},{cy})"]
        fade_in = 90 if i == 0 else 0
        fade_out = 110 if i == len(phrase) - 1 else 0
        if fade_in or fade_out:
            tags.append(f"\\fad({fade_in},{fade_out})")
        if popping and not scaled:
            # When `scaled`, each run carries its own pop instead — a line-level tag
            # here would be overridden per run and animate nothing.
            tags.append(f"\\fscx92\\fscy92\\t(0,{grow},\\fscx100\\fscy100)")

        parts = ["{" + "".join(tags) + "}"]
        for j, w in enumerate(phrase):
            if j:
                # The separator inherits the previous run's scale unless told
                # otherwise, which would stretch the space after an emphasised word.
                parts.append(f"{{{_scale_tag(False, style, grow)}}} " if scaled else " ")
            drawn = _render_text(w, style)
            text = _esc(drawn.upper() if style.uppercase else drawn)
            scale = _scale_tag(w.emphasis, style, grow) if scaled else ""
            base = emph_colour if w.emphasis else white
            if j == i:
                # Cross-fade into the accent so the highlight glides rather than cuts.
                # It starts from this word's own base colour, so the karaoke highlight
                # still reads on a word that is already emphasised.
                ramp = max(40, min(110, int((end - start) * 450)))
                parts.append(f"{{\\c{base}{scale}\\t(0,{ramp},\\c{accent})}}{text}")
            else:
                parts.append(f"{{\\c{base}{scale}}}{text}")

        events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{''.join(parts)}")
    return events


def build_animated_captions(
    audio_path: str | Path,
    subtitle_path: str | Path | None,
    dest: Path,
    width: int,
    height: int,
    style_name: str = "default",
) -> Path | None:
    """Write an animated .ass caption file. Returns None if timings are unusable.

    Returning None rather than raising lets the caller fall back to the plain SRT
    burn-in, so a missing sidecar degrades the look without failing the render.
    """
    style = STYLES.get(style_name, STYLES["default"])
    if width > height:
        style = CaptionStyle(**{**style.__dict__, **_LANDSCAPE})

    # Marked here rather than in `load_words` so that stays a pure timing function:
    # emphasis is a styling decision and depends on the style, which timings do not.
    words = mark_emphasis(load_words(audio_path, subtitle_path), style)
    if not words:
        return None

    size, outline, _shadow, margin = _metrics(style, width, height)
    family, font = resolve_face(style, size)
    # The outline grows the drawn line by roughly `outline` px on each side, so it
    # has to come out of the usable width or a full-width phrase clips its edges.
    max_px = width - 2 * margin - 2 * outline
    phrases = group_phrases(words, style, max_px, font.getlength if font else None)
    if not phrases:
        return None

    cx, cy = width // 2, round(height * style.y_ratio)
    events: list[str] = []
    for pi, phrase in enumerate(phrases):
        nxt = phrases[pi + 1][0].start if pi + 1 < len(phrases) else None
        p_end = phrase[-1].end + HOLD
        if nxt is not None and nxt - phrase[-1].end <= BRIDGE:
            p_end = nxt
        # Give a phrase ending on a very short word enough time to be read...
        p_end = max(p_end, phrase[-1].start + 0.08)
        if nxt is not None:
            # ...but never at the cost of bleeding into the next phrase: two \pos'd
            # lines at one anchor would draw on top of each other. Butting exactly is
            # not bleeding — libass shows the old line strictly before the boundary —
            # and leaving even a 10ms hole here blanks a whole frame at 30fps, which
            # is visible as a flicker on every phrase change. A phrase squeezed to
            # nothing is dropped by the empty-event guard below.
            p_end = min(p_end, nxt)
        events.extend(_phrase_events(phrase, p_end, style, cx, cy))

    if not events:
        return None

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = _header(style, width, height, family) + "\n" + "\n".join(events) + "\n"
    dest.write_text(body, encoding="utf-8")
    logger.info(
        "captions: {} phrases / {} events -> {} ({}, {})",
        len(phrases),
        len(events),
        dest.name,
        style_name,
        family,
    )
    return dest


__all__ = [
    "STYLES",
    "CaptionStyle",
    "Word",
    "build_animated_captions",
    "group_phrases",
    "load_words",
    "mark_emphasis",
]
