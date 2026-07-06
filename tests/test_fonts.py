"""Font adaptation layer: coverage detection, declared-font parsing, replacement
choice, and override generation. Test fonts are synthesized in-memory (fontTools)
so nothing depends on external font files."""
from io import BytesIO
from pathlib import Path

import pytest

from core.fonts import (
    REQUIRED_GLYPHS, font_missing_glyphs, font_covers,
    collect_declared_fonts, choose_replacement, build_font_override,
    apply_font_fix,
)


def _make_font(codepoints) -> bytes:
    """A minimal valid TTF whose cmap covers exactly `codepoints`."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    names = [".notdef"] + [f"g{cp:04x}" for cp in codepoints]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({cp: f"g{cp:04x}" for cp in codepoints})
    empty = TTGlyphPen(None).glyph()
    fb.setupGlyf({n: empty for n in names})
    fb.setupHorizontalMetrics({n: (500, 0) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    buf = BytesIO()
    fb.save(buf)
    return buf.getvalue()


ASCII = [ord(c) for c in "ABCabc .,!?"]
FR_FULL = ASCII + [ord(c) for c in REQUIRED_GLYPHS["fr"]]
RU_FULL = ASCII + [ord(c) for c in REQUIRED_GLYPHS["ru"]]


# -- coverage ----------------------------------------------------------------

def test_font_missing_glyphs_flags_a_latin_only_font():
    missing = font_missing_glyphs(_make_font(ASCII), "fr")
    assert "é" in missing and "à" in missing            # accents absent
    assert font_covers(_make_font(ASCII), "fr") is False


def test_font_covers_when_glyphs_present():
    assert font_missing_glyphs(_make_font(FR_FULL), "fr") == []
    assert font_covers(_make_font(FR_FULL), "fr") is True


def test_coverage_is_per_language():
    latin = _make_font(FR_FULL)
    assert font_covers(latin, "fr") is True
    assert font_covers(latin, "ru") is False            # no Cyrillic in a Latin font
    assert font_covers(_make_font(RU_FULL), "ru") is True


def test_unreadable_bytes_return_none_not_crash():
    # None = "can't tell" -> caller must NOT touch the patch (never break on a guess)
    assert font_missing_glyphs(b"not a font", "fr") is None


# -- declared-font parsing ---------------------------------------------------

ETERNUM_GUI = '''\
define gui.text_font = "ade1.ttf"
define gui.name_text_font = "Sketchzone.ttf"
define gui.interface_text_font = "poiretone.ttf"
define gui.button_text_font = gui.interface_text_font
define gui.choice_button_text_font = gui.text_font
'''


def test_collect_declared_fonts_reads_gui_and_resolves_aliases():
    fonts = collect_declared_fonts(ETERNUM_GUI)
    assert set(fonts) == {"ade1.ttf", "Sketchzone.ttf", "poiretone.ttf"}


def test_collect_declared_fonts_catches_inline_font_tags():
    # {font=x} tags embedded in dialogue TEXT (the Eternum "blank accents in
    # memories" gap — fonts used this way bypass gui.*_font entirely).
    text = ('label l:\n'
            '    "{i}{font=tales5.ttf}{size=45}A legend from long ago."\n'
            '    "{font=chinafont.ttf}some styled text"\n')
    fonts = collect_declared_fonts(text)
    assert "tales5.ttf" in fonts and "chinafont.ttf" in fonts


def test_collect_declared_fonts_includes_inline_and_dedupes():
    text = ETERNUM_GUI + '\n    textbutton "x" text_font "poiretone.ttf"\n    text "y" font "extra.otf"\n'
    fonts = collect_declared_fonts(text)
    assert "extra.otf" in fonts
    assert fonts.count("poiretone.ttf") == 1            # not duplicated


# -- replacement choice ------------------------------------------------------

def test_choose_replacement_prefers_a_covering_game_font():
    game = {"broken.ttf": _make_font(ASCII), "good.ttf": _make_font(FR_FULL)}
    name, data = choose_replacement("fr", game)
    assert name == "good.ttf" and data == game["good.ttf"]


def test_choose_replacement_falls_back_to_bundled(tmp_path):
    (tmp_path / "DejaVuSans.ttf").write_bytes(_make_font(FR_FULL))
    game = {"broken.ttf": _make_font(ASCII)}            # nothing in-game covers fr
    name, data = choose_replacement("fr", game, bundled_dir=tmp_path)
    assert name == "DejaVuSans.ttf"


def test_choose_replacement_none_when_nothing_covers(tmp_path):
    game = {"broken.ttf": _make_font(ASCII)}
    assert choose_replacement("ja", game, bundled_dir=tmp_path) is None


# -- override generation -----------------------------------------------------

def test_build_font_override_emits_replacement_map():
    rpy = build_font_override(["ade1.ttf", "ade2.ttf"], "DejaVuSans.ttf", "fr")
    assert "config.font_replacement_map" in rpy
    assert 'fonts/DejaVuSans.ttf' in rpy
    assert '"ade1.ttf"' in rpy and '"ade2.ttf"' in rpy
    assert "init 1000 python:" in rpy
    # MUST map bold AND italic variants — italic narration ({i}…) looks up the
    # (font, False, True) key; mapping only regular left italic accents blank.
    assert "for _lt_b in (False, True)" in rpy
    assert "for _lt_i in (False, True)" in rpy


# -- end-to-end apply_font_fix -----------------------------------------------

def _game(tmp_path, gui_font, fonts: dict):
    """A minimal game/: a gui.rpy declaring `gui_font`, plus the given font files."""
    game = tmp_path / "game"
    game.mkdir()
    (game / "gui.rpy").write_text(f'define gui.text_font = "{gui_font}"\n', encoding="utf-8")
    for name, data in fonts.items():
        (game / name).write_bytes(data)
    return game


def test_apply_font_fix_redirects_insufficient_font(tmp_path):
    game = _game(tmp_path, "broken.ttf",
                 {"broken.ttf": _make_font(ASCII), "good.ttf": _make_font(FR_FULL)})
    out = tmp_path / "out"
    repl = apply_font_fix(game, out, "fr")
    assert repl == "good.ttf"                                   # in-game covering font used
    assert (out / "game" / "tl" / "fr" / "fonts" / "good.ttf").exists()
    override = (out / "game" / "tl" / "fr" / "localtranslate_fonts.rpy").read_text()
    assert "config.font_replacement_map" in override and '"broken.ttf"' in override


def test_apply_font_fix_noop_when_game_font_already_covers(tmp_path):
    game = _game(tmp_path, "ok.ttf", {"ok.ttf": _make_font(FR_FULL)})
    out = tmp_path / "out"
    assert apply_font_fix(game, out, "fr") is None
    assert not (out / "game" / "tl" / "fr" / "localtranslate_fonts.rpy").exists()


def test_apply_font_fix_warns_when_nothing_covers(tmp_path):
    game = _game(tmp_path, "broken.ttf", {"broken.ttf": _make_font(ASCII)})
    warnings = []
    repl = apply_font_fix(game, tmp_path / "out", "ja", warn=warnings.append)
    assert repl is None
    assert warnings and "ja" in warnings[0]                    # honest warning, no silent tofu
