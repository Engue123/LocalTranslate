"""
Syntax validation for generated RenPy translation files.
Uses Python's tokenizer to catch basic syntax errors in .rpy output.
"""
import io
import tokenize
from pathlib import Path
from typing import List, Dict


def validate_rpy_syntax(file_path: Path) -> List[Dict]:
    """
    Tokenize a .rpy file to detect syntax-level errors.
    Returns list of issue dicts with line numbers.
    """
    issues: List[Dict] = []
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return [{"line": 0, "error": f"Cannot read file: {e}"}]
    
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except tokenize.TokenError as e:
        line = e.args[1][0] if len(e.args) > 1 and isinstance(e.args[1], tuple) else 0
        issues.append({"line": line, "error": str(e)})
    except IndentationError as e:
        issues.append({"line": e.lineno or 0, "error": f"IndentationError: {e.msg}"})
    except SyntaxError as e:
        issues.append({"line": e.lineno or 0, "error": f"SyntaxError: {e.msg}"})
    except Exception as e:
        issues.append({"line": 0, "error": f"Validation error: {e}"})
    
    lines = text.splitlines()
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("old ") or stripped.startswith("new ") or '"' in stripped:
            quote_count = stripped.count('"')
            if quote_count % 2 != 0:
                issues.append({"line": idx, "error": "Possible unbalanced double quotes"})
    
    return issues


def validate_directory(rpy_dir: Path) -> Dict[str, List[Dict]]:
    """Validate all .rpy files in a directory."""
    results: Dict[str, List[Dict]] = {}
    if not rpy_dir.exists():
        return results
    for rpy in sorted(rpy_dir.rglob("*.rpy")):
        issues = validate_rpy_syntax(rpy)
        if issues:
            results[str(rpy.relative_to(rpy_dir))] = issues
    return results
