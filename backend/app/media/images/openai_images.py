"""OpenAI Images provider (paid, optional). Requires OPENAI_API_KEY."""

from __future__ import annotations

import base64
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger


class OpenAIImageProvider:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not set but image_provider=openai")

    def generate(self, prompt: str, dest: Path, width: int, height: int) -> Path:
        from openai import OpenAI  # noqa: PLC0415

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        client = OpenAI(api_key=settings.openai_api_key)
        size = _closest_size(width, height)
        result = client.images.generate(model="gpt-image-1", prompt=prompt, size=size, n=1)
        b64 = result.data[0].b64_json
        dest.write_bytes(base64.b64decode(b64))
        logger.debug("openai image saved {}", dest.name)
        return dest


def _closest_size(w: int, h: int) -> str:
    if w == h:
        return "1024x1024"
    return "1024x1536" if h > w else "1536x1024"
