"""Preflight check: verify the OpenRouter key and which models in the chain respond.

Usage:  python scripts/check_llm.py
Exits non-zero if no model in LLM_MODELS is usable.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if not env_file.exists():
        sys.exit("No .env found. Copy .env.example to .env first.")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.split("#")[0].strip()
    return env


def main() -> int:
    env = load_env()
    api_key = env.get("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")
    base = env.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    models = [m.strip() for m in env.get("LLM_MODELS", "").split(",") if m.strip()]

    if not api_key:
        sys.exit("OPENROUTER_API_KEY is empty in .env")
    if not models:
        sys.exit("LLM_MODELS is empty in .env")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # 1. Key validity + remaining credit/limits
    print("Checking API key...")
    with httpx.Client(timeout=30) as client:
        r = client.get(f"{base}/auth/key", headers=headers)
        if r.status_code != 200:
            sys.exit(f"  key rejected ({r.status_code}): {r.text[:200]}")
        data = r.json().get("data", {})
        print(f"  key OK | label={data.get('label')!r} "
              f"usage={data.get('usage')} limit={data.get('limit')} "
              f"free_tier={data.get('is_free_tier')}")

    # 2. Round-trip each model in the chain
    print(f"\nTesting {len(models)} model(s) in the fallback chain:\n")
    working = []
    for model in models:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 10,
        }
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=90) as client:
                r = client.post(f"{base}/chat/completions", headers=headers, json=payload)
            elapsed = time.perf_counter() - started
            if r.status_code == 200:
                reply = r.json()["choices"][0]["message"]["content"].strip()
                print(f"  [PASS] {model}\n         {elapsed:.1f}s  reply={reply[:40]!r}")
                working.append(model)
            elif r.status_code == 429:
                print(f"  [RATE] {model}\n         rate-limited right now (still a valid fallback)")
            elif r.status_code == 404:
                print(f"  [GONE] {model}\n         model id not found — replace it in .env")
            else:
                print(f"  [FAIL] {model}\n         {r.status_code}: {r.text[:120]}")
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL] {model}\n         {type(e).__name__}: {e}")

    print(f"\n{len(working)}/{len(models)} models responding.")
    if not working:
        print("No usable model. Check live free ids: https://openrouter.ai/models?max_price=0")
        return 1
    print(f"Primary model will be: {working[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
