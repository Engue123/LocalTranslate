"""P5a — custom-statement (UserStatement) dialogue extraction + grouping."""
from pathlib import Path

from core.renpy_ast.loader import ast_classes
from core.renpy_ast.walker import walk_dialogue, DialogueWalker
from core.models import UnitType


def _make_label(name, block):
    Label = ast_classes()["Label"]
    lab = Label.__new__(Label)
    lab.__dict__["name"] = name
    lab.block = block
    lab.parameters = None
    lab.hide = False
    lab.linenumber = 0
    return lab


def _make_us(line, translatable=True):
    US = ast_classes()["UserStatement"]
    u = US.__new__(US)
    u.line = line
    u.translatable = translatable
    u.block = []
    u.code_block = None
    u.parsed = None
    u.linenumber = 0
    return u


def test_say_like_userstatement_extracted():
    stmts = [_make_label("start", [_make_us('bardi "Hello there."')])]
    units = walk_dialogue(stmts)
    assert len(units) == 1
    assert units[0].who == "bardi"
    assert units[0].what == "Hello there."
    assert units[0].identifier.startswith("start_")


def test_consecutive_userstatements_share_one_id():
    stmts = [_make_label("start", [
        _make_us('a "First line."'),
        _make_us('b "Second line."'),
        _make_us('c "Third line."'),
    ])]
    units = walk_dialogue(stmts)
    assert len(units) == 3
    ids = {u.identifier for u in units}
    assert len(ids) == 1                       # one group, one identifier
    assert [u.what for u in units] == ["First line.", "Second line.", "Third line."]


def test_non_saylike_statement_in_group_is_skipped_safely():
    walker = DialogueWalker()
    walker._translate_dialogue([_make_label("start", [
        _make_us('a "Real dialogue."'),
        _make_us('show screen overlay()'),      # translatable but not say-like
    ])])
    # Mixed group -> skipped (don't risk dropping the non-dialogue statement).
    assert walker.units == []
    assert walker.skipped_us == 2


def test_non_translatable_userstatement_ignored():
    stmts = [_make_label("start", [_make_us('pause 1.0', translatable=False),
                                  _make_us('e "Hi."')])]
    units = walk_dialogue(stmts)
    assert len(units) == 1 and units[0].what == "Hi."


def test_generator_emits_one_block_per_shared_id(tmp_path):
    from plugins.generators.renpy import RenPyGenerator
    from core.models import TranslationUnit
    rel = Path("script.rpy")
    units = [
        TranslationUnit(rel, 1, "First.", unit_type=UnitType.DIALOGUE, character="a",
                        metadata={"renpy_id": "start_abc12345"}),
        TranslationUnit(rel, 2, "Second.", unit_type=UnitType.DIALOGUE, character="b",
                        metadata={"renpy_id": "start_abc12345"}),
    ]
    for u in units:
        u.translated_text = "TR:" + u.original_text
    out = tmp_path / "out"
    RenPyGenerator().generate(translated_units=units, source_dir=tmp_path / "src",
                              output_dir=out, target_lang="fr", mode="A")
    text = (out / "game" / "tl" / "fr" / "script.rpy").read_text(encoding="utf-8")
    # Exactly one translate block for the shared id, containing both lines.
    assert text.count("translate fr start_abc12345:") == 1
    assert 'a "TR:First."' in text
    assert 'b "TR:Second."' in text
