"""
Incremental translation engine.
Compares previously translated output with new source to find only
new or changed strings, avoiding re-translation of unchanged content.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Set
from core.models import TranslationUnit, UnitType


def load_previous_strings(tl_dir: Path) -> Dict[str, str]:
    """
    Scan an existing tl/ directory and build a map of source_text -> translated_text.
    Handles both Mode A (translate blocks) and Mode B (strings blocks).
    """
    known: Dict[str, str] = {}
    if not tl_dir.exists():
        return known

    # Handle Mode A and Mode B files
    for rpy in tl_dir.rglob("*.rpy"):
        try:
            lines = rpy.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        
        in_strings_block = False
        old_buffer = None
        
        for idx, line in enumerate(lines):
            stripped = line.strip()
            
            # Mode B detection: strings blocks
            if "translate" in stripped and "strings:" in stripped:
                in_strings_block = True
                continue
            
            if in_strings_block:
                # old "Hello"
                # new "Bonjour"
                if stripped.startswith("old "):
                    first_q = stripped.find('"')
                    last_q = stripped.rfind('"')
                    if first_q != -1 and last_q != -1 and first_q < last_q:
                        old_buffer = stripped[first_q + 1:last_q]
                elif stripped.startswith("new ") and old_buffer is not None:
                    first_q = stripped.find('"')
                    last_q = stripped.rfind('"')
                    if first_q != -1 and last_q != -1 and first_q < last_q:
                        new_text = stripped[first_q + 1:last_q]
                        known[old_buffer] = new_text
                        old_buffer = None
            else:
                # Mode A detection: standard dialogue blocks
                if stripped.startswith("#") and ('"' in stripped or "'" in stripped):
                    comment_content = stripped[1:].strip()
                    first_q = comment_content.find('"')
                    last_q = comment_content.rfind('"')
                    if first_q == -1 or last_q == -1 or first_q >= last_q:
                        first_q = comment_content.find("'")
                        last_q = comment_content.rfind("'")
                    
                    if first_q != -1 and last_q != -1 and first_q < last_q:
                        old_text = comment_content[first_q + 1:last_q]
                        for next_idx in range(idx + 1, min(idx + 5, len(lines))):
                            next_line = lines[next_idx].strip()
                            if not next_line or next_line.startswith("#"):
                                continue
                            if next_line.startswith("translate") or next_line.endswith(":"):
                                break
                            
                            next_first_q = next_line.find('"')
                            next_last_q = next_line.rfind('"')
                            if next_first_q == -1 or next_last_q == -1 or next_first_q >= next_last_q:
                                next_first_q = next_line.find("'")
                                next_last_q = next_line.rfind("'")
                            
                            if next_first_q != -1 and next_last_q != -1 and next_first_q < next_last_q:
                                new_text = next_line[next_first_q + 1:next_last_q]
                                known[old_text] = new_text
                                break
                        
    return known


def compute_diff(units: List[TranslationUnit], known: Dict[str, str]) -> List[TranslationUnit]:
    """
    Filter units: keep only those whose source_text is NOT in known.
    """
    diff: List[TranslationUnit] = []
    for u in units:
        if u.source_text not in known:
            diff.append(u)
    return diff


def filter_untranslated(
    units: List[TranslationUnit],
    covered_ids: Dict[str, str],
    covered_olds: Set[str],
) -> List[TranslationUnit]:
    """DELTA mode: drop units an existing patch already covers, so we translate
    ONLY the gap (e.g. a 3rd-party FR patch at v0.75, game now at v0.95).

    Dialogue is matched by EXACT identifier (`renpy_id` — desync-immune, handles
    duplicate texts); `strings:`-channel units (menu/UI) by exact source text.
    The two channels never cross: a dialogue line is never dropped because its
    text happens to equal a translated UI string, and vice-versa.

    `covered_ids` maps id -> the source text it was translated from. A line whose
    id is reused but whose SOURCE CHANGED between versions is RE-translated (the
    old translation is stale). When the stored source is unknown (empty) we keep
    the existing translation (skip) — the safe, non-destructive default.
    """
    kept: List[TranslationUnit] = []
    for u in units:
        rid = (u.metadata or {}).get("renpy_id")
        if u.unit_type == UnitType.DIALOGUE:
            if rid and rid in covered_ids:
                old_src = covered_ids[rid]
                if not (old_src and old_src != u.original_text):
                    continue              # same (or unknown) source -> already done
                # source changed -> fall through, keep it for re-translation
        else:  # MENU / UI_STRING -> strings: channel, matched by source text
            if u.original_text in covered_olds:
                continue
        kept.append(u)
    return kept
