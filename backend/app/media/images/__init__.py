"""Pluggable image generation providers.

Default (free, no key, no GPU): Pollinations.ai hosted endpoint.
Alternatives: local Stable Diffusion / FLUX (diffusers), OpenAI Images.
Selected via settings.image_provider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.logging import logger


class ImageProvider(Protocol):
    def generate(self, prompt: str, dest: Path, width: int, height: int) -> Path:
        """Write an image to `dest`. Raises if generation genuinely failed.

        Providers must NOT swallow failures by writing a placeholder — the caller
        decides on fallbacks so that failures stay visible in logs and metrics.
        """
        ...


def write_placeholder(dest: Path, width: int, height: int, prompt: str) -> Path:
    """Deterministic gradient stand-in, used only when a provider has given up."""
    from app.media.images.pollinations import write_placeholder as _wp

    _wp(dest, width, height, prompt)
    return dest


def get_image_provider() -> ImageProvider:
    name = settings.image_provider.lower()
    if name == "pollinations":
        from app.media.images.pollinations import PollinationsProvider

        return PollinationsProvider()
    if name in ("stable_diffusion", "flux"):
        from app.media.images.diffusers_local import DiffusersProvider

        return DiffusersProvider(flux=name == "flux")
    if name == "openai":
        from app.media.images.openai_images import OpenAIImageProvider

        return OpenAIImageProvider()
    logger.warning("unknown image_provider '{}', falling back to pollinations", name)
    from app.media.images.pollinations import PollinationsProvider

    return PollinationsProvider()
