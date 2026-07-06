"""
Delta mode (complete an existing 3rd-party patch): scan what a tl/<lang>/ already
covers, then translate ONLY the gap. No model involved — pure parsing + filtering.

Scenario: a French patch shipped for game v0.75; the game is now v0.95. The
delta must skip the already-translated dialogue (by exact id) and strings (by
source text), so the new files add only what's missing — Ren'Py merges them with
no "translation already exists" collision.
"""
from pathlib import Path

from core.models import TranslationUnit, UnitType
from core.diff_engine import filter_untranslated
from plugins.generators.renpy import (
    scan_existing_translation, detect_existing_patch, reconstruct_units_from_patch)


def _unit(text, unit_type=UnitType.DIALOGUE, renpy_id=None):
    meta = {"renpy_id": renpy_id} if renpy_id else {}
    return TranslationUnit(file_path=Path("script.rpy"), line_number=1,
                           original_text=text, unit_type=unit_type, metadata=meta)


EXISTING_PATCH = '''\
# Old French patch (game v0.75)

translate french start_c15ca220:
    # "*RING RING*"
    "*DRING DRING*"

translate french intro_302cace2:
    # "Fucking Mark."
    "Putain de Mark."

translate french strings:
    old "Continue"
    new "Continuer"

    old "She said \\"hello\\"."
    new "Elle a dit « bonjour »."
'''


def _write_patch(tmp_path) -> Path:
    tl = tmp_path / "game" / "tl" / "french"
    tl.mkdir(parents=True)
    (tl / "script.rpy").write_text(EXISTING_PATCH, encoding="utf-8")
    return tl


# -- scanner -----------------------------------------------------------------

def test_scan_collects_ids_with_source_and_unescaped_olds(tmp_path):
    tl = _write_patch(tmp_path)
    ids, olds = scan_existing_translation(tl)
    # id -> the SOURCE text it was translated from (for changed-source detection)
    assert ids == {"start_c15ca220": "*RING RING*", "intro_302cace2": "Fucking Mark."}
    assert "strings" not in ids                      # the `strings:` keyword is not an id
    assert "Continue" in olds
    assert 'She said "hello".' in olds               # `\\"` unescaped back to a raw quote


def test_scan_missing_dir_is_empty(tmp_path):
    ids, olds = scan_existing_translation(tmp_path / "nope")
    assert ids == {} and olds == set()


# -- reconstruct units from a patch (post-run quality audit on resume) --------

RECON_PATCH = '''\
translate fr a_001:
    # e "Hello there."
    e "Bonjour."

translate fr a_002:
    # m "Reputation"
    m "Reputation"

translate fr strings:
    old "Continue"
    new "Continuer"

    old "Settings"
    new "Settings"
'''


def test_reconstruct_reads_source_and_translation(tmp_path):
    tl = tmp_path / "game" / "tl" / "fr"
    tl.mkdir(parents=True)
    (tl / "script.rpy").write_text(RECON_PATCH, encoding="utf-8")
    units = reconstruct_units_from_patch(tl)
    pairs = {(u.original_text, u.translated_text) for u in units}
    assert ("Hello there.", "Bonjour.") in pairs        # dialogue, translated
    assert ("Reputation", "Reputation") in pairs        # dialogue, kept in English
    assert ("Continue", "Continuer") in pairs           # string, translated
    assert ("Settings", "Settings") in pairs            # string, kept in English

    # the audit then flags exactly the two kept-English units as untranslated
    from core.quality_check import QualityReport
    qr = QualityReport(units)
    qr.check_all()
    untranslated = [i for i in qr.issues if i["type"] == "untranslated"]
    assert len(untranslated) == 2


def test_reconstruct_missing_dir_is_empty(tmp_path):
    assert reconstruct_units_from_patch(tmp_path / "nope") == []


# -- filter ------------------------------------------------------------------

def test_filter_drops_covered_keeps_gap(tmp_path):
    ids, olds = scan_existing_translation(_write_patch(tmp_path))
    units = [
        _unit("*RING RING*", renpy_id="start_c15ca220"),    # covered by id -> drop
        _unit("Brand new line.", renpy_id="start_99999999"),  # new -> keep
        _unit("Continue", UnitType.UI_STRING),               # covered by old -> drop
        _unit("New Game", UnitType.MENU),                    # new string -> keep
    ]
    kept = filter_untranslated(units, ids, olds)
    texts = {u.original_text for u in kept}
    assert texts == {"Brand new line.", "New Game"}


def test_filter_channels_do_not_cross(tmp_path):
    """A dialogue line is matched ONLY by id, a string ONLY by source text —
    so a dialogue whose text equals a covered UI string is still translated,
    and a string whose text equals a covered dialogue's text is too."""
    ids, olds = scan_existing_translation(_write_patch(tmp_path))
    # dialogue "Continue" with an UNcovered id -> kept (id miss; not matched by old)
    dlg = _unit("Continue", renpy_id="other_12345678")
    # a UI string "*RING RING*" (covered id belongs to dialogue, not strings) -> kept
    ui = _unit("*RING RING*", UnitType.UI_STRING)
    kept = filter_untranslated([dlg, ui], ids, olds)
    assert dlg in kept and ui in kept


def test_filter_dialogue_without_id_is_kept(tmp_path):
    """A dialogue unit lacking an id (regex fallback) can't be excluded by id —
    keep it rather than risk wrongly skipping (safe default)."""
    ids, olds = scan_existing_translation(_write_patch(tmp_path))
    u = _unit("*RING RING*", renpy_id=None)            # same text, no id
    assert filter_untranslated([u], ids, olds) == [u]


def test_filter_reincludes_changed_source(tmp_path):
    """Same id, but the SOURCE changed between game versions -> the existing
    translation is stale -> re-translate (keep it in the delta)."""
    ids, olds = scan_existing_translation(_write_patch(tmp_path))
    same = _unit("Fucking Mark.", renpy_id="intro_302cace2")     # unchanged -> drop
    edited = _unit("Fucking Mark, again.", renpy_id="intro_302cace2")  # changed -> keep
    kept = filter_untranslated([same, edited], ids, olds)
    assert kept == [edited]


# -- detection ---------------------------------------------------------------

def test_detect_matches_alias_folder(tmp_path):
    """tl/french exists; --tgt-lang fr must find it (ISO<->English alias)."""
    _write_patch(tmp_path)                              # creates game/tl/french
    game = tmp_path / "game"
    found = detect_existing_patch(game, "fr")
    assert found is not None and found.name == "french"
    # and the reverse: targeting "french" directly also finds it
    assert detect_existing_patch(game, "french").name == "french"


def test_detect_none_when_absent_or_empty(tmp_path):
    game = tmp_path / "game"
    (game / "tl").mkdir(parents=True)
    assert detect_existing_patch(game, "fr") is None       # no lang folder
    (game / "tl" / "french").mkdir()                       # empty folder (no .rpy)
    assert detect_existing_patch(game, "fr") is None


def test_detect_ignores_unrelated_language(tmp_path):
    _write_patch(tmp_path)                                 # tl/french with .rpy
    assert detect_existing_patch(tmp_path / "game", "de") is None


# -- engine end-to-end (mock translator, real extractor/generator) -----------

import zlib
from core.renpy_ast.loader import ast_classes, _bootstrap


def _make_say(who, what):
    Say = ast_classes()["Say"]
    s = Say.__new__(Say)
    s.who = who; s.what = what
    s.with_ = None; s.interact = True; s.attributes = None
    s.temporary_attributes = None; s.arguments = None
    s.identifier = None; s.explicit_identifier = False; s.linenumber = 0
    return s


def _make_label(name, block):
    Label = ast_classes()["Label"]
    lab = Label.__new__(Label)
    lab.__dict__["name"] = name
    lab.block = block; lab.parameters = None; lab.hide = False; lab.linenumber = 0
    return lab


def _v1_blob(stmts):
    rc = _bootstrap()
    return zlib.compress(rc.pickle_safe_dumps((None, stmts)))


def test_engine_delta_completes_existing_patch(tmp_path):
    """Game has two lines; an existing tl/french already translates the first.
    Auto-delta must translate ONLY the second, into tl/french/localtranslate_delta/,
    never overwriting the existing file."""
    from core.engine import TranslationEngine
    from core.translator import MockTranslator
    from core.renpy_ast.walker import walk_dialogue

    # Build the source game with two dialogue lines and learn their real ids.
    stmts = [_make_label("start", [_make_say("e", "Already done."),
                                   _make_say("e", "Brand new line.")])]
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "script.rpyc").write_bytes(_v1_blob(stmts))
    ids = {u.what: u.identifier for u in walk_dialogue(stmts)}

    # Existing French patch covers ONLY the first line, in its own file.
    tl_fr = game / "tl" / "french"
    tl_fr.mkdir(parents=True)
    (tl_fr / "script.rpy").write_text(
        f'translate french {ids["Already done."]}:\n'
        f'    # e "Already done."\n    e "Déjà fait."\n', encoding="utf-8")

    out = tmp_path / "out"
    res = TranslationEngine(source_dir=tmp_path / "src", output_dir=out,
                            target_lang="fr", translator=MockTranslator()).run(
        progress_callback=lambda p, m: None)
    assert not res.errors

    # Delta written under the EXISTING folder's name + a subdir, never tl/fr.
    delta_dir = out / "game" / "tl" / "french" / "localtranslate_delta"
    assert delta_dir.is_dir()
    assert not (out / "game" / "tl" / "fr").exists()       # not a parallel language
    gen = (delta_dir / "script.rpy").read_text(encoding="utf-8")
    assert "Brand new line." in gen                        # the gap, translated
    assert ids["Brand new line."] in gen
    assert "Already done." not in gen                      # the covered line, skipped
    # the existing patch file is untouched (read-only source invariant)
    assert (tl_fr / "script.rpy").read_text(encoding="utf-8").count("translate") == 1


def test_engine_full_ignores_existing_patch(tmp_path):
    """--full re-translates everything into tl/fr (no delta subdir)."""
    from core.engine import TranslationEngine
    from core.translator import MockTranslator
    from core.renpy_ast.walker import walk_dialogue

    stmts = [_make_label("start", [_make_say("e", "Already done."),
                                   _make_say("e", "Brand new line.")])]
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "script.rpyc").write_bytes(_v1_blob(stmts))
    tl_fr = game / "tl" / "french"
    tl_fr.mkdir(parents=True)
    (tl_fr / "script.rpy").write_text("translate french x:\n    # e \"y\"\n    e \"z\"\n",
                                      encoding="utf-8")

    out = tmp_path / "out"
    res = TranslationEngine(source_dir=tmp_path / "src", output_dir=out,
                            target_lang="fr", translator=MockTranslator()).run(
        full=True, progress_callback=lambda p, m: None)
    assert not res.errors
    gen = (out / "game" / "tl" / "fr" / "script.rpy").read_text(encoding="utf-8")
    assert "Already done." in gen and "Brand new line." in gen   # everything
    assert not (out / "game" / "tl" / "french").exists()         # ignored existing
