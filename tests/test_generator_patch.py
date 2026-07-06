"""
Regression tests for the Ren'Py patch generator — the crashes real games hit:

  1. `Exception: A translation for "X" already exists` — the same UI string was
     emitted as `old "X"` in two files (string translations are GLOBAL in Ren'Py).
  2. `is not terminated with a newline` — a translated line with a raw newline /
     unescaped quote / backslash broke the generated `.rpy`.

Plus the `validate_patch` safety net that catches both before shipping.
"""
import tempfile
from pathlib import Path

from core.models import TranslationUnit, UnitType
from plugins.generators.renpy import (
    RenPyGenerator, escape_renpy_string, validate_patch,
)


def _gen(units, mode="A"):
    out = Path(tempfile.mkdtemp())
    RenPyGenerator().generate(units, Path("/src"), out, "fr", mode=mode)
    return out / "game" / "tl" / "fr"


def _ui(path, text, trans):
    return TranslationUnit(file_path=Path(path), line_number=1, original_text=text,
                           translated_text=trans, unit_type=UnitType.UI_STRING)


def _dlg(path, text, trans, rid, who=None):
    return TranslationUnit(file_path=Path(path), line_number=1, original_text=text,
                           translated_text=trans, unit_type=UnitType.DIALOGUE,
                           character=who, metadata={"renpy_id": rid})


# --- bug 1: a UI string shared by two files must be emitted ONCE ------------

def test_shared_string_emitted_once_across_files():
    units = [
        _ui("game/chapter2.rpy", "Don't", "Pas"),
        _ui("game/chapter2_pt2.rpy", "Don't", "Pas"),
    ]
    patch = _gen(units)
    total = sum(f.read_text(encoding="utf-8").count('old "Don\'t"')
                for f in patch.rglob("*.rpy"))
    assert total == 1, f'expected one `old "Don\'t"` in the whole patch, got {total}'
    # and the validator agrees it is clean
    assert validate_patch(patch) == []


def test_duplicate_string_across_files_was_the_real_crash():
    """The exact shape of screenshots 1 & 2: 'Cancel' in two mirrored files."""
    units = [
        _ui("game/Scripts/Map/house/rileypark.rpy", "Cancel", "Annuler"),
        _ui("game/Scripts/Map/functions/sleep.rpy", "Cancel", "Annuler"),
    ]
    patch = _gen(units)
    olds = [ln for f in patch.rglob("*.rpy")
            for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() == 'old "Cancel"']
    assert len(olds) == 1


# --- bug 2: escaping (newline / quote / backslash) -------------------------

def test_escape_handles_newline_quote_backslash():
    s = escape_renpy_string('Ligne1\nLigne2 "x" et \\ fin')
    assert "\n" not in s                 # no raw newline survives
    assert "\\n" in s                    # newline became the \n escape
    assert '\\"' in s                    # quote escaped
    assert "\\\\" in s                   # backslash escaped


def test_multiline_translation_stays_one_physical_line():
    units = [_dlg("game/a.rpy", "hi", 'Premier.\nDeuxième "ok".', "start_abcd1234")]
    patch = _gen(units)
    content = (patch / "a.rpy").read_text(encoding="utf-8")
    # the translated say line (not the comment) must be a single physical line
    say_lines = [ln for ln in content.splitlines()
                 if ln.strip().startswith('"') and not ln.strip().startswith("#")]
    assert len(say_lines) == 1
    assert "\\n" in say_lines[0]
    assert validate_patch(patch) == []


# --- validate_patch catches what slips through -----------------------------

def test_validator_flags_duplicate_old():
    bad = Path(tempfile.mkdtemp()) / "tl" / "fr"
    bad.mkdir(parents=True)
    (bad / "a.rpy").write_text('translate fr strings:\n    old "Hi"\n    new "Salut"\n',
                               encoding="utf-8")
    (bad / "b.rpy").write_text('translate fr strings:\n    old "Hi"\n    new "Coucou"\n',
                               encoding="utf-8")
    problems = validate_patch(bad)
    assert any("duplicate string" in p and "Hi" in p for p in problems)


def test_validator_flags_unterminated_string():
    bad = Path(tempfile.mkdtemp()) / "tl" / "fr"
    bad.mkdir(parents=True)
    # a raw newline inside the quotes splits the logical line
    (bad / "a.rpy").write_text('translate fr s_1:\n    "il dit\nbonjour"\n', encoding="utf-8")
    problems = validate_patch(bad)
    assert any("unterminated" in p for p in problems)


def test_validator_passes_clean_patch():
    units = [
        _dlg("game/a.rpy", "Hello", "Bonjour", "start_11111111", who="e"),
        _ui("game/a.rpy", "Options", "Options"),
    ]
    assert validate_patch(_gen(units)) == []


def test_shared_language_launcher_is_valid_and_auto_discovering():
    """finalize() writes ONE shared, auto-discovering selector at tl/ root that
    passes patch validation and is never duplicated per language."""
    out = Path(tempfile.mkdtemp())
    gen = RenPyGenerator()
    gen.finalize(out, "fr")

    launcher = out / "game" / "tl" / "localtranslate_language.rpy"
    assert launcher.exists()
    text = launcher.read_text(encoding="utf-8")
    assert "renpy.known_languages()" in text          # auto-discovery, not hardcoded
    assert "renpy.get_screen" in text                  # shown only in menus (not gameplay)
    # MUST be always_shown_screens: the engine SUPPRESSES overlay_screens on
    # the main menu and in the game menu — the button would never display
    # (found on a real user game; verified in 00gamemenu.rpy suppress_overlay).
    assert "config.always_shown_screens.append" in text
    assert text.count("screen lt_language_menu") == 1
    # the validator (balanced quotes etc.) is happy with the launcher
    assert validate_patch(out / "game" / "tl") == []

    # a second language run must NOT add a second launcher (else screen redefinition)
    gen.finalize(out, "es")
    launchers = list((out / "game" / "tl").rglob("*language*.rpy"))
    assert len(launchers) == 1
