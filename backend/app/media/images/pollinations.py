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
        seed = int(hashlib.sha256(prompt.encode()).hexdigest(), 16) % 1_000_000
        data = self._fetch(prompt, width, height, seed)
        dest.write_bytes(data)
        logger.debug("pollinations image saved {}", dest.name)
        return dest


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
