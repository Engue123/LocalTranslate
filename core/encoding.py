"""
Robust file encoding detection and reading.
Handles UTF-8, UTF-8-BOM, UTF-16-LE, UTF-16-BE, Latin-1 fallback.
"""
import codecs
from pathlib import Path
from typing import Tuple


def detect_encoding(file_path: Path) -> Tuple[str, str]:
    """
    Detect file encoding by BOM and fallback heuristics.
    Returns (encoding, content_string).
    """
    raw = file_path.read_bytes()
    
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig", raw[len(codecs.BOM_UTF8):].decode("utf-8")
    elif raw.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le", raw[len(codecs.BOM_UTF16_LE):].decode("utf-16-le")
    elif raw.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be", raw[len(codecs.BOM_UTF16_BE):].decode("utf-16-be")
    
    try:
        return "utf-8", raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    
    return "latin-1", raw.decode("latin-1")


def read_text_robust(file_path: Path) -> str:
    """Read a file with automatic encoding detection."""
    _, content = detect_encoding(file_path)
    return content


def write_text_robust(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text ensuring UTF-8 output (RenPy standard)."""
    file_path.write_text(content, encoding=encoding)
