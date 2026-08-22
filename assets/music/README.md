# Background music beds

One folder per content type. At video-assembly time the pipeline picks a random
track from `assets/music/<content_type>/`, loops it under the voiceover at
`MUSIC_VOLUME` (default 0.12), and stops it exactly when the narration ends.

An empty (or missing) folder means that content type ships voiceover-only —
music is always optional, never a failure.

```
assets/music/
  news/
    tech_pulse.mp3    synthesized — neutral forward-motion bed
  dev_humor/
    lofi_pad.mp3      synthesized — warm Dm9 lo-fi pad
  code_heartbreak/
    ambient_pad.wav   synthesized placeholder — replace with a real sad piano track
```

Every track here is synthesized by `python scripts/make_music_beds.py`, which is
also where the sound design lives if you want to tweak it. They are deliberately
plain: at 12% volume under narration a bed's job is to keep the gaps between
sentences from sounding like the audio dropped out, not to be noticed. Synthesized
rather than downloaded means none of them can ever draw a Content ID claim.

Replacing them with real music is still an upgrade — see below. The generator
skips any file that already exists, so a real track you drop in survives a rerun
(`--force` overwrites).

## Adding real tracks

Use music you have the right to monetize on YouTube:

- **YouTube Audio Library** (Creator Studio → Audio Library) — free, safe for
  monetized videos. Filter by mood: "Sad", "Calm". Download MP3s straight into
  the folder.
- Formats accepted: `.mp3`, `.wav`, `.m4a`, `.ogg`.
- 30-60s is plenty — tracks loop automatically.
- Several tracks per folder = automatic variety (one is picked at random per video).

Do NOT use commercial songs (even "sad piano covers") — Content ID will claim
or block the videos, which defeats the channel's monetization goal.
