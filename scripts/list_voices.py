"""List available edge-tts voices to pick EDGE_TTS_VOICE for .env.

Usage:
    python scripts/list_voices.py            # all en-* voices
    python scripts/list_voices.py en-GB      # filter by locale prefix
    python scripts/list_voices.py all        # every voice, all languages
"""

from __future__ import annotations

import asyncio
import sys


async def main() -> None:
    import edge_tts

    prefix = sys.argv[1] if len(sys.argv) > 1 else "en-"
    if prefix == "all":
        prefix = ""

    voices = await edge_tts.list_voices()
    rows = sorted(
        (v for v in voices if v["ShortName"].startswith(prefix)),
        key=lambda v: v["ShortName"],
    )
    if not rows:
        print(f"No voices matching {prefix!r}. Try: python scripts/list_voices.py all")
        return

    print(f"{len(rows)} voice(s) matching {prefix!r}:\n")
    print(f"{'ShortName':<34}{'Gender':<9}{'Personalities'}")
    print("-" * 78)
    for v in rows:
        tags = ", ".join(v.get("VoiceTag", {}).get("VoicePersonalities", []) or [])
        print(f"{v['ShortName']:<34}{v['Gender']:<9}{tags}")

    print("\nSet your choice in .env, e.g.:  EDGE_TTS_VOICE=en-US-AriaNeural")


if __name__ == "__main__":
    asyncio.run(main())
