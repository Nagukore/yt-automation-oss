"""Free, keyless image generation via Pollinations.ai.

Great zero-cost default: a plain HTTPS GET returns a generated image. Falls back
to a locally-rendered gradient placeholder if the service is unreachable, so the
pipeline never hard-fails on the image step.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.logging import logger


def _seed(prompt: str, dest: Path) -> int:
    """Generation seed for one scene: stable per video, different across videos.

    Seeding on the prompt alone made the endpoint a pure function of the text, so
    any two videos whose scene prompts collided got byte-identical pictures. With
    themes on a rotation the collisions are not hypothetical — the same theme comes
    round every few weeks and a small model tends to reach for the same imagery
    ("rain on a dark window, single glowing monitor") when it does.

    Mixing the destination in fixes that while keeping the property worth having:
    `dest` is project_<id>/images/scene_<n>.png, so re-rendering a project rebuilds
    exactly the same images rather than quietly producing a different video.
    """
    key = f"{prompt}\x00{dest.parent.parent.name}\x00{dest.stem}"
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 1_000_000


class PollinationsProvider:
    BASE = "https://image.pollinations.ai/prompt"

    # The free endpoint rate-limits aggressively (429) under concurrency. Retry
    # patiently — a 429 here is transient, and waiting beats shipping a placeholder.
    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    def _fetch(self, prompt: str, width: int, height: int, seed: int) -> bytes:
        url = (
            f"{self.BASE}/{quote(prompt)}"
            f"?width={width}&height={height}&seed={seed}&nologo=true&model=flux"
        )
        with httpx.Client(timeout=180) as client:
            resp = client.get(url)
            resp.raise_for_status()
            if not resp.content:
                raise ValueError("empty image body")
            return resp.content

    def generate(self, prompt: str, dest: Path, width: int, height: int) -> Path:
        """Generate an image. Raises on failure so the caller can count real vs fallback.

        Placeholder writing is deliberately *not* done here: silently substituting a
        gradient made failures look like successes in the logs.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = self._fetch(prompt, width, height, _seed(prompt, dest))
        dest.write_bytes(data)
        _warn_if_undersized(dest, width, height)
        logger.debug("pollinations image saved {}", dest.name)
        return dest


def _warn_if_undersized(dest: Path, width: int, height: int) -> None:
    """Log when the endpoint returns an image smaller than the frame it must fill.

    Pollinations treats width/height as an aspect-ratio hint rather than a size: a
    1080x1920 request comes back 576x1024, and so does every other combination —
    measured across `flux`, `turbo`, `sana` and the default, at four request sizes.
    (`/models` now lists only `sana`, so the `model=flux` above is silently falling
    back to it; changing that would change the look of a running channel, so it is
    left alone deliberately.)

    The renderer then enlarges by ~2.1x to fill the frame, which means the video is
    sourced at roughly a third of its nominal resolution. Nothing said so before this:
    the image count was right, so the run logged `7/7 real` and looked healthy. That
    silence is the actual bug — the soft output is a provider limit, but not knowing
    about it is ours.
    """
    from PIL import Image  # noqa: PLC0415

    try:
        got_w, got_h = Image.open(dest).size
    except Exception as e:  # noqa: BLE001
        logger.debug("could not measure {}: {}", dest.name, e)
        return
    if got_w < width or got_h < height:
        logger.warning(
            "{}: provider returned {}x{} for a {}x{} request; the render will enlarge "
            "it {:.1f}x and sharpen to compensate",
            dest.name,
            got_w,
            got_h,
            width,
            height,
            max(width / got_w, height / got_h),
        )


def write_placeholder(dest: Path, width: int, height: int, prompt: str) -> None:
    """Deterministic gradient placeholder so downstream steps still run."""
    from PIL import Image, ImageDraw

    seed = int(hashlib.sha256(prompt.encode()).hexdigest(), 16)
    r, g, b = (seed >> 0) & 255, (seed >> 8) & 255, (seed >> 16) & 255
    img = Image.new("RGB", (width, height), (r, g, b))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        shade = int(255 * (y / height))
        draw.line([(0, y), (width, y)], fill=((r + shade) % 256, (g + shade) % 256, b))
    img.save(dest)
