import json
from pathlib import Path
from typing import List, Dict
from core.models import TranslationUnit


def _replace_json_strings(node, mapping: Dict[str, str]):
    """Recursively rebuild a JSON structure, replacing string *values* found in
    `mapping` with their translation. Keys and non-string values are preserved."""
    if isinstance(node, dict):
        return {k: _replace_json_strings(v, mapping) for k, v in node.items()}
    if isinstance(node, list):
        return [_replace_json_strings(v, mapping) for v in node]
    if isinstance(node, str):
        return mapping.get(node, node)
    return node


class PlainTextGenerator:
    def __init__(self):
        self.is_phase3 = True

    def generate(
        self,
        translated_units: List[TranslationUnit],
        source_dir: Path,
        output_dir: Path,
        target_lang: str,
        mode: str = "A",
    ) -> None:
        source_dir = Path(source_dir)
        output_dir = Path(output_dir)

        # Group units by file
        by_file = {}
        for u in translated_units:
            by_file.setdefault(u.file_path, []).append(u)

        for file_path, units in by_file.items():
            out_path = self._output_path(file_path, source_dir, output_dir, target_lang)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            if Path(file_path).suffix.lower() == ".json":
                self._generate_json(file_path, units, out_path)
            else:
                self._generate_lines(file_path, units, out_path)

    def _output_path(self, file_path: Path, source_dir: Path, output_dir: Path, target_lang: str) -> Path:
        if source_dir.is_file():
            rel_p = Path(file_path.name)
        else:
            try:
                rel_p = file_path.relative_to(source_dir)
            except ValueError:
                rel_p = Path(file_path.name)

        # If not phase 3, wrap in game/tl/lang/
        if not getattr(self, "is_phase3", True):
            parts = list(rel_p.parts)
            if parts and parts[0] == "game":
                new_parts = ["game", "tl", target_lang] + parts[1:]
            else:
                new_parts = ["game", "tl", target_lang] + parts
            rel_p = Path(*new_parts)

        return output_dir / rel_p

    def _generate_lines(self, file_path: Path, units: List[TranslationUnit], out_path: Path) -> None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                src_lines = f.readlines()
        except Exception:
            return

        trans_by_line = {u.line_number: u.translated_text for u in units if u.translated_text}

        out_lines = []
        for line_num, line in enumerate(src_lines, 1):
            if line_num in trans_by_line:
                ending = "\n" if line.endswith("\n") else ""
                out_lines.append(f"{trans_by_line[line_num]}{ending}")
            else:
                out_lines.append(line)

        with open(out_path, "w", encoding="utf-8") as f:
            f.writelines(out_lines)

    def _generate_json(self, file_path: Path, units: List[TranslationUnit], out_path: Path) -> None:
        try:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        except Exception:
            return

        mapping = {u.original_text: u.translated_text for u in units if u.translated_text}
        rebuilt = _replace_json_strings(data, mapping)
        out_path.write_text(
            json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
