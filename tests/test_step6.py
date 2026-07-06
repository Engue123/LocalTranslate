import pytest
import tempfile
import codecs
from pathlib import Path

from core.encoding import detect_encoding, read_text_robust, write_text_robust
from core.quality_check import QualityReport
from core.syntax_check import validate_rpy_syntax, validate_directory
from core.diff_engine import load_previous_strings, compute_diff
from core.models import TranslationUnit, UnitType


def test_encoding_detection(tmp_path):
    """Verify different encoding formats are correctly detected."""
    f_utf8 = tmp_path / "utf8.txt"
    f_utf8.write_text("Hello UTF-8", encoding="utf-8")
    enc, text = detect_encoding(f_utf8)
    assert enc == "utf-8"
    assert text == "Hello UTF-8"
    
    f_utf8_bom = tmp_path / "utf8_bom.txt"
    f_utf8_bom.write_bytes(codecs.BOM_UTF8 + "Hello UTF-8 BOM".encode("utf-8"))
    enc, text = detect_encoding(f_utf8_bom)
    assert enc == "utf-8-sig"
    assert text == "Hello UTF-8 BOM"

    f_utf16_le = tmp_path / "utf16le.txt"
    f_utf16_le.write_bytes(codecs.BOM_UTF16_LE + "Hello UTF-16 LE".encode("utf-16-le"))
    enc, text = detect_encoding(f_utf16_le)
    assert enc == "utf-16-le"
    assert text == "Hello UTF-16 LE"


def test_encoding_robust_read(tmp_path):
    """Verify robust reading of file contents."""
    f = tmp_path / "robust.txt"
    write_text_robust(f, "Robust text content")
    text = read_text_robust(f)
    assert text == "Robust text content"


def test_quality_report_checks():
    """Verify QualityReport flags untranslated, placeholders mismatch and length ratio."""
    units = [
        # 1. Untranslated (same text, long enough, only alphabetic)
        TranslationUnit(Path("a.rpy"), 1, "EileenDialogue", "EileenDialogue", UnitType.DIALOGUE),
        # 2. Placeholder mismatch
        TranslationUnit(Path("a.rpy"), 2, "Hello [player]!", "Hello [mismatch]!", UnitType.DIALOGUE),
        # 3. Suspicious length ratio (too long or too short)
        TranslationUnit(Path("a.rpy"), 3, "Short text", "This is an extremely long translation that definitely violates length ratio checks.", UnitType.DIALOGUE),
        # 4. Correct unit (should not flag)
        TranslationUnit(Path("a.rpy"), 4, "Hello", "Bonjour", UnitType.DIALOGUE)
    ]
    
    qr = QualityReport(units)
    qr.check_all()
    
    assert len(qr.issues) == 3
    issue_types = [issue["type"] for issue in qr.issues]
    assert "untranslated" in issue_types
    assert "placeholder_mismatch" in issue_types
    assert "suspicious_length" in issue_types


def test_quality_report_write(tmp_path):
    """Verify QualityReport write generates markdown output."""
    units = [
        TranslationUnit(Path("a.rpy"), 1, "EileenDialogue", "EileenDialogue", UnitType.DIALOGUE)
    ]
    qr = QualityReport(units)
    qr.check_all()
    report_file = qr.write(tmp_path)
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "Quality Report" in content


def test_quality_report_write_summary(tmp_path):
    """The report header surfaces the kept-in-source % — the visibility payload."""
    units = [
        TranslationUnit(Path("a.rpy"), 1, "EileenDialogue", "EileenDialogue", UnitType.DIALOGUE),
        TranslationUnit(Path("a.rpy"), 2, "Hello", "Bonjour", UnitType.DIALOGUE),
    ]
    qr = QualityReport(units)
    qr.check_all()
    content = qr.write(tmp_path).read_text(encoding="utf-8")
    assert "**Units audited**: 2" in content
    assert "Identical to source" in content
    assert "1 (50.0%)" in content                  # 1 of 2 units kept identical
    assert "untranslated" in content


def test_syntax_validation_single_file(tmp_path):
    """Verify validate_rpy_syntax detects token and unbalanced quote errors."""
    f_good = tmp_path / "good.rpy"
    f_good.write_text('translate fr start_a1b2c3d4:\n    e "Bonjour"\n', encoding="utf-8")
    issues = validate_rpy_syntax(f_good)
    assert len(issues) == 0
    
    f_unbalanced = tmp_path / "unbalanced.rpy"
    f_unbalanced.write_text('translate fr start_a1b2c3d4:\n    e "Bonjour\n', encoding="utf-8")
    issues = validate_rpy_syntax(f_unbalanced)
    assert len(issues) > 0
    assert any("unbalanced" in i["error"].lower() or "token" in i["error"].lower() for i in issues)


def test_syntax_validation_directory(tmp_path):
    """Verify validate_directory checks all files."""
    f = tmp_path / "invalid.rpy"
    f.write_text('translate fr start_a1b2c3d4:\n    e "Bonjour\n', encoding="utf-8")
    
    res = validate_directory(tmp_path)
    assert "invalid.rpy" in res
    assert len(res["invalid.rpy"]) > 0


def test_diff_engine_mode_a_and_b(tmp_path):
    """Verify diff engine parsing for Mode A comments and Mode B strings blocks."""
    # 1. Mode A file
    f_mode_a = tmp_path / "mode_a.rpy"
    mode_a_content = """
translate fr start_1234:
    # e "Hello Eileen"
    e "Bonjour Eileen"
"""
    f_mode_a.write_text(mode_a_content, encoding="utf-8")
    
    # 2. Mode B file
    f_mode_b = tmp_path / "mode_b.rpy"
    mode_b_content = """
translate fr strings:
    old "Click Start"
    new "Cliquer Début"
"""
    f_mode_b.write_text(mode_b_content, encoding="utf-8")
    
    # Load strings
    known = load_previous_strings(tmp_path)
    assert known.get("Hello Eileen") == "Bonjour Eileen"
    assert known.get("Click Start") == "Cliquer Début"
    
    # Compute diff
    units = [
        TranslationUnit(Path("a.rpy"), 1, "Hello Eileen", unit_type=UnitType.DIALOGUE),
        TranslationUnit(Path("a.rpy"), 2, "Unchanged sentence", unit_type=UnitType.DIALOGUE)
    ]
    diff = compute_diff(units, known)
    assert len(diff) == 1
    assert diff[0].original_text == "Unchanged sentence"
