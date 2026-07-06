"""
Robustness fixes for the real 13h-run failure on an exFAT external drive:

  - macOS "._*" AppleDouble sidecars (+ .DS_Store) must be SKIPPED, never parsed
    as scripts/archives (they produced "AST unreadable" + a bogus RPA error);
  - an unreadable archive is a NON-FATAL warning, never an error that skips
    finalize and marks the whole run FAILURE while everything was translated;
  - finalize (language selector) runs whenever a patch exists — including a
    fully-resumed re-run — so an interrupted run can be completed by re-running.
"""
import zlib
from pathlib import Path

from core.engine import _collect_renpy_jobs, TranslationEngine
from core.models import EngineResult
from core.renpy_ast.loader import ast_classes, _bootstrap
from core.translator import MockTranslator


def _say(who, what):
    Say = ast_classes()["Say"]
    s = Say.__new__(Say)
    s.who = who; s.what = what
    s.with_ = None; s.interact = True; s.attributes = None
    s.temporary_attributes = None; s.arguments = None
    s.identifier = None; s.explicit_identifier = False; s.linenumber = 0
    return s


def _label(name, block):
    Label = ast_classes()["Label"]
    lab = Label.__new__(Label)
    lab.__dict__["name"] = name
    lab.block = block; lab.parameters = None; lab.hide = False; lab.linenumber = 0
    return lab


def _v1(stmts):
    rc = _bootstrap()
    return zlib.compress(rc.pickle_safe_dumps((None, stmts)))


# -- junk filtering ----------------------------------------------------------

def test_collect_jobs_skips_appledouble_and_dsstore(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text('label start:\n    "Hi."\n', encoding="utf-8")
    (game / "._script.rpy").write_bytes(b"\x00\x05\x16\x07garbage")   # AppleDouble
    (game / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (game / "._archive_0.09.rpa").write_bytes(b"\x00\x05\x16\x07garbage")  # junk rpa

    res = EngineResult(output_dir=tmp_path / "out")
    jobs, _ = _collect_renpy_jobs(game, tmp_path / "out", lambda *a: None, res)

    rels = {j[0] for j in jobs}
    assert rels == {"script.rpy"}                       # only the real file
    assert not any(r.startswith("._") for r in rels)
    # the junk "._archive.rpa" was never opened -> no error, no warning
    assert res.errors == [] and res.warnings == []


def test_unreadable_archive_is_warning_not_error(tmp_path):
    """A real-named but corrupt .rpa -> a non-fatal WARNING (not an error that
    would fail the run and skip finalize)."""
    game = tmp_path / "game"
    game.mkdir()
    (game / "archive.rpa").write_bytes(b"not a real rpa header at all")

    res = EngineResult(output_dir=tmp_path / "out")
    _collect_renpy_jobs(game, tmp_path / "out", lambda *a: None, res)

    assert res.errors == []                              # never fatal
    assert len(res.warnings) == 1 and "archive.rpa" in res.warnings[0]


# -- end-to-end: a bad archive must not fail the run / skip finalize ----------

def test_run_succeeds_and_finalizes_despite_bad_archive(tmp_path, monkeypatch):
    stmts = [_label("start", [_say("e", "Hello there."), _say("e", "Bye.")])]
    monkeypatch.setattr("core.renpy_ast.load_ast", lambda src, try_harder=False: stmts)
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "script.rpyc").write_bytes(_v1(stmts))
    (game / "archive.rpa").write_bytes(b"corrupt archive bytes")     # triggers a warning

    out = tmp_path / "out"
    res = TranslationEngine(source_dir=tmp_path / "src", output_dir=out,
                            target_lang="fr", translator=MockTranslator()).run(
        progress_callback=lambda p, m: None)

    # The bad archive is a warning, NOT a failure — the run succeeds...
    assert res.errors == []
    assert res.warnings and "archive.rpa" in res.warnings[0]
    assert res.units                                     # dialogue was translated
    # ...and finalize STILL ran (the language selector exists).
    assert (out / "game" / "tl" / "localtranslate_language.rpy").exists()


def test_resumed_run_still_finalizes(tmp_path, monkeypatch):
    """An interrupted run is completed by simply re-running: even when every
    file is skipped (state says 'completed'), finalize runs on the existing patch."""
    stmts = [_label("start", [_say("e", "Hello there.")])]
    monkeypatch.setattr("core.renpy_ast.load_ast", lambda src, try_harder=False: stmts)
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "script.rpyc").write_bytes(_v1(stmts))
    out = tmp_path / "out"

    eng = TranslationEngine(source_dir=tmp_path / "src", output_dir=out,
                            target_lang="fr", translator=MockTranslator())
    eng.run(progress_callback=lambda p, m: None)         # first pass: translates + finalizes
    selector = out / "game" / "tl" / "localtranslate_language.rpy"
    assert selector.exists()
    selector.unlink()                                    # simulate a missing finalize

    # Re-run: the state file marks the file completed -> skipped -> but finalize
    # must run again on the existing patch.
    res2 = eng.run(progress_callback=lambda p, m: None)
    assert res2.errors == []
    assert selector.exists()                             # finalized on the resumed run


def test_resume_with_state_rebuilds_quality_report(tmp_path, monkeypatch):
    """The real-13h-run class: a run that 'failed at the end' keeps its state file,
    so the recovery re-run SKIPS every file (0 fresh units). The quality report must
    still be produced — reconstructed from the on-disk patch — not silently absent
    (the gating was `if result.units`, which a full resume never satisfies)."""
    stmts = [_label("start", [_say("e", "Hello there.")])]
    monkeypatch.setattr("core.renpy_ast.load_ast", lambda src, try_harder=False: stmts)
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "script.rpyc").write_bytes(_v1(stmts))
    out = tmp_path / "out"

    eng = TranslationEngine(source_dir=tmp_path / "src", output_dir=out,
                            target_lang="fr", translator=MockTranslator())
    eng.run(progress_callback=lambda p, m: None)         # writes the patch + report
    report = out / "quality_report.md"
    report.unlink()                                      # this one came from fresh units

    # On a successful run the state file is deleted; recreate it to force the skip
    # path (= the interrupted run that kept its state). Job key for script.rpyc is
    # "script.rpy" (see test_collect_jobs_* above).
    (out / ".translate_state.json").write_text(
        '{"completed_files": ["script.rpy"]}', encoding="utf-8")

    res2 = eng.run(progress_callback=lambda p, m: None)  # resume: every file skipped
    assert res2.errors == []
    assert not res2.units                                # 0 fresh units this pass
    assert report.exists()                               # rebuilt from the on-disk patch
    assert "Units audited" in report.read_text(encoding="utf-8")


def test_validate_patch_ignores_appledouble(tmp_path):
    """validate_patch must skip macOS '._*' sidecars (exFAT) — not flag them as
    'not valid UTF-8' structural problems (the recovery-run false FAILURE)."""
    from plugins.generators.renpy import validate_patch
    tl = tmp_path / "game" / "tl" / "fr"
    tl.mkdir(parents=True)
    (tl / "script.rpy").write_text(
        'translate fr start_abc:\n    # "Hi."\n    "Salut."\n', encoding="utf-8")
    (tl / "._script.rpy").write_bytes(b"\x00\x05\x16\x07Mac sidecar, not UTF-8 \xff\xfe")
    (tl / ".DS_Store").write_bytes(b"\x00\x00Bud1\xff")
    assert validate_patch(tl, "fr") == []      # junk ignored -> structurally sound
