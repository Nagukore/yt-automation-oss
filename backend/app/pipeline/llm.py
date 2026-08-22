"""OpenRouter chat client with a model fallback chain and JSON helpers.

Uses OpenRouter's OpenAI-compatible /chat/completions endpoint over httpx so we
have zero heavy SDK dependencies. The configured `LLM_MODELS` list is a fallback
chain: if a free model is rate-limited or errors, the next one is tried.
"""

from __future__ import annotations

import json
import re
import time

import httpx
from langsmith import traceable
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import logger
from app.core.tracing import tag_current_run


class LLMError(RuntimeError):
    """Transient/unknown failure — worth retrying on the same model."""


class ModelUnavailable(LLMError):
    """Model is rate-limited or gone. Do NOT retry it; fail over to the next model now."""


class LLMClient:
    def __init__(self, models: list[str] | None = None, provider: str | None = None) -> None:
        # Provider chosen per-process from settings (Shorts -> openrouter,
        # long-form -> gemini). Both speak the same OpenAI-compatible protocol, so
        # only the base URL, key, model chain and attribution headers differ.
        provider = (provider or settings.llm_provider or "openrouter").lower()
        self.provider = provider
        if provider == "gemini":
            self.api_key = settings.gemini_api_key
            self.base_url = settings.gemini_base_url
            self.models = models or settings.gemini_model_chain
            self.extra_headers: dict[str, str] = {}
            key_env = "GEMINI_API_KEY"
        else:
            self.api_key = settings.openrouter_api_key
            self.base_url = settings.openrouter_base_url
            self.models = models or settings.llm_model_chain
            # Attribution headers OpenRouter recommends; harmless elsewhere.
            self.extra_headers = {
                "HTTP-Referer": "https://github.com/yt-automation",
                "X-Title": "YT Automation",
            }
            key_env = "OPENROUTER_API_KEY"
        if not self.api_key:
            logger.warning("{} is empty — LLM calls will fail until configured.", key_env)

    # run_type="llm" is what makes LangSmith render this as a model call — with the
    # messages, the completion and the latency — rather than an opaque function span.
    # It sits *inside* the retry so each attempt is its own span: a model that had to
    # be retried twice before answering is exactly what you go to a trace to find out.
    # A no-op call when tracing is off.
    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        # ModelUnavailable is excluded on purpose: a 429/404 means this model won't work now,
        # so retrying it just burns seconds. We fail over to the next model in the chain instead.
        retry=retry_if_exception_type((httpx.HTTPError, LLMError))
        & retry_if_not_exception_type(ModelUnavailable),
    )
    @traceable(run_type="llm", name="chat_completion")
    def _call_model(self, model: str, messages: list[dict], temperature: float) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        payload = {"model": model, "messages": messages, "temperature": temperature}
        budget = settings.llm_timeout_seconds
        deadline = time.monotonic() + budget
        # Streamed on purpose. A plain .post() only bounds each individual socket
        # read, and OpenRouter keeps a queued free-model request alive by trickling
        # bytes — so the per-read timeout never fires and the call runs unbounded.
        # Reading chunk by chunk lets us abandon it against a real clock.
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                status = resp.status_code
                if status in (429, 404, 402):
                    raise ModelUnavailable(f"{model} -> {status} (failing over)")
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body += chunk
                    if time.monotonic() > deadline:
                        # Treated as unavailable, not a transient error: retrying a
                        # model that just ate the full budget would spend it twice.
                        raise ModelUnavailable(
                            f"{model} exceeded {budget}s wall clock (failing over)"
                        )
        if status >= 400:
            raise LLMError(f"{model} -> {status}: {body[:300].decode(errors='replace')}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise LLMError(f"non-JSON response from {model}: {bytes(body[:200])!r}") from e
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"malformed response from {model}: {data}") from e
        # Some free models return a null content body (reasoning-only responses).
        # Treat that as this model being unusable rather than as a hard failure.
        if not content or not content.strip():
            raise ModelUnavailable(f"{model} returned empty content (failing over)")
        return content

    @traceable(run_type="chain", name="llm_chat")
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

        # Per-minute 429s on :free models hit every model at once when the pipeline
        # fires calls back-to-back, so one pass over the chain can dead-end even
        # though a short wait would succeed. Sweep the chain a few times with a
        # pause between passes before giving up.
        #
        # Per-request timeouts alone don't bound this loop — 4 models x 3 sweeps is
        # 12 chances to spend the full timeout. Stop sweeping once the whole call has
        # outlived its budget so one bad stage can't consume the CI job's hour.
        budget = settings.llm_chat_budget_seconds
        deadline = time.monotonic() + budget
        last_err: Exception | None = None
        # Which models were burned through before one answered. The whole reason to
        # trace this call is to see that the "free" chain cost three failovers today.
        failovers: list[str] = []
        for sweep, pause in enumerate((0, 20, 60)):
            if pause:
                if time.monotonic() + pause > deadline:
                    break
                logger.warning("all models busy; sweep {} retrying in {}s", sweep + 1, pause)
                time.sleep(pause)
            for model in self.models:
                if time.monotonic() > deadline:
                    logger.warning("chat budget of {}s exhausted; giving up", budget)
                    tag_current_run(outcome="budget_exhausted", failovers=failovers, sweeps=sweep)
                    raise LLMError(f"budget of {budget}s exhausted; last error: {last_err}")
                try:
                    logger.debug("LLM call model={}", model)
                    out = self._call_model(model, messages, temp).strip()
                    tag_current_run(
                        provider=self.provider,
                        model=model,
                        temperature=temp,
                        sweeps=sweep,
                        failovers=failovers,
                    )
                    return out
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    failovers.append(f"{model}: {type(e).__name__}")
                    logger.warning("model {} failed ({}); trying next", model, e)
        tag_current_run(outcome="all_models_failed", failovers=failovers)
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
        logger.info(
            "LLM client: provider={} models={}", _client.provider, _client.models
        )
    return _client
