"""OpenRouter chat client with a model fallback chain and JSON helpers.

Uses OpenRouter's OpenAI-compatible /chat/completions endpoint over httpx so we
have zero heavy SDK dependencies. The configured `LLM_MODELS` list is a fallback
chain: if a free model is rate-limited or errors, the next one is tried.
"""

from __future__ import annotations

import json
import re

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import logger


class LLMError(RuntimeError):
    """Transient/unknown failure — worth retrying on the same model."""


class ModelUnavailable(LLMError):
    """Model is rate-limited or gone. Do NOT retry it; fail over to the next model now."""


class LLMClient:
    def __init__(self, models: list[str] | None = None) -> None:
        self.models = models or settings.llm_model_chain
        if not settings.openrouter_api_key:
            logger.warning("OPENROUTER_API_KEY is empty — LLM calls will fail until configured.")

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        # ModelUnavailable is excluded on purpose: a 429/404 means this model won't work now,
        # so retrying it just burns seconds. We fail over to the next model in the chain instead.
        retry=retry_if_exception_type((httpx.HTTPError, LLMError))
        & retry_if_not_exception_type(ModelUnavailable),
    )
    def _call_model(self, model: str, messages: list[dict], temperature: float) -> str:
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            # Optional attribution headers recommended by OpenRouter.
            "HTTP-Referer": "https://github.com/yt-automation",
            "X-Title": "YT Automation",
        }
        payload = {"model": model, "messages": messages, "temperature": temperature}
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            resp = client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
        if resp.status_code in (429, 404, 402):
            raise ModelUnavailable(f"{model} -> {resp.status_code} (failing over)")
        if resp.status_code >= 400:
            raise LLMError(f"{model} -> {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"malformed response from {model}: {data}") from e
        # Some free models return a null content body (reasoning-only responses).
        # Treat that as this model being unusable rather than as a hard failure.
        if not content or not content.strip():
            raise ModelUnavailable(f"{model} returned empty content (failing over)")
        return content

    def chat(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        temp = settings.llm_temperature if temperature is None else temperature

        last_err: Exception | None = None
        for model in self.models:
            try:
                logger.debug("LLM call model={}", model)
                return self._call_model(model, messages, temp).strip()
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("model {} failed ({}); trying next", model, e)
        raise LLMError(f"all models failed: {last_err}")

    def chat_json(self, prompt: str, system: str | None = None) -> dict | list:
        """Chat and coerce the reply into JSON, tolerating markdown fences."""
        sys = (system or "") + "\nRespond ONLY with valid minified JSON. No prose, no code fences."
        raw = self.chat(prompt, system=sys.strip(), temperature=0.3)
        return _extract_json(raw)


def _extract_json(text: str) -> dict | list:
    text = text.strip()
    # strip ```json ... ``` fences if the model added them anyway
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # last resort: grab the first {...} or [...] blob
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
