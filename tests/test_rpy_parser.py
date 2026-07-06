"""P6 — faithful .rpy text parser → exact dialogue identifiers."""
from pathlib import Path

from core.renpy_ast.rpy_parser import parse_rpy_dialogue, _strip_comment
from core.models import UnitType


def test_matches_real_game_identifiers():
    # Same oracle as the AST tests (Guilty Pleasure, label `start`).
    text = 'label start:\n    "*RING RING*"\n    MCT "Fucking Mark."\n'
    ids = {u.identifier for u in parse_rpy_dialogue(text)}
    assert "start_c15ca220" in ids      # narrator
    assert "start_302cace2" in ids      # MCT "Fucking Mark."


def test_label_with_trailing_comment_is_tracked():
    text = 'label intro1: #meet\n    e "Hi."\nlabel intro1_jasmina: #jasmina\n    e "Yo."\n'
    units = parse_rpy_dialogue(text)
    labels = {u.what: u.label for u in units}
    assert labels["Hi."] == "intro1"
    assert labels["Yo."] == "intro1_jasmina"


def test_string_who_form():
    text = 'label start:\n    "" "Empty-name line."\n    "Sylvie" "Named line."\n'
    units = parse_rpy_dialogue(text)
    whats = {u.what for u in units}
    assert "Empty-name line." in whats
    assert "Named line." in whats


def test_skips_image_python_screen_blocks():
    text = (
        'image anim:\n    "frame_0001"\n    "frame_0002"\n'
        'init python:\n    x = "not dialogue"\n'
        'screen hud():\n    text "Score"\n'
        'label start:\n    e "Real line."\n'
    )
    whats = {u.what for u in parse_rpy_dialogue(text)}
    assert whats == {"Real line."}      # only the real say


def test_character_attributes():
    # `e happy "..."` -> who=e, attribute happy; must still produce one say.
    units = parse_rpy_dialogue('label start:\n    e happy "Hello!"\n')
    assert len(units) == 1
    assert units[0].who == "e" and units[0].what == "Hello!"


def test_duplicate_lines_unique_ids():
    units = parse_rpy_dialogue('label start:\n    e "Same."\n    e "Same."\n')
    assert len(units) == 2
    assert units[1].identifier == units[0].identifier + "_1"


def test_strip_comment_respects_strings():
    assert _strip_comment('text "#fff" # real comment') == 'text "#fff"'
    assert _strip_comment('e "hi"') == 'e "hi"'


def test_extract_rpy_carries_renpy_id(tmp_path):
    from plugins.extractors.renpy import RenPyExtractor
    game = tmp_path / "game"
    game.mkdir()
    f = game / "script.rpy"
    f.write_text('label start:\n    e "Hello there."\n', encoding="utf-8")
    units = RenPyExtractor().extract_rpy(f, Path("script.rpy"))
    dlg = [u for u in units if u.unit_type == UnitType.DIALOGUE]
    assert dlg and dlg[0].metadata.get("renpy_id", "").startswith("start_")
