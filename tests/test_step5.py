import pytest
import tempfile
import json
import time
import threading
from pathlib import Path
from unittest.mock import patch

from core.pipeline import TranslationPipeline
from core.translator import MockTranslator
from core.dryrun import DryRunReport
from core.benchmark import measure_throughput
from core.engine import TranslationEngine
from plugins.extractors.renpy import RenPyExtractor


class BatchSizeSpyTranslator(MockTranslator):
    def __init__(self, prefix: str = "[FR] ", style_hint: str = None):
        super().__init__(prefix, style_hint)
        self.received_lengths = []
        
    def translate_batch(
        self,
        texts,
        source_lang=None,
        target_lang=None,
        progress_callback=None,
        style_hint=None
    ):
        self.received_lengths.append(len(texts))
        return super().translate_batch(texts, source_lang, target_lang, progress_callback, style_hint)


class TagDroppingTranslator(MockTranslator):
    """Simulates an MT model that drops protected tokens (__TAG_n__)."""

    def translate_batch(self, texts, source_lang=None, target_lang=None,
                        progress_callback=None, style_hint=None):
        import re
        return [re.sub(r'__TAG_\d+__', '', t).strip() for t in texts]


class InterruptingTranslator(MockTranslator):
    def __init__(self, fail_after_calls: int = 1):
        super().__init__()
        self.fail_after_calls = fail_after_calls
        self.call_count = 0
        
    def translate_batch(
        self,
        texts,
        source_lang=None,
        target_lang=None,
        progress_callback=None,
        style_hint=None
    ):
        self.call_count += 1
        if self.call_count > self.fail_after_calls:
            raise RuntimeError("Simulated interruption during translation")
        return super().translate_batch(texts, source_lang, target_lang, progress_callback, style_hint)


def test_chunking_persists_state(tmp_path):
    """Interrupt a run, check state json, resume and verify skip."""
    src_path = tmp_path / "src"
    out_path = tmp_path / "out"
    
    # Create game/ directory
    game_dir = src_path / "game"
    game_dir.mkdir(parents=True)
    
    # Create script1.rpy and script2.rpy
    (game_dir / "script1.rpy").write_text('label start:\n    "Line one."\n', encoding="utf-8")
    (game_dir / "script2.rpy").write_text('label start:\n    "Line two."\n', encoding="utf-8")
    
    # Instantiate pipeline
    pipeline = TranslationPipeline(
        source_dir=src_path,
        output_dir=out_path,
        source_lang="en",
        target_lang="fr",
        mode="A",
        translator_type="mock"
    )
    
    # Setup interrupting translator failing on second file translation
    spy_translator = InterruptingTranslator(fail_after_calls=1)
    
    # Run first execution (should fail)
    res = pipeline.run(translator=spy_translator)
    assert len(res.errors) > 0, "Pipeline run should have failed due to simulated interruption"
    
    # Check that state JSON exists in output_dir only
    state_file = out_path / ".translate_state.json"
    assert state_file.exists(), "State JSON file should exist after interruption"
    assert not (src_path / ".translate_state.json").exists(), "State JSON should NEVER be written to source"
    
    with open(state_file, "r", encoding="utf-8") as f:
        state_data = json.load(f)
        
    assert "completed_files" in state_data
    assert len(state_data["completed_files"]) == 1
    completed_file_name = state_data["completed_files"][0]
    assert "script1.rpy" in completed_file_name
    
    # Verify that output for script1.rpy was generated
    output_script1 = out_path / "game" / "tl" / "fr" / "script1.rpy"
    assert output_script1.exists()
    
    # Verify output for script2.rpy was NOT generated
    output_script2 = out_path / "game" / "tl" / "fr" / "script2.rpy"
    assert not output_script2.exists()
    
    # Run again to resume (no failure this time)
    resume_spy = BatchSizeSpyTranslator()
    res_resume = pipeline.run(translator=resume_spy)
    assert len(res_resume.errors) == 0
    
    # Since we ran successfully, the state file should be deleted
    assert not state_file.exists(), "State file should be cleaned up after successful completion"
    
    # Check both files were generated
    assert (out_path / "game" / "tl" / "fr" / "script1.rpy").exists()
    assert (out_path / "game" / "tl" / "fr" / "script2.rpy").exists()
    
    # Verify script1.rpy was indeed skipped (resume_spy should only translate script2)
    assert len(resume_spy.received_lengths) == 1
    assert resume_spy.received_lengths[0] == 1


def test_batch_size_renpy(tmp_path):
    """Verify mock translator receives lists of length max 5 for RenPy."""
    src_path = tmp_path / "src"
    out_path = tmp_path / "out"
    
    # Create game/ directory
    game_dir = src_path / "game"
    game_dir.mkdir(parents=True)
    
    # Create a script with 8 unique lines
    lines = [f'    e "Line {i}."' for i in range(8)]
    script_content = "label start:\n" + "\n".join(lines) + "\n"
    (game_dir / "script.rpy").write_text(script_content, encoding="utf-8")
    
    pipeline = TranslationPipeline(
        source_dir=src_path,
        output_dir=out_path,
        source_lang="en",
        target_lang="fr",
        mode="A",
        translator_type="mock"
    )
    
    spy = BatchSizeSpyTranslator()
    pipeline.run(translator=spy, batch_size=5)
        
    assert len(spy.received_lengths) > 0
    assert max(spy.received_lengths) <= 5
    assert sum(spy.received_lengths) == 8


def test_batch_size_plaintext(tmp_path):
    """Verify mock translator receives lists of length max 10 for PlainText."""
    src_path = tmp_path / "src"
    out_path = tmp_path / "out"
    src_path.mkdir(exist_ok=True)
    
    # Create a plaintext script with 15 unique lines
    lines = [f"Plain line {i}" for i in range(15)]
    script_content = "\n".join(lines) + "\n"
    (src_path / "doc.txt").write_text(script_content, encoding="utf-8")
    
    pipeline = TranslationPipeline(
        source_dir=src_path,
        output_dir=out_path,
        source_lang="en",
        target_lang="fr",
        mode="A",
        translator_type="mock"
    )
    
    spy = BatchSizeSpyTranslator()
    pipeline.run(translator=spy, batch_size=10)
        
    assert len(spy.received_lengths) > 0
    assert max(spy.received_lengths) <= 10
    assert sum(spy.received_lengths) == 15
    
    # Verify output mirrored plaintext file
    output_txt = out_path / "game" / "tl" / "fr" / "doc.txt"
    assert output_txt.exists()


def test_dryrun_report():
    """Verify DryRunReport generation."""
    from core.models import TranslationUnit, UnitType
    units = [
        TranslationUnit(Path("script.rpy"), 10, "Hello", unit_type=UnitType.DIALOGUE),
        TranslationUnit(Path("script.rpy"), 12, "World", unit_type=UnitType.UI_STRING)
    ]
    report = DryRunReport(units)
    md = report.to_markdown()
    assert "Total units found: 2" in md
    assert "Breakdown by type" in md
    assert "`dialogue`: 1" in md
    assert "`ui_string`: 1" in md
    
    with tempfile.TemporaryDirectory() as temp_dir:
        path = report.write(Path(temp_dir))
        assert path.exists()
        assert "Total units found: 2" in path.read_text(encoding="utf-8")


def test_auto_tune():
    """Verify measure_throughput works correctly."""
    def dummy_translate(texts):
        # artificial sleep
        time.sleep(0.01)
        return [t.upper() for t in texts]
        
    samples = [f"Text {i}" for i in range(12)]
    res = measure_throughput(dummy_translate, samples, [2, 4])
    assert "best_batch_size" in res
    assert res["best_batch_size"] in [2, 4]


def test_cancellation(tmp_path):
    """Verify engine stops when cancel_event is set."""
    src_path = tmp_path / "src"
    out_path = tmp_path / "out"
    game_dir = src_path / "game"
    game_dir.mkdir(parents=True)
    
    # Create several scripts
    for i in range(5):
        (game_dir / f"script{i}.rpy").write_text('label start:\n    "Line."\n', encoding="utf-8")
        
    pipeline = TranslationPipeline(
        source_dir=src_path,
        output_dir=out_path,
        source_lang="en",
        target_lang="fr",
        mode="A",
        translator_type="mock"
    )
    
    cancel_event = threading.Event()
    
    # Let's subclass or monkeypatch translate_batch to trigger cancel_event
    original_translator = MockTranslator()
    def cancel_on_translate(texts, *args, **kwargs):
        cancel_event.set()
        return original_translator.translate_batch(texts, *args, **kwargs)
        
    spy_translator = MockTranslator()
    spy_translator.translate_batch = cancel_on_translate
    
    res = pipeline.run(translator=spy_translator, cancel_event=cancel_event)
    assert "Cancelled by user" in res.errors
    # Should not have finished processing all 5 files
    state_file = out_path / ".translate_state.json"
    assert state_file.exists()
    with open(state_file, "r") as f:
        state_data = json.load(f)
    assert len(state_data["completed_files"]) < 5


def test_engine_rpa_integration(tmp_path):
    """Verify that the engine detects RPA archives, extracts, decompiles, and translates scripts."""
    import pickle
    import zlib
    from core.engine import TranslationEngine
    
    src_path = tmp_path / "src"
    out_path = tmp_path / "out"
    game_dir = src_path / "game"
    game_dir.mkdir(parents=True)
    
    # 1. Create a dummy RPA file containing a script
    archive_file = game_dir / "archive.rpa"
    script_content = b'label start:\n    "Hello RPA!"\n'
    
    key = 0xDEADBEEF
    dummy_header = f"RPA-3.0 {0:016x} {key:08x}\n"
    header_len = len(dummy_header.encode("utf-8"))
    
    file_offsets = {"script.rpy": (header_len, len(script_content))}
    index_offset = header_len + len(script_content)
    
    raw_index = {"script.rpy": [(header_len ^ key, len(script_content) ^ key, b"")]}
    pickled = pickle.dumps(raw_index, protocol=2)
    compressed_index = zlib.compress(pickled)
    
    with open(archive_file, "wb") as f:
        header_str = f"RPA-3.0 {index_offset:016x} {key:08x}\n"
        f.write(header_str.encode("utf-8"))
        f.write(script_content)
        f.write(compressed_index)
        
    # 2. Run the engine
    engine = TranslationEngine(
        source_dir=src_path,
        output_dir=out_path,
        source_lang="en",
        target_lang="fr",
        mode="A",
        translator_type="mock"
    )
    
    res = engine.run()
    assert len(res.errors) == 0
    assert res.dialogue_extracted == 1
    
    # 3. Check output was generated correctly in out_path / "game" / "tl" / "fr" / "script.rpy"
    out_file = out_path / "game" / "tl" / "fr" / "script.rpy"
    assert out_file.exists()
    
    content = out_file.read_text(encoding="utf-8")
    assert "translate fr" in content
    assert 'Hello RPA!' in content


def test_macos_app_bundle_e2e(tmp_path):
    """E2E: a macOS .app bundle must resolve to its inner game/ and produce
    output at out/game/tl/fr/script.rpy, never leaking .app/Contents paths."""
    src = tmp_path / "src"
    inner_game = src / "MyGame.app" / "Contents" / "Resources" / "autorun" / "game"
    inner_game.mkdir(parents=True)
    (inner_game / "script.rpy").write_text(
        'label start:\n    e "Hi there."\n', encoding="utf-8"
    )
    out = tmp_path / "out"

    engine = TranslationEngine(source_dir=src, output_dir=out, target_lang="fr", mode="A")
    res = engine.run(translator=MockTranslator())
    assert len(res.errors) == 0

    out_file = out / "game" / "tl" / "fr" / "script.rpy"
    assert out_file.exists(), "output must be at out/game/tl/fr/script.rpy"

    # Internal paths must be relative to game/, never the bundle internals.
    for u in res.units:
        posix = u.file_path.as_posix()
        assert ".app" not in posix
        assert "Contents" not in posix

    assert "translate fr" in out_file.read_text(encoding="utf-8")


def test_json_document_translation(tmp_path):
    """JSON document mode: translate string values, preserve keys/structure/types."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "data.json").write_text(
        json.dumps({"title": "Hello", "count": 3, "items": ["A", "B"],
                    "nested": {"msg": "Bye"}}),
        encoding="utf-8",
    )
    out = tmp_path / "out"

    engine = TranslationEngine(source_dir=src, output_dir=out, target_lang="fr")
    res = engine.run(translator=MockTranslator())
    assert len(res.errors) == 0

    # Auto-detected document mode mirrors under game/tl/<lang>/ (same contract
    # as the plaintext path, see test_batch_size_plaintext).
    out_json = out / "game" / "tl" / "fr" / "data.json"
    assert out_json.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["title"].startswith("[FR]")
    assert data["count"] == 3                      # number untouched
    assert all(v.startswith("[FR]") for v in data["items"])
    assert data["nested"]["msg"].startswith("[FR]")
    assert "title" in data                         # keys untouched


def test_renpy_finalize_produces_language_button(tmp_path):
    """A successful Ren'Py run must ship the turnkey language switcher snippet
    and install instructions alongside the tl/ files."""
    src = tmp_path / "src"
    out = tmp_path / "out"
    game = src / "game"
    game.mkdir(parents=True)
    (game / "script.rpy").write_text('label start:\n    "Hello there."\n', encoding="utf-8")

    engine = TranslationEngine(
        source_dir=src, output_dir=out, target_lang="fr", mode="A",
    )
    res = engine.run(translator=MockTranslator())
    assert len(res.errors) == 0

    # The selector is ONE shared, language-agnostic file at tl/ root (not per
    # language) that auto-discovers installed languages — no hardcoded Language("fr").
    launcher = out / "game" / "tl" / "localtranslate_language.rpy"
    assert launcher.exists(), "shared language selector must be generated"
    text = launcher.read_text(encoding="utf-8")
    assert "known_languages" in text          # auto-discovery
    assert "lt_language_menu" in text          # the popup screen
    assert "Language(_lt_l)" in text           # dynamic per-language action
    assert "config.overlay_screens" in text    # the always-visible button
    assert not (out / "game" / "tl" / "fr" / "zzz_localtranslate_language.rpy").exists()
    assert (out / "INSTALL_INSTRUCTIONS.md").exists()


def test_lost_placeholder_is_flagged_for_review(tmp_path):
    """A translator that drops a protected [placeholder] must flag the unit
    as needs_review with a warning, instead of silently losing it."""
    src_path = tmp_path / "src"
    out_path = tmp_path / "out"
    game_dir = src_path / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text(
        'label start:\n    "You have [points] points!"\n', encoding="utf-8"
    )

    engine = TranslationEngine(
        source_dir=src_path,
        output_dir=out_path,
        source_lang="en",
        target_lang="fr",
        mode="A",
    )
    res = engine.run(translator=TagDroppingTranslator())

    assert len(res.errors) == 0
    flagged = [u for u in res.units if u.needs_review]
    assert len(flagged) == 1, "The unit with the dropped [points] tag must be flagged"
    # L5 guarantee: rather than emit broken Ren'Py, we fall back to the ORIGINAL,
    # so the placeholder is still intact (just untranslated) and flagged for review.
    assert flagged[0].translated_text == flagged[0].original_text
    assert "[points]" in flagged[0].translated_text
    assert flagged[0].needs_review is True



class ElisionMockTranslator(MockTranslator):
    """Always returns French text with an elision error, to test post-editing."""

    def translate_batch(self, texts, source_lang=None, target_lang=None,
                        progress_callback=None, style_hint=None):
        return ["Tu te endors." for _ in texts]


def _run_one_line_game(tmp_path, target_lang, translator):
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "s.rpy").write_text('label start:\n    "Hello there friend."\n', encoding="utf-8")
    TranslationPipeline(
        source_dir=tmp_path / "src", output_dir=tmp_path / "out",
        source_lang="en", target_lang=target_lang, mode="A", translator_type="mock",
    ).run(translator=translator)
    return (tmp_path / "out" / "game" / "tl" / target_lang / "s.rpy").read_text(encoding="utf-8")


def test_french_elision_postedit_applied(tmp_path):
    """French target: the deterministic elision fix runs on translated dialogue."""
    out = _run_one_line_game(tmp_path, "fr", ElisionMockTranslator())
    assert "t’endors" in out          # "te endors" -> "t’endors"
    assert "te endors" not in out


def test_elision_postedit_skipped_for_non_french(tmp_path):
    """Non-French target: output is left exactly as the model produced it."""
    out = _run_one_line_game(tmp_path, "de", ElisionMockTranslator())
    assert "te endors" in out              # untouched
    assert "t’endors" not in out
