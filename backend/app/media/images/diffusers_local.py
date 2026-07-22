"""Local Stable Diffusion XL / FLUX via HuggingFace diffusers.

Fully free but needs a GPU (or a lot of patience on CPU). Requires the `media`
extra: pip install -e ".[media]". The pipeline is loaded once and cached.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.logging import logger

_pipe = None


def _load(flux: bool):
    global _pipe
    if _pipe is not None:
        return _pipe
    import torch  # noqa: PLC0415
    from diffusers import AutoPipelineForText2Image  # noqa: PLC0415

    model = settings.flux_model if flux else settings.stable_diffusion_model
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    logger.info("loading diffusers model '{}' on {}", model, device)
    _pipe = AutoPipelineForText2Image.from_pretrained(model, torch_dtype=dtype)
    _pipe = _pipe.to(device)
    return _pipe


class DiffusersProvider:
    def __init__(self, flux: bool = False) -> None:
        self.flux = flux

    def generate(self, prompt: str, dest: Path, width: int, height: int) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        pipe = _load(self.flux)
        steps = 4 if self.flux else 30  # FLUX.schnell is a few-step model
        image = pipe(
            prompt=prompt,
            width=_round8(width),
            height=_round8(height),
            num_inference_steps=steps,
        ).images[0]
        image.save(dest)
        return dest


def _round8(x: int) -> int:
    return max(8, (x // 8) * 8)
