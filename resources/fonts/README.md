# Bundled fallback fonts

The font layer (`core/fonts.py`) ships a covering font into each patch when the
game's own font can't draw the target language. Priority: a font already in the
game → a font here → warn.

- **DejaVuSans.ttf** — full Latin (accents) + Cyrillic + Greek. Covers fr/es/it/de/pt/ru.
  (Every Ren'Py game already bundles this in `renpy/common/`, so it is rarely needed
  from here; kept as a guaranteed fallback.) License: Bitstream Vera / permissive.

## CJK (ja / zh / ko) — drop-in required

No redistributable CJK font is bundled by default (size). To enable CJK font
adaptation, download **Noto Sans CJK** (SIL OFL, free) and place a Regular file here:

    NotoSansCJK-Regular.ttc      (all of JP/KR/SC/TC, ~40 MB)
    # or per-region:
    NotoSansJP-Regular.otf / NotoSansKR-Regular.otf / NotoSansSC-Regular.otf

Source: https://github.com/notofonts/noto-cjk/releases

Without it, a CJK patch still generates, but the tool warns that no covering font
was found (the text would render as tofu until a CJK font is provided in-game).
