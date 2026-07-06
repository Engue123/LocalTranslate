"""
Post-translation quality checks.
Flags untranslated strings, broken placeholders, and suspicious outputs.
"""
import re
from collections import Counter
from pathlib import Path
from typing import List, Dict
from core.models import TranslationUnit


class QualityReport:
    def __init__(self, units: List[TranslationUnit]):
        self.units = units
        self.issues: List[Dict] = []

    def check_all(self) -> None:
        for u in self.units:
            self._check_untranslated(u)
            self._check_placeholders(u)
            self._check_length_ratio(u)

    def _check_untranslated(self, u: TranslationUnit) -> None:
        src = u.source_text.strip().lower()
        tgt = (u.translated_text or "").strip().lower()
        if src == tgt and len(src) > 3 and src.isalpha():
            self.issues.append({
                "type": "untranslated",
                "unit_id": u.unit_id,
                "text": u.source_text,
            })

    def _check_placeholders(self, u: TranslationUnit) -> None:
        if not u.translated_text:
            return
        src_vars = set(re.findall(r'\[[\w_]+\]', u.source_text))
        tgt_vars = set(re.findall(r'\[[\w_]+\]', u.translated_text))
        if src_vars != tgt_vars:
            self.issues.append({
                "type": "placeholder_mismatch",
                "unit_id": u.unit_id,
                "expected": src_vars,
                "got": tgt_vars,
            })

    def _check_length_ratio(self, u: TranslationUnit) -> None:
        if not u.translated_text:
            return
        ratio = len(u.translated_text) / max(1, len(u.source_text))
        if ratio > 3.0 or ratio < 0.3:
            self.issues.append({
                "type": "suspicious_length",
                "unit_id": u.unit_id,
                "ratio": round(ratio, 2),
            })

    def write(self, output_dir: Path) -> Path:
        path = output_dir / "quality_report.md"
        total = len(self.units)
        by_type = Counter(i["type"] for i in self.issues)
        # Honest "identical to source" rate. The strict `untranslated` ISSUE check
        # requires the WHOLE string be alphabetic, so it misses phrases and markup
        # kept in English ("Are you ready?", "{i}need{/i}…") and would report ~0% on a
        # real game. Count the true identical-to-source rate here — it legitimately
        # includes proper names and onomatopoeia that stay unchanged (hence the
        # caveat: this is visibility, not all error).
        kept = sum(1 for u in self.units
                   if u.translated_text is not None
                   and u.source_text.strip().lower() == u.translated_text.strip().lower()
                   and any(c.isalpha() for c in u.source_text))
        kept_pct = (100.0 * kept / total) if total else 0.0
        lines = [
            "# Quality Report",
            "",
            f"- **Units audited**: {total}",
            f"- **Identical to source** (kept unchanged — incl. names/onomatopoeia): "
            f"{kept} ({kept_pct:.1f}%)",
            f"- **Untranslated words flagged** (single all-alpha tokens): "
            f"{by_type.get('untranslated', 0)}",
            f"- **Placeholder mismatches**: {by_type.get('placeholder_mismatch', 0)}",
            f"- **Suspicious length**: {by_type.get('suspicious_length', 0)}",
            f"- **Total issues**: {len(self.issues)}",
            "",
        ]
        for issue in self.issues:
            lines.append(f"- `{issue['type']}` — {issue.get('unit_id', '')}")
            if "text" in issue:
                lines.append(f"  Text: `{issue['text']}`")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
