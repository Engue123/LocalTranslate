import shutil
from pathlib import Path
import pytest

from core.models import TranslationUnit, UnitType, EngineResult
from core.engine import TranslationEngine
from plugins.extractors.plaintext import PlainTextExtractor
from plugins.generators.plaintext import PlainTextGenerator


class MockTranslator:
    def translate_batch(self, texts):
        return [t.upper() for t in texts]


def test_models():
    unit = TranslationUnit(
        file_path=Path("a.txt"),
        line_number=1,
        original_text="Hello",
        unit_type=UnitType.DIALOGUE
    )
    assert unit.source_text == "Hello"
    assert unit.unit_id == unit.key


def test_plaintext_roundtrip(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f1 = src_dir / "f1.txt"
    f1.write_text("Hello\nWorld\n", encoding="utf-8")
    
    out_dir = tmp_path / "out"
    
    extractor = PlainTextExtractor()
    generator = PlainTextGenerator()
    translator = MockTranslator()
    
    engine = TranslationEngine(src_dir, out_dir, translator=translator)
    result = engine.run(
        source_path=src_dir,
        output_path=out_dir,
        extractor=extractor,
        generator=generator,
        batch_size=2
    )
    
    assert len(result.errors) == 0
    assert len(result.units) == 2
    assert (out_dir / "f1.txt").exists()
    out_content = (out_dir / "f1.txt").read_text(encoding="utf-8")
    assert out_content == "HELLO\nWORLD\n"


def test_engine_error(tmp_path):
    extractor = PlainTextExtractor()
    generator = PlainTextGenerator()
    translator = MockTranslator()
    
    engine = TranslationEngine(tmp_path / "nonexistent", tmp_path / "out", translator=translator)
    result = engine.run(
        source_path=tmp_path / "nonexistent",
        output_path=tmp_path / "out",
        extractor=extractor,
        generator=generator
    )
    assert len(result.errors) > 0
