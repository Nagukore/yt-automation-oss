"""Video assembly: images + voiceover (+ burned subtitles) -> final MP4.

Strategy (all free / local):
1. MoviePy stitches the scene images into a slideshow with a gentle Ken-Burns
   zoom, timed to the voiceover duration, and muxes the audio.
2. FFmpeg burns the SRT subtitles onto the result (avoids MoviePy's fragile
   ImageMagick TextClip dependency). If subtitle burning fails, the un-subtitled
   video is kept so the step never hard-fails.
"""

from __future__ import annotations

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


def assemble_video(
    image_paths: list[str],
    audio_path: str,
    output_path: Path,
    subtitle_path: str | None = None,
    vertical: bool = True,
) -> Path:
    _patch_pillow_compat()  # must run before MoviePy's fx modules are used

    from moviepy.editor import (  # noqa: PLC0415
        AudioFileClip,
        ImageClip,
        concatenate_videoclips,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not image_paths:
        raise ValueError("assemble_video requires at least one image")

    width = settings.shorts_width if vertical else settings.longform_width
    height = settings.shorts_height if vertical else settings.longform_height

    audio = AudioFileClip(str(audio_path))
    duration = max(audio.duration, 1.0)
    per_image = duration / len(image_paths)

    clips = []
    for i, img in enumerate(image_paths):
        clip = (
            ImageClip(str(img))
            .set_duration(per_image)
            .resize(height=height)  # scale then crop to fill frame
        )
        clip = _fit_frame(clip, width, height)
        clip = _ken_burns(clip, per_image)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose").set_audio(audio)

    tmp = output_path.with_name("_tmp_" + output_path.name)
    video.write_videofile(
        str(tmp),
        fps=settings.video_fps,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="medium",
        logger=None,
    )
    video.close()
    audio.close()

    if subtitle_path and Path(subtitle_path).exists() and Path(subtitle_path).stat().st_size > 0:
        if _burn_subtitles(tmp, Path(subtitle_path), output_path):
            tmp.unlink(missing_ok=True)
            return output_path

    shutil.move(str(tmp), str(output_path))
    return output_path


def _fit_frame(clip, width: int, height: int):
    """Center-crop the (height-scaled) clip to exactly width x height."""
    from moviepy.video.fx.crop import crop  # noqa: PLC0415

    if clip.w < width:
        clip = clip.resize(width=width)
    return crop(clip, width=width, height=height, x_center=clip.w / 2, y_center=clip.h / 2)


def _ken_burns(clip, duration: float):
    """Slow 8% zoom for visual life on static images."""
    return clip.resize(lambda t: 1 + 0.08 * (t / max(duration, 0.01)))


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


def _burn_subtitles(src: Path, srt: Path, dest: Path) -> bool:
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        logger.warning("no ffmpeg available; skipping subtitle burn-in")
        return False
    # ffmpeg's subtitles filter needs escaped path separators on Windows.
    srt_arg = str(srt).replace("\\", "/").replace(":", "\\:")
    style = "FontSize=18,Outline=2,Shadow=1,Alignment=2,MarginV=60"
    try:
        subprocess.run(
            [
                ffmpeg, "-y", "-i", str(src),
                "-vf", f"subtitles='{srt_arg}':force_style='{style}'",
                "-c:a", "copy", str(dest),
            ],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.warning("subtitle burn-in failed: {}", e.stderr.decode(errors="ignore")[-300:])
        return False


__all__ = ["assemble_video"]
