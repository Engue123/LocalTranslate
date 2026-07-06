"""
Dry-run mode: extracts all translatable units and generates a coverage report
without calling the LLM. Useful for auditing a RenPy project before translation.
"""
from pathlib import Path
from typing import Dict, List
from collections import Counter
from core.models import TranslationUnit


class DryRunReport:
    def __init__(self, units: List[TranslationUnit]):
        self.units = units
        self.by_file: Dict[str, List[TranslationUnit]] = {}
        for u in units:
            self.by_file.setdefault(str(u.file_path), []).append(u)

    def to_markdown(self) -> str:
        lines = [
            "# LocalTranslate — Dry-Run Report",
            "",
            f"**Total units found**: {len(self.units)} (Total units found: {len(self.units)})",
            "",
            "## Breakdown by type",
        ]
        type_counts = Counter(u.unit_type.value for u in self.units)
        for t, c in sorted(type_counts.items()):
            lines.append(f"- `{t}`: {c}")
        lines.append("")
        lines.append("## Breakdown by file")
        for fp, us in sorted(self.by_file.items()):
            lines.append(f"- `{Path(fp).name}`: {len(us)} units")
        lines.append("")
        lines.append("## First 10 sample units")
        for u in self.units[:10]:
            lines.append(f"- `{u.unit_type.value}` (line {u.line_number}): `{u.source_text[:80]}`")
        return "\n".join(lines)

    def write(self, output_dir: Path) -> Path:
        path = output_dir / "dryrun_report.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
        return path
