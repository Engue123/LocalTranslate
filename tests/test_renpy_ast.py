"""
Tests for the AST loader + dialogue walker on synthetic .rpyc data.

We build real fake-AST nodes (the same classes unrpyc unpickles into), serialize
them as a v1 `.rpyc` blob, load them back, and verify the walker reproduces
Ren'Py's identifiers — including nested labels and duplicate-line uniqueness.
"""
import zlib

import types

from core.renpy_ast.loader import ast_classes, load_ast, _bootstrap
from core.renpy_ast.walker import walk_dialogue, walk_strings, collect_character_names


def _classes():
    return ast_classes()


def make_say(who, what):
    Say = _classes()["Say"]
    s = Say.__new__(Say)
    s.who = who
    s.what = what
    s.with_ = None
    s.interact = True
    s.attributes = None
    s.temporary_attributes = None
    s.arguments = None
    s.identifier = None
    s.explicit_identifier = False
    s.linenumber = 0
    return s


def make_label(name, block):
    Label = _classes()["Label"]
    lab = Label.__new__(Label)
    lab.__dict__["name"] = name
    lab.block = block
    lab.parameters = None
    lab.hide = False
    lab.linenumber = 0
    return lab


def make_menu(items):
    """items: list of (label, condition, block-or-None)."""
    Menu = _classes()["Menu"]
    m = Menu.__new__(Menu)
    m.items = items
    m.has_caption = False
    m.with_ = None
    m.set = None
    m.arguments = None
    m.item_arguments = None
    m.linenumber = 0
    return m


def make_define(source_code):
    """A Define node whose code source is `source_code` (e.g. a Character(...))."""
    classes = _classes()
    Define = classes["Define"]
    d = Define.__new__(Define)
    PyCode = classes.get("PyCode")
    if PyCode is not None:
        code = PyCode.__new__(PyCode)
        code.source = source_code
    else:
        code = types.SimpleNamespace(source=source_code)
    d.code = code
    d.varname = "e"
    d.store = "store"
    d.linenumber = 0
    return d


def make_v1_rpyc(stmts) -> bytes:
    """Serialize statements as a v1 (.rpyc) zlib-compressed pickle blob."""
    rc = _bootstrap()
    return zlib.compress(rc.pickle_safe_dumps((None, stmts)))


def test_collect_character_names():
    stmts = [
        make_define('Character("Eileen")'),
        make_label("start", [make_define('Character(_("Protagonist"))'),
                             make_say("e", "Hi.")]),
    ]
    names = collect_character_names(stmts)
    assert "Eileen" in names
    assert "Protagonist" in names


def test_collect_character_names_with_space_and_factories():
    """Ren'Py allows a space before '(' and factory variants; we must catch both
    (real case: Nephilim writes `Character ("Eliza", who_color=…)`)."""
    stmts = [
        make_define('Character ("Eliza", who_color="#ca61ed")'),   # space before (
        make_define('DynamicCharacter ("Riven")'),                 # factory + space
        make_define('NVLCharacter("Echo")'),                       # factory, no space
    ]
    names = collect_character_names(stmts)
    assert {"Eliza", "Riven", "Echo"} <= names


def test_analyze_game_reports_coverage(tmp_path):
    from core.renpy_ast.structure import analyze_game
    game = tmp_path / "game"
    game.mkdir()
    rpyc = make_v1_rpyc([make_label("start", [
        make_say(None, "Hi."), make_say("e", "Hey there."),
    ])])
    (game / "script.rpyc").write_bytes(rpyc)

    report = analyze_game(tmp_path)
    assert report.game_dir == game
    assert report.rpyc_ok == 1 and report.rpyc_failed == 0
    assert report.dialogue_total == 2
    assert report.coverage_pct == 100
    assert "Structure Report" in report.to_markdown()
    assert any("readable" in line for line in report.summary_lines())


def test_analyze_game_flags_unreadable_rpyc(tmp_path):
    from core.renpy_ast.structure import analyze_game
    game = tmp_path / "game"
    game.mkdir()
    (game / "broken.rpyc").write_bytes(b"definitely not a valid rpyc blob")

    report = analyze_game(tmp_path)
    assert report.rpyc_failed == 1
    assert report.coverage_pct == 0
    assert any("readable" in w.lower() or "obfusc" in w.lower() for w in report.warnings)


def test_collect_glossary_from_jobs(monkeypatch):
    from core.engine import _collect_renpy_glossary
    stmts = [
        make_define('Character("Eileen")'),
        make_label("start", [make_say("e", "Hello.")]),
    ]
    # load_ast is imported inside _collect_renpy_glossary; patch at the source.
    # (the engine now calls it with try_harder=True, so accept that kwarg)
    monkeypatch.setattr("core.renpy_ast.load_ast", lambda src, try_harder=False: stmts)
    jobs = [("script.rpy", "rpyc", (b"fake-bytes", None))]
    glossary = _collect_renpy_glossary(jobs)
    assert glossary == {"Eileen": "Eileen"}


def test_walker_on_in_memory_ast():
    stmts = [make_label("start", [
        make_say(None, "*RING RING*"),
        make_say("MCT", "Fucking Mark."),
    ])]
    units = walk_dialogue(stmts)
    ids = {u.identifier for u in units}
    assert "start_c15ca220" in ids       # narrator line, real-game digest
    assert "start_302cace2" in ids       # MCT line, real-game digest
    # who/what carried through
    narr = next(u for u in units if u.who is None)
    assert narr.what == "*RING RING*"


def test_decompile_to_text_in_memory():
    from core.renpy_ast.loader import decompile_to_text
    stmts = [make_label("start", [make_say("e", "Hello there.")])]
    text = decompile_to_text(stmts)
    assert "label start:" in text
    assert "Hello there." in text


def test_loader_roundtrip_v1():
    stmts = [make_label("start", [make_say("e", "Hello.")])]
    blob = make_v1_rpyc(stmts)
    loaded = load_ast(blob)
    units = walk_dialogue(loaded)
    assert len(units) == 1
    assert units[0].who == "e"
    assert units[0].what == "Hello."
    assert units[0].identifier.startswith("start_")


def test_nested_label_tracks_innermost():
    """A say after a nested label is attributed to that nested label."""
    inner = make_label("inner", [make_say("e", "Deep line.")])
    stmts = [make_label("outer", [inner])]
    units = walk_dialogue(stmts)
    assert len(units) == 1
    assert units[0].label == "inner"
    assert units[0].identifier.startswith("inner_")


def test_duplicate_lines_get_unique_ids():
    stmts = [make_label("start", [
        make_say("e", "Same."),
        make_say("e", "Same."),
    ])]
    units = walk_dialogue(stmts)
    assert len(units) == 2
    assert units[0].identifier != units[1].identifier
    assert units[1].identifier == units[0].identifier + "_1"


def test_walk_strings_extracts_menu_choices():
    menu = make_menu([
        ("Definitely.", "True", [make_say(None, "You chose.")]),
        ("I'll do my best.", "True", []),
    ])
    stmts = [make_label("start", [menu])]
    sus = walk_strings(stmts)
    texts = {s.text for s in sus}
    assert "Definitely." in texts
    assert "I'll do my best." in texts
    assert all(s.kind == "menu" for s in sus)


def test_menu_choice_consequences_still_yield_dialogue():
    """Dialogue inside a menu choice block is still extracted with an id."""
    menu = make_menu([("Yes.", "True", [make_say("e", "Good choice.")])])
    stmts = [make_label("start", [menu])]
    units = walk_dialogue(stmts)
    assert any(u.what == "Good choice." and u.identifier.startswith("start_") for u in units)


def test_underscore_label_is_alternate_not_primary():
    """Labels starting with _ are 'alternate' and don't become the prefix."""
    stmts = [
        make_label("real", []),
        make_label("_alt", [make_say("e", "Hi.")]),
    ]
    units = walk_dialogue(stmts)
    assert len(units) == 1
    # primary label stays "real"; the _alt only reserves an alternate id
    assert units[0].label == "real"
    assert units[0].identifier.startswith("real_")


def _obfuscate_v2(blob: bytes) -> bytes:
    """Wrap a v1 blob in a v2 container whose slot 1 is base64-encoded.

    Mirrors a real-world obfuscation: a valid RENPY RPC2 shell, but the AST slot
    carries an extra base64 layer the standard loader can't peel. Recoverable
    only via the deobfuscation fallback.
    """
    import base64
    import struct
    payload = base64.b64encode(blob)
    slot2 = b"\x00"
    start1 = 10 + 12 * 3
    start2 = start1 + len(payload)
    header = (b"RENPY RPC2"
              + struct.pack("<III", 1, start1, len(payload))
              + struct.pack("<III", 2, start2, len(slot2))
              + struct.pack("<III", 0, 0, 0))
    return header + payload + slot2 + b"\x00"


def test_extractor_recovers_obfuscated_rpyc():
    """The .rpyc extractor passes try_harder=True, so an obfuscated container is
    deobfuscated and still yields dialogue with exact ids (instead of raising)."""
    from pathlib import Path
    from plugins.extractors.renpy import RenPyExtractor

    blob = make_v1_rpyc([make_label("start", [make_say("e", "Hello there.")])])
    obf = _obfuscate_v2(blob)
    units = RenPyExtractor().extract_rpyc(obf, Path("script.rpyc"))

    dialogue = [u for u in units if u.metadata.get("renpy_id")]
    assert dialogue, "expected dialogue units recovered from the obfuscated .rpyc"
    assert "Hello there." in {u.original_text for u in dialogue}


def test_analyze_game_recovers_obfuscated_rpyc(tmp_path):
    """analyze_game loads an obfuscated .rpyc via the fallback, counts it readable,
    and flags it as recovered (operator transparency)."""
    from core.renpy_ast.structure import analyze_game
    game = tmp_path / "game"
    game.mkdir()
    blob = make_v1_rpyc([make_label("start", [make_say("e", "Hey there.")])])
    (game / "obf.rpyc").write_bytes(_obfuscate_v2(blob))

    report = analyze_game(tmp_path)
    assert report.rpyc_ok == 1 and report.rpyc_failed == 0
    assert report.dialogue_total == 1
    assert any("deobfuscat" in w.lower() or "recovered" in w.lower()
               for w in report.warnings)
