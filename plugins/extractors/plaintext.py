import json
from pathlib import Path
from typing import List
from core.models import TranslationUnit, UnitType

# Document types handled by the simple document-translation mode.
TEXT_SUFFIXES = (".txt", ".md")
SUPPORTED_SUFFIXES = TEXT_SUFFIXES + (".json",)


def _collect_json_strings(node, out: List[str]) -> None:
    """Recursively gather all non-empty string *values* from a JSON structure
    (dict values and list items). Dict keys are left untouched."""
    if isinstance(node, dict):
        for v in node.values():
            _collect_json_strings(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_json_strings(v, out)
    elif isinstance(node, str):
        if node.strip():
            out.append(node)


class PlainTextExtractor:
    def extract(self, source_dir: Path) -> List[TranslationUnit]:
        units = []
        source_dir = Path(source_dir)

        files = []
        if source_dir.is_file():
            files = [source_dir]
        else:
            for ext in SUPPORTED_SUFFIXES:
                files.extend(source_dir.rglob(f"*{ext}"))

        for file_path in sorted(files):
            if file_path.suffix.lower() == ".json":
                units.extend(self._extract_json(file_path))
            else:
                units.extend(self._extract_lines(file_path))
        return units

    def _extract_lines(self, file_path: Path) -> List[TranslationUnit]:
        units = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return units
        for line_idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue
            units.append(TranslationUnit(
                file_path=file_path,
                line_number=line_idx,
                original_text=stripped,
                unit_type=UnitType.UI_STRING,
            ))
        return units

    def _extract_json(self, file_path: Path) -> List[TranslationUnit]:
        units = []
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return units
        strings: List[str] = []
        _collect_json_strings(data, strings)
        for idx, value in enumerate(strings, 1):
            units.append(TranslationUnit(
                file_path=file_path,
                line_number=idx,
                original_text=value,
                unit_type=UnitType.UI_STRING,
                metadata={"format": "json"},
            ))
        return units
