"""
End-to-end: compiled .rpyc -> AST extraction -> Mode A generation must emit the
EXACT engine identifiers (so Ren'Py actually binds the translations).
"""
import zlib
from pathlib import Path

from core.renpy_ast.loader import ast_classes, _bootstrap
from plugins.extractors.renpy import RenPyExtractor
from plugins.generators.renpy import RenPyGenerator, get_dialogue_id


def _make_say(who, what):
    Say = ast_classes()["Say"]
    s = Say.__new__(Say)
    s.who = who; s.what = what
    s.with_ = None; s.interact = True
    s.attributes = None; s.temporary_attributes = None
    s.arguments = None; s.identifier = None; s.explicit_identifier = False
    s.linenumber = 0
    return s


def _make_label(name, block):
    Label = ast_classes()["Label"]
    lab = Label.__new__(Label)
    lab.__dict__["name"] = name
    lab.block = block; lab.parameters = None; lab.hide = False; lab.linenumber = 0
    return lab


def _make_menu(items):
    Menu = ast_classes()["Menu"]
    m = Menu.__new__(Menu)
    m.items = items; m.has_caption = False; m.with_ = None; m.set = None
    m.arguments = None; m.item_arguments = None; m.linenumber = 0
    return m


def _make_rpyc_bytes(stmts):
    rc = _bootstrap()
    return zlib.compress(rc.pickle_safe_dumps((None, stmts)))


def test_extract_rpyc_carries_exact_identifier():
    rpyc = _make_rpyc_bytes([_make_label("start", [
        _make_say(None, "*RING RING*"),
        _make_say("MCT", "Fucking Mark."),
    ])])
    units = RenPyExtractor().extract_rpyc(rpyc, Path("script.rpy"))
    ids = {get_dialogue_id(u) for u in units}
    assert "start_c15ca220" in ids
    assert "start_302cace2" in ids


def test_menu_choices_go_to_strings_block(tmp_path):
    rpyc = _make_rpyc_bytes([_make_label("start", [
        _make_say(None, "What do you do?"),
        _make_menu([
            ("Enter.", "True", [_make_say("e", "You enter.")]),
            ("Knock Again.", "True", []),
        ]),
    ])])
    units = RenPyExtractor().extract_rpyc(rpyc, Path("script.rpy"))

    from core.models import UnitType
    menu_texts = {u.original_text for u in units if u.unit_type == UnitType.MENU}
    assert menu_texts == {"Enter.", "Knock Again."}

    for u in units:
        u.translated_text = "TR:" + u.original_text
    out = tmp_path / "out"
    RenPyGenerator().generate(
        translated_units=units, source_dir=tmp_path / "src",
        output_dir=out, target_lang="fr", mode="A",
    )
    gen = (out / "game" / "tl" / "fr" / "script.rpy").read_text(encoding="utf-8")
    # dialogue consequence present as a translate <id> block
    assert "translate fr start_" in gen
    # menu choices are GLOBAL string translations → consolidated, deduped file
    strings = (out / "game" / "tl" / "fr" / "localtranslate_strings.rpy").read_text(encoding="utf-8")
    assert "translate fr strings:" in strings
    assert 'old "Enter."' in strings
    assert 'old "Knock Again."' in strings


def test_mode_a_output_uses_exact_identifier(tmp_path):
    rpyc = _make_rpyc_bytes([_make_label("start", [
        _make_say("MCT", "Fucking Mark."),
    ])])
    units = RenPyExtractor().extract_rpyc(rpyc, Path("script.rpy"))
    for u in units:
        u.translated_text = "Fanculo Mark."

    out = tmp_path / "out"
    RenPyGenerator().generate(
        translated_units=units, source_dir=tmp_path / "src",
        output_dir=out, target_lang="it", mode="A",
    )

    generated = (out / "game" / "tl" / "it" / "script.rpy").read_text(encoding="utf-8")
    assert "translate it start_302cace2:" in generated
    assert "Fanculo Mark." in generated
    assert "MCT" in generated
