# Caption fonts

Fonts committed here are loaded directly by the caption renderer, so a video looks
the same wherever it is built.

That matters because production renders do **not** happen on a developer machine —
they run on `ubuntu-latest` GitHub Actions runners, which share almost no fonts with
Windows or macOS. Without a bundled face, libass silently substitutes whatever
fontconfig picks, and the captions you reviewed locally are not the captions your
channel publishes.

## How they are used

- `app.media.subtitles.animated.resolve_face` looks here first (see
  `settings.caption_font_dir`, default `./assets/fonts`) before falling back to
  system fonts. The chain ends at DejaVu Sans, which is always present on Ubuntu,
  so a missing file degrades the look but never breaks a render.
- The same file is used both to measure phrase widths and, via FFmpeg's
  `subtitles=...:fontsdir=`, to draw them — so libass and the width check can never
  disagree about which face is being laid out.
- Fonts do **not** need to be installed on the system. `fontsdir` is enough.

## Adding a font

Only add faces you are licensed to redistribute — the SIL Open Font License (OFL)
and Apache 2.0 both allow it. Commit the license file alongside the font, then add
the `(family name, filename)` pair to the relevant chain in `animated.py`. The
family name must match the font's internal name, which you can check with:

```python
from PIL import ImageFont
print(ImageFont.truetype("assets/fonts/Anton-Regular.ttf", 40).getname())
```

## Contents

| File | Family | License |
| --- | --- | --- |
| `Anton-Regular.ttf` | Anton | SIL Open Font License 1.1 (`OFL.txt`) |
