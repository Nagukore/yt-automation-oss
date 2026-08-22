"""Video assembly: images + voiceover (+ music bed, burned captions) -> final MP4.

Strategy (all free / local):
1. MoviePy stitches the scene images into a slideshow. Each scene gets a Ken-Burns
   move rendered by sub-pixel cropping (see `_motion_clip`) and scenes are joined
   with a crossfade, timed so the whole thing lands on the voiceover's duration.
2. FFmpeg masters the audio in one pass: it loops a background-music track under
   the voiceover and normalizes the result to broadcast loudness (see
   `_master_audio`). The video stream is copied, so this pass is cheap. If it
   fails the untouched voiceover-only audio is kept.
3. FFmpeg burns captions onto the result via libass — word-by-word animated ASS
   when word timings are available, otherwise the plain SRT. If burning fails,
   the un-captioned video is kept so the step never hard-fails.

Each stage falls back rather than raising, so a render always produces a
publishable file; the log says which quality tier it landed on.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger


def _patch_pillow_compat() -> None:
    """Restore PIL constants MoviePy 1.x needs but Pillow >=10 removed.

    Pillow deprecated `Image.ANTIALIAS` in 9.1 and deleted it in 10. MoviePy 1.0.3
    still calls it inside its resize fx, so every render raises AttributeError on a
    modern Pillow. The constants were only renamed, so aliasing them back is exact,
    not a workaround. Harmless on older Pillow, which already defines them.
    """
    from PIL import Image  # noqa: PLC0415

    for old, new in (("ANTIALIAS", "LANCZOS"), ("BILINEAR", "BILINEAR"), ("BICUBIC", "BICUBIC")):
        if not hasattr(Image, old):
            resampling = getattr(Image, "Resampling", Image)
            if hasattr(resampling, new):
                setattr(Image, old, getattr(resampling, new))


# Subtitle burn styles (ASS force_style). "default" is the caption look used by
# news and humor; "quote" renders phrase cues large, italic and dead-center for
# the sad-quote-reel aesthetic (viewers read the line as it is spoken).
SUBTITLE_STYLES = {
    "default": "FontSize=18,Outline=2,Shadow=1,Alignment=2,MarginV=60",
    # NB: force_style Alignment uses legacy SSA numbering (2=bottom-center,
    # 6=top-center, 10=middle-center), not ASS numpad values.
    "quote": (
        "FontSize=20,Italic=1,Outline=1,Shadow=2,Alignment=10,"
        "MarginL=40,MarginR=40,Spacing=1"
    ),
}


def assemble_video(
    image_paths: list[str],
    audio_path: str,
    output_path: Path,
    subtitle_path: str | None = None,
    vertical: bool = True,
    music_path: str | None = None,
    subtitle_style: str = "default",
) -> Path:
    _patch_pillow_compat()  # must run before MoviePy's fx modules are used

    from moviepy.editor import AudioFileClip, concatenate_videoclips  # noqa: PLC0415

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not image_paths:
        raise ValueError("assemble_video requires at least one image")

    width = settings.shorts_width if vertical else settings.longform_width
    height = settings.shorts_height if vertical else settings.longform_height

    audio = AudioFileClip(str(audio_path))
    duration = max(audio.duration, 1.0)

    # Crossfades overlap, so each scene must run longer than its share of the
    # timeline for the total to still land on the voiceover's duration.
    n = len(image_paths)
    xfade = min(XFADE, duration / n * 0.4) if n > 1 else 0.0
    per_image = (duration + (n - 1) * xfade) / n

    clips = []
    for i, img in enumerate(image_paths):
        clip = _motion_clip(str(img), per_image, width, height, i)
        if i and xfade:
            clip = clip.crossfadein(xfade)
        clips.append(clip)

    # padding is used arithmetically by MoviePy, so it must stay numeric: a
    # single-image render has no crossfade and simply pads by zero.
    video = concatenate_videoclips(clips, method="compose", padding=-xfade)
    # Guard against float drift in the padding arithmetic leaving a frame of black.
    video = video.set_duration(duration).set_audio(audio)

    tmp = output_path.with_name("_tmp_" + output_path.name)
    video.write_videofile(
        str(tmp),
        fps=settings.video_fps,
        codec="libx264",
        audio_codec="aac",
        # Nothing normally ships this track — the master pass re-reads the original
        # voiceover rather than this re-encode — but if every master rung fails it is
        # what publishes, and MoviePy's default lands near 128k.
        audio_bitrate="192k",
        threads=4,
        preset="medium",
        # The subtitle burn re-encodes this file, so keep the first pass visually
        # lossless — generation loss on gradients is what makes AI slideshows look
        # muddy. CRF 18 is roughly transparent at these resolutions.
        ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p"],
        logger=None,
    )
    video.close()
    audio.close()

    # Always run, music or not: the loudness pass is what the normalization is for,
    # and a voiceover-only video needs it just as much as a mixed one.
    music = Path(music_path) if music_path and Path(music_path).exists() else None
    mastered = output_path.with_name("_mix_" + output_path.name)
    if _master_audio(tmp, music, mastered, duration, Path(audio_path)):
        tmp.unlink(missing_ok=True)
        tmp = mastered

    captions = _caption_file(audio_path, subtitle_path, output_path, width, height, subtitle_style)
    if captions:
        style = "" if captions.suffix == ".ass" else SUBTITLE_STYLES.get(
            subtitle_style, SUBTITLE_STYLES["default"]
        )
        if _burn_subtitles(tmp, captions, output_path, style):
            tmp.unlink(missing_ok=True)
            return output_path

    shutil.move(str(tmp), str(output_path))
    return output_path


def _caption_file(
    audio_path: str,
    subtitle_path: str | None,
    output_path: Path,
    width: int,
    height: int,
    subtitle_style: str,
) -> Path | None:
    """Pick what to burn: animated ASS when timings allow, else the plain SRT.

    The ASS build returns None when no word timings exist, so a TTS provider that
    can't supply them degrades to the old static captions rather than failing.
    """
    srt = Path(subtitle_path) if subtitle_path else None
    has_srt = bool(srt and srt.exists() and srt.stat().st_size > 0)

    if settings.animated_captions:
        try:
            from app.media.subtitles.animated import build_animated_captions  # noqa: PLC0415

            ass = build_animated_captions(
                audio_path,
                subtitle_path if has_srt else None,
                output_path.with_name("captions.ass"),
                width,
                height,
                subtitle_style,
            )
            if ass:
                return ass
        except Exception as e:  # noqa: BLE001
            logger.warning("animated captions failed ({}); falling back to SRT", e)

    return srt if has_srt else None


def pick_music(content_type: str) -> str | None:
    """Random track from assets/music/<content_type>/, or None if there are none.

    Music is optional by design: an empty (or missing) directory means the video
    ships voiceover-only, exactly as before. Drop royalty-free MP3/WAV files in
    per-type folders to enable it (see assets/music/README.md).
    """
    import random  # noqa: PLC0415

    folder = Path(settings.music_dir) / content_type
    tracks = [
        p for p in folder.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")
    ] if folder.is_dir() else []
    if not tracks:
        return None
    return str(random.choice(tracks))


# Crossfade between scenes. Long enough to read as a dissolve, short enough that
# no scene loses meaningful screen time.
XFADE = 0.5

# Music bed fades, in seconds. See _music_fades. The out is the one that matters —
# it is what stops the looped bed being guillotined at full level on the last frame.
MUSIC_FADE_IN = 0.5
MUSIC_FADE_OUT = 1.2

# Post-enlargement sharpening. See _sharpen for why this is needed at all and why
# the strength is capped where it is.
SHARPEN_MIN_SCALE = 1.05  # below this the resample costs nothing worth correcting
SHARPEN_RADIUS = 2
SHARPEN_PERCENT_PER_X = 65.0  # per 1.0x of enlargement
SHARPEN_MAX_PERCENT = 80.0

# Loudness target for the finished mix, in LUFS (EBU R128 integrated).
#
# YouTube normalizes playback to roughly -14 LUFS, but the adjustment is one-way:
# it turns loud uploads DOWN and leaves quiet ones exactly as they are. So a video
# mastered below the target doesn't get helped — it simply plays quieter than every
# other video in the feed, which on a Short is a swipe rather than a reach for the
# volume key. Measured on the first 100 uploads (ffmpeg -af ebur128): -20.9 and
# -20.7 LUFS integrated, about 7 dB down on a correctly mastered Short.
#
# Not configurable on purpose: -14 is a property of the platform, not a taste.
LOUDNESS_TARGET_LUFS = -14.0
# True-peak ceiling. -1.5 dBFS leaves room for the overshoot that lossy encoding
# adds after us, so the AAC YouTube transcodes from never clips.
LOUDNESS_TRUE_PEAK_DBFS = -1.5
# Loudness range. Narration is naturally flat (the renders measure LRA ~2), so this
# is a ceiling that never binds on speech; it exists to stop a dynamic music bed
# from being squashed flat.
LOUDNESS_RANGE_LU = 11.0

# Camera moves, cycled per scene so consecutive images never repeat one.
# (zoom_start, zoom_end, x_start, x_end, y_start, y_end); x/y are fractions of the
# *available* pan range at that zoom, so a move can never run off the image and
# its magnitude adapts to how much headroom the source resolution allows.
_MOVES = (
    (1.00, 1.12, 0.50, 0.50, 0.50, 0.50),  # push in
    (1.12, 1.00, 0.50, 0.50, 0.50, 0.50),  # pull out
    (1.14, 1.14, 0.12, 0.88, 0.50, 0.50),  # pan right
    (1.06, 1.14, 0.50, 0.50, 0.30, 0.70),  # push in, drift down
    (1.14, 1.14, 0.88, 0.12, 0.50, 0.50),  # pan left
    (1.14, 1.06, 0.50, 0.50, 0.70, 0.30),  # pull out, rise
)


def _ease(p: float) -> float:
    """Smoothstep, so a move accelerates in and decelerates out rather than snapping."""
    p = min(1.0, max(0.0, p))
    return p * p * (3.0 - 2.0 * p)


def _sharpen(img, scale: float):
    """Unsharp-mask an enlarged source to claw back the local contrast Lanczos smears.

    This cannot add detail that was never generated — a 576x1024 image stretched to
    fill a 1080x1920 frame holds 576x1024 worth of information no matter what runs
    over it. What it can do is restore edge contrast, and at a glance that is most of
    what reads as "soft". Applied once to the pre-scaled source rather than per frame:
    the crop window samples from this image every frame, so sharpening here is both
    cheaper and consistent across the move.

    Strength scales with how far the image was stretched, and is capped. Past roughly
    80% the halos around high-contrast edges become more visible than the softness
    they are covering, and AI art is full of exactly those edges.
    """
    from PIL import ImageFilter  # noqa: PLC0415

    percent = round(min(SHARPEN_MAX_PERCENT, SHARPEN_PERCENT_PER_X * (scale - 1.0)))
    if percent <= 0:
        return img
    return img.filter(
        ImageFilter.UnsharpMask(radius=SHARPEN_RADIUS, percent=percent, threshold=3)
    )


def _motion_clip(img_path: str, duration: float, width: int, height: int, index: int):
    """A Ken Burns move rendered by sub-pixel cropping instead of per-frame scaling.

    The obvious MoviePy approach — `clip.resize(lambda t: ...)` — rounds the frame
    to whole pixels on every frame, so the image visibly steps rather than glides,
    and it leaves the composed clip larger than the target frame. Here the source
    is instead sampled through a *float* crop box: PIL's `resize(box=...)` accepts
    fractional coordinates, so the window moves in true sub-pixel increments and
    every frame comes out at exactly width x height.

    The source is pre-scaled so that the most zoomed-in moment of the move is
    pixel-for-pixel 1:1. When the provider hands us an image larger than the frame
    that means the picture is only ever downsampled, which is what keeps it sharp.

    In practice it usually does not. Pollinations treats the requested width/height
    as an aspect-ratio hint and returns 576x1024 whatever a Short asks for, so the
    pre-scale is an ~2.1x *enlargement* and the frame is softer than its CRF implies.
    `_sharpen` mitigates that; only a provider that returns frame-sized images fixes
    it. Nothing here can tell the two cases apart beyond the scale factor, which is
    exactly what decides whether sharpening runs.
    """
    import numpy as np  # noqa: PLC0415
    from moviepy.editor import VideoClip  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    src = Image.open(img_path)
    if src.mode != "RGB":
        src = src.convert("RGB")

    z0, z1, x0, x1, y0, y1 = _MOVES[index % len(_MOVES)]
    peak = max(z0, z1)

    scale = max(width * peak / src.width, height * peak / src.height)
    if abs(scale - 1.0) > 0.01:
        src = src.resize(
            (max(1, round(src.width * scale)), max(1, round(src.height * scale))),
            Image.LANCZOS,
        )
        if scale >= SHARPEN_MIN_SCALE:
            src = _sharpen(src, scale)
    sw, sh = src.size

    # Widest window: the largest frame-aspect rectangle that fits the source.
    base_w = min(sw, sh * width / height)
    base_h = base_w * height / width
    span = max(duration, 1e-6)

    def make_frame(t):
        p = _ease(t / span)
        zoom = z0 + (z1 - z0) * p
        win_w = base_w / zoom
        win_h = base_h / zoom
        left = (x0 + (x1 - x0) * p) * (sw - win_w)
        top = (y0 + (y1 - y0) * p) * (sh - win_h)
        frame = src.resize(
            (width, height),
            Image.LANCZOS,
            box=(left, top, left + win_w, top + win_h),
        )
        return np.asarray(frame)

    return VideoClip(make_frame, duration=duration)


def _ffmpeg_exe() -> str | None:
    """Locate FFmpeg: system PATH first, else the binary bundled with imageio-ffmpeg.

    MoviePy pulls in imageio-ffmpeg, so a working FFmpeg is available via pip even
    when the user has never installed one system-wide (common on Windows).
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def _audio_inputs(src: Path, music: Path | None, voice: Path | None = None) -> list[str]:
    """FFmpeg input args: the render, the original voiceover, and the looped music.

    `src` is here for its video stream; its audio is the AAC that MoviePy already
    encoded once. Mastering from `voice` instead means the narration is compressed
    exactly once, on the way out, rather than being decoded from a 128k intermediate
    and re-encoded. `voice` is None only when the file has gone missing, which drops
    us back to the old behaviour rather than failing.
    """
    args = ["-i", str(src)]
    if voice:
        args += ["-i", str(voice)]
    if music:
        args += ["-stream_loop", "-1", "-i", str(music)]
    return args


def _audio_chain(
    music: Path | None, loudnorm: str, duration: float = 0.0, voice: Path | None = None
) -> str:
    """filter_complex producing [a]: optional music bed, then the loudness pass.

    `duration=first` stops the looped music exactly at the voiceover's end, and
    `normalize=0` keeps amix from halving the voice to make headroom for the bed —
    the bed is already ducked to settings.music_volume. Loudness is measured after
    the mix so the number that lands is the one the viewer actually hears.

    The stream indices track _audio_inputs: the voice is input 1 when the original
    voiceover was handed to FFmpeg, and input 0 (the render's own audio) when it
    wasn't. Both callers pass the same `duration` and `voice`, which matters — the
    analysis pass has to measure the exact chain the correction pass will apply.
    """
    v = "[1:a]" if voice else "[0:a]"
    if music:
        vol = max(0.0, min(settings.music_volume, 1.0))
        m = "[2:a]" if voice else "[1:a]"
        return (
            f"{m}volume={vol}{_music_fades(duration)}[m];"
            f"{v}[m]amix=inputs=2:duration=first:normalize=0[mix];"
            f"[mix]{loudnorm}[a]"
        )
    return f"{v}{loudnorm}[a]"


def _music_fades(duration: float) -> str:
    """afade pair that eases the music bed in and out, or "" if we can't place them.

    Without this the bed simply stops on the last frame: `-stream_loop -1` makes the
    track endless and amix's `duration=first` guillotines it wherever the narration
    happens to end, mid-phrase and at full level. On a Short, which loops, that hard
    edge is audible on every pass. The fades apply to the music only — fading the
    mix would swallow the last narrated word.

    Both fades are capped at a quarter of the runtime so a very short video gets
    proportionally shorter fades instead of a bed that is never at full level.
    """
    if duration <= 0:
        return ""
    fade_in = min(MUSIC_FADE_IN, duration / 4)
    fade_out = min(MUSIC_FADE_OUT, duration / 4)
    return (
        f",afade=t=in:st=0:d={fade_in:.3f}"
        f",afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}"
    )


def _loudnorm(measured: dict | None = None) -> str:
    """The loudnorm filter, in measuring form or in corrective form.

    Called twice per render. With no `measured` it is the analysis pass; feeding
    its results back in switches loudnorm to linear mode, which applies one
    constant gain across the whole track. Single-pass loudnorm is dynamic instead:
    it rides the gain as it goes, and because it starts out knowing nothing about
    the material it pumps audibly through the first few seconds — which on a Short
    is precisely the part that decides whether anyone stays.
    """
    base = (
        f"loudnorm=I={LOUDNESS_TARGET_LUFS}:TP={LOUDNESS_TRUE_PEAK_DBFS}"
        f":LRA={LOUDNESS_RANGE_LU}"
    )
    if not measured:
        return base
    return base + (
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        ":linear=true"
    )


def _measure_loudness(
    ffmpeg: str, src: Path, music: Path | None, duration: float, voice: Path | None
) -> dict | None:
    """Analysis pass: what loudness does the finished mix actually sit at?

    Decodes audio only and discards the output, so it runs at ~25x realtime — a
    second or so for a Short. Returns None if anything about the measurement is
    unusable, which drops the caller back to single-pass mode rather than failing.
    """
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner",
        *_audio_inputs(src, music, voice),
        "-filter_complex",
        _audio_chain(music, _loudnorm() + ":print_format=json", duration, voice),
        "-map", "[a]", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        logger.warning("loudness analysis failed: {}", e.stderr.decode(errors="ignore")[-300:])
        return None

    # loudnorm prints its JSON report as the last object on stderr, after the
    # normal encoder chatter.
    err = proc.stderr.decode(errors="ignore")
    start, end = err.rfind("{"), err.rfind("}")
    if start < 0 or end < start:
        logger.warning("loudness analysis produced no JSON report")
        return None
    try:
        report = json.loads(err[start : end + 1])
    except ValueError:
        logger.warning("loudness analysis report was not valid JSON")
        return None

    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if any(k not in report for k in required):
        logger.warning("loudness analysis report was missing fields")
        return None
    # A silent or near-silent track measures as -inf and linear mode cannot use it.
    if any(not _is_finite(report[k]) for k in required):
        logger.warning("loudness analysis measured a non-finite level; staying single-pass")
        return None
    return report


def _is_finite(value: object) -> bool:
    """True when a loudnorm report field is a real number (not '-inf' / 'nan')."""
    try:
        num = float(str(value))
    except (TypeError, ValueError):
        return False
    return num == num and abs(num) != float("inf")


def _run_master(
    ffmpeg: str, src: Path, music: Path | None, dest: Path, chain: str, voice: Path | None
) -> bool:
    """Write `dest` = video of `src` (stream-copied) + the audio `chain` produces."""
    cmd = [
        ffmpeg, "-y", "-nostdin", "-hide_banner",
        *_audio_inputs(src, music, voice),
        "-filter_complex", chain,
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy",
        # loudnorm resamples its output to 192 kHz internally; bring it back to a
        # rate YouTube expects rather than shipping an oddly-sampled AAC track.
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.warning("audio master failed: {}", e.stderr.decode(errors="ignore")[-300:])
        return False


def _master_audio(
    src: Path,
    music: Path | None,
    dest: Path,
    duration: float = 0.0,
    voice: Path | None = None,
) -> bool:
    """Mix the optional music bed and normalize the result to LOUDNESS_TARGET_LUFS.

    The video stream is copied, so the whole thing costs a couple of seconds.

    Three rungs, each a real video if the one above it fails: two-pass linear
    normalization, then single-pass dynamic, then the plain music mix with no
    normalization at all (the behaviour before loudness mastering existed). False
    means every rung failed and the caller should keep the unmastered render.
    """
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        logger.warning("no ffmpeg available; skipping audio master")
        return False

    # A missing voiceover just means mastering from the render's own audio, as it
    # did before — one extra generation of AAC, not a failure.
    if voice is not None and not voice.exists():
        logger.warning("voiceover {} missing; mastering from the render's audio", voice.name)
        voice = None

    measured = _measure_loudness(ffmpeg, src, music, duration, voice)
    if measured and _run_master(
        ffmpeg, src, music, dest, _audio_chain(music, _loudnorm(measured), duration, voice), voice
    ):
        logger.info(
            "audio mastered to {} LUFS (was {} LUFS){}",
            LOUDNESS_TARGET_LUFS,
            measured["input_i"],
            f", music bed {music.name}" if music else "",
        )
        return True

    if _run_master(
        ffmpeg, src, music, dest, _audio_chain(music, _loudnorm(), duration, voice), voice
    ):
        logger.info("audio mastered to {} LUFS (single-pass)", LOUDNESS_TARGET_LUFS)
        return True

    if music and _run_master(
        ffmpeg, src, music, dest, _audio_chain(music, "anull", duration, voice), voice
    ):
        logger.warning("loudness normalization failed; mixed music bed {} only", music.name)
        return True

    # A failed rung can leave a half-written file behind, and the caller is about to
    # publish `src` instead — so nothing should be left claiming to be the master.
    dest.unlink(missing_ok=True)
    return False


def _filter_path(p: Path) -> str:
    """Escape a path for use inside an FFmpeg filter argument.

    The filter parser treats ':' as an option separator and '\\' as an escape, so
    a Windows path has to be rewritten before it can be embedded.
    """
    return str(p).replace("\\", "/").replace(":", "\\:")


def _burn_subtitles(src: Path, subs: Path, dest: Path, style: str) -> bool:
    """Burn `subs` (SRT or ASS) into the video via libass.

    `style` is a force_style override and applies to SRT only — an ASS file
    already carries its own styling, and force_style would flatten the per-word
    animation it encodes.
    """
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        logger.warning("no ffmpeg available; skipping subtitle burn-in")
        return False
    vf = f"subtitles='{_filter_path(subs)}'"
    # Point libass at the repo's own fonts so it can use a face that is not
    # installed system-wide — this is what makes a CI render match a local one.
    fonts = Path(settings.caption_font_dir)
    if fonts.is_dir() and any(fonts.iterdir()):
        vf += f":fontsdir='{_filter_path(fonts.resolve())}'"
    if style:
        vf += f":force_style='{style}'"
    try:
        subprocess.run(
            [
                ffmpeg, "-y", "-i", str(src),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-c:a", "copy", str(dest),
            ],
            check=True,
            capture_output=True,
        )
        logger.info("burned {} captions", subs.suffix.lstrip("."))
        return True
    except subprocess.CalledProcessError as e:
        logger.warning("subtitle burn-in failed: {}", e.stderr.decode(errors="ignore")[-300:])
        return False


__all__ = ["assemble_video", "pick_music", "SUBTITLE_STYLES"]
