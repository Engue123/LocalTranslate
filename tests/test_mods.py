"""P5b — player-mod detection + include/exclude."""
from pathlib import Path

import pytest

from core.renpy_ast.mods import classify_mod, is_mod
from core.engine import TranslationEngine
from core.translator import MockTranslator


@pytest.mark.parametrize("name,expected", [
    ("1mod.rpy", "mod"),
    ("game/mod/mod.rpy", "mod"),
    ("1NewCheatMode.rpy", "cheat"),
    ("941409_unlock_gallery.rpy", "gallery unlock"),
    ("scripts_engine/mod/walkthrough.rpy", "walkthrough"),
    ("~dirtyConsole.rpy", "console"),
    ("urm.rpy", "mod"),
])
def test_classify_mod_hits(name, expected):
    assert classify_mod(name) == expected


@pytest.mark.parametrize("name", [
    "script.rpy", "options.rpy", "scripts/base/gallery.rpy",
    "scripts_engine/settings/photo_mode.rpy", "screens.rpy",
])
def test_classify_mod_legit_files_not_flagged(name):
    assert classify_mod(name) is None
    assert not is_mod(name)


def _game_with_mod(tmp_path):
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text('label start:\n    e "Hello."\n', encoding="utf-8")
    (game / "cheat_menu.rpy").write_text('label cheats:\n    e "Cheats on."\n', encoding="utf-8")
    return tmp_path / "src"


def _fr(out):
    return out / "game" / "tl" / "fr"


def test_engine_translates_mods_by_default(tmp_path):
    src = _game_with_mod(tmp_path)
    out = tmp_path / "out"
    TranslationEngine(source_dir=src, output_dir=out, target_lang="fr").run(
        translator=MockTranslator())
    assert (_fr(out) / "script.rpy").exists()
    assert (_fr(out) / "cheat_menu.rpy").exists()      # mod translated too


def test_engine_excludes_mods_when_requested(tmp_path):
    src = _game_with_mod(tmp_path)
    out = tmp_path / "out"
    TranslationEngine(source_dir=src, output_dir=out, target_lang="fr").run(
        translator=MockTranslator(), exclude_mods=True)
    assert (_fr(out) / "script.rpy").exists()
    assert not (_fr(out) / "cheat_menu.rpy").exists()  # mod skipped


def test_structure_report_lists_mods(tmp_path):
    from core.renpy_ast.structure import analyze_game
    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text('label start:\n    e "Hi."\n', encoding="utf-8")
    (game / "walkthrough.rpy").write_text('label wt:\n    e "Hint."\n', encoding="utf-8")
    report = analyze_game(tmp_path)
    mod_names = {rel for rel, _cat in report.mods}
    assert "walkthrough.rpy" in mod_names
    assert any("walkthrough" in line.lower() or "Mods" in line for line in report.summary_lines())
