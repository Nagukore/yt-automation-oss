"""Synthesize the background-music beds in assets/music/<content_type>/.

These exist because the alternative was silence. Two of the three streams shipped
voiceover over a dead track for their first hundred videos, and a bed — even a
plain one — is most of the difference between "a video" and "a text-to-speech
clip". Real music is still better: see assets/music/README.md for the YouTube
Audio Library route, and just overwrite these files.

Synthesized rather than downloaded so the provenance is in the repo: nothing here
can ever draw a Content ID claim, which is the one failure mode that would cost
the channel money instead of just sounding worse.

Everything is built to loop seamlessly. FFmpeg concatenates the file end-to-end
(`-stream_loop -1`), so any partial left mid-cycle at the boundary is a click on
every repeat. Every frequency is therefore quantized to complete a whole number of
cycles across the loop, and every envelope is periodic over it.

    python scripts/make_music_beds.py [--force]

Idempotent: existing files are left alone unless --force is passed, so a real
track dropped into a folder is never overwritten by a rerun.
"""

from __future__ import annotations

import argparse
import math
import wave
from pathlib import Path

import numpy as np

RATE = 44_100
SECONDS = 32.0
ASSETS = Path(__file__).resolve().parent.parent / "assets" / "music"


def _t() -> np.ndarray:
    return np.arange(int(RATE * SECONDS), dtype=np.float64) / RATE


def _snap(freq: float) -> float:
    """Nearest frequency completing a whole number of cycles across the loop.

    At a 32s loop the grid is 0.031 Hz, so the shift is far below the ~0.3%
    threshold where a pitch change is audible — but it is exactly what makes the
    waveform meet itself at the seam.
    """
    return max(1, round(freq * SECONDS)) / SECONDS


def _osc(t: np.ndarray, freq: float, phase: float = 0.0, harmonics: int = 1) -> np.ndarray:
    """Additive tone: a snapped fundamental plus `harmonics` decaying partials."""
    out = np.zeros_like(t)
    for h in range(1, harmonics + 1):
        f = _snap(freq * h)
        if f > RATE / 2:
            break
        out += np.sin(2 * np.pi * f * t + phase) / (h * h)
    return out


def _lfo(t: np.ndarray, cycles: int, depth: float, floor: float = 0.0) -> np.ndarray:
    """Amplitude envelope completing `cycles` whole periods across the loop."""
    return floor + depth * (0.5 + 0.5 * np.sin(2 * np.pi * cycles * t / SECONDS - np.pi / 2))


def _looped_noise(t: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """Band-limited noise built from summed sine partials, so it loops exactly.

    Filtering white noise would leave the tail unrelated to the head; summing
    harmonics of the loop frequency with random phases gives the same hiss and
    meets itself at the seam by construction.
    """
    rng = np.random.default_rng(7)
    out = np.zeros_like(t)
    base = 1.0 / SECONDS
    n = int(cutoff_hz / base)
    # Sparse partials: a few hundred is plenty of density for an air layer and
    # keeps this from taking a minute to build.
    for k in rng.choice(np.arange(40, n), size=min(400, n - 40), replace=False):
        out += np.sin(2 * np.pi * k * base * t + rng.uniform(0, 2 * np.pi)) / math.sqrt(k)
    return out / (np.max(np.abs(out)) or 1.0)


def news_bed() -> np.ndarray:
    """Neutral forward-motion tech bed: sub pulse, open fifth, a breath of air.

    Deliberately featureless. It plays under AI-news narration at 12% volume and
    its whole job is to stop the gaps between sentences from sounding like the
    audio dropped out.
    """
    t = _t()
    # 8 pulses across the loop = 15 BPM felt as a slow swell, not a beat. A real
    # rhythm would fight the narration's own cadence.
    pulse = _lfo(t, cycles=8, depth=0.7, floor=0.3)
    sub = _osc(t, 110.0, harmonics=2) * pulse * 0.5           # A2
    fifth = _osc(t, 164.81, harmonics=3) * _lfo(t, 2, 0.5, 0.5) * 0.22  # E3
    octave = _osc(t, 220.0, harmonics=2) * _lfo(t, 3, 0.6, 0.2) * 0.10  # A3
    air = _looped_noise(t, 6000) * _lfo(t, 1, 0.5, 0.3) * 0.05
    return sub + fifth + octave + air


def dev_humor_bed() -> np.ndarray:
    """Warm lo-fi Dm9 pad — mellow, a little wistful, no rhythm to trip on.

    The humor stream is bittersweet comedy, so the bed leans warm and slightly sad
    rather than upbeat; an upbeat bed under a heartbreak-metaphor punchline reads
    as sarcasm.
    """
    t = _t()
    swell = _lfo(t, cycles=2, depth=0.55, floor=0.45)
    # D3 F3 A3 C4 E4 — a minor ninth, voiced open so it stays soft under speech.
    voices = ((146.83, 0.34), (174.61, 0.26), (220.0, 0.20), (261.63, 0.15), (329.63, 0.09))
    pad = np.zeros_like(t)
    for i, (freq, gain) in enumerate(voices):
        # Each voice drifts on its own slow cycle so the chord breathes instead of
        # sitting as one static block.
        pad += _osc(t, freq, phase=i * 1.1, harmonics=4) * _lfo(t, i + 1, 0.35, 0.65) * gain
    hiss = _looped_noise(t, 4000) * 0.035  # the tape floor that makes it read lo-fi
    return pad * swell + hiss


def _stereo(mono: np.ndarray, spread: float = 0.012) -> np.ndarray:
    """Widen to stereo by delaying one side a few milliseconds (Haas effect).

    np.roll wraps the tail back to the head, which is exactly right here: the file
    loops, so the wrapped samples are the ones that genuinely precede it.
    """
    delay = int(RATE * spread)
    return np.stack([mono, np.roll(mono, delay)], axis=-1)


def _ffmpeg_exe() -> str | None:
    """Same lookup the render path uses: system FFmpeg, else the pip-installed one."""
    import shutil

    if found := shutil.which("ffmpeg"):
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def _encode_mp3(wav: Path, mp3: Path) -> bool:
    """Transcode to MP3 and drop the WAV.

    32s of 44.1k stereo PCM is 5.6MB, and CI checks this repo out on every run.
    At 192kbps the bed is ~750KB and the difference is inaudible under narration
    at 12% volume — especially after the whole mix is re-encoded to AAC anyway.
    """
    import subprocess

    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return False
    try:
        subprocess.run(
            [ffmpeg, "-y", "-nostdin", "-hide_banner", "-i", str(wav),
             "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return False
    wav.unlink(missing_ok=True)
    return True


def _write(path: Path, mono: np.ndarray, peak: float = 0.5) -> None:
    """Normalize to `peak`, apply a gentle soft-knee, and write the bed.

    Headroom is deliberate: the mix ducks the bed to MUSIC_VOLUME (0.12) and then
    the loudness pass sets the final level, so a hot file here would buy nothing
    and only risk intersample peaks.
    """
    mono = mono / (np.max(np.abs(mono)) or 1.0)
    mono = np.tanh(mono * 1.2) / math.tanh(1.2)  # round off the peaks, no hard clip
    stereo = _stereo(mono * peak)
    path.parent.mkdir(parents=True, exist_ok=True)
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(stereo, -1.0, 1.0) * 32767).astype("<i2").tobytes())

    written = wav
    if path.suffix == ".mp3" and _encode_mp3(wav, path):
        written = path

    seam = float(np.max(np.abs(stereo[0] - stereo[-1])))
    size_kb = round(written.stat().st_size / 1024)
    print(
        f"wrote {written.relative_to(ASSETS.parent.parent)}  "
        f"{SECONDS:.0f}s  {size_kb}KB  seam delta {seam:.4f}"
    )


BEDS = {
    "news/tech_pulse.mp3": news_bed,
    "dev_humor/lofi_pad.mp3": dev_humor_bed,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing beds (default: skip anything already there)",
    )
    args = parser.parse_args()

    for rel, build in BEDS.items():
        dest = ASSETS / rel
        if dest.exists() and not args.force:
            print(f"skip {rel} (already exists; --force to overwrite)")
            continue
        _write(dest, build())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
