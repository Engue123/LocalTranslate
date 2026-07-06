"""
P4 — Ren'Py game structure analyzer (read-only pre-flight).

Inspects a game BEFORE any translation and reports: distribution shape, Ren'Py
version, archives, readable vs unreadable (.rpyc), translatable coverage
(dialogue + strings), and a character-name preview. It writes nothing into the
game and creates no temp files — pure reconnaissance.

It is an aggregation of capabilities the project already has (game-dir
resolution, RPA reader, AST loader, walkers, character-name collector).
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from core.renpy_ast.loader import load_ast_safe
from core.renpy_ast.walker import walk_dialogue, walk_strings, collect_character_names
from core.renpy_ast.mods import classify_mod


def _is_renpy_common(path_str: str) -> bool:
    parts = Path(path_str).parts
    if any(p == "renpy" for p in parts) and any(p == "common" for p in parts):
        return True
    return "renpy/common" in Path(path_str).as_posix()


def _rpyc_version(raw: bytes) -> str:
    return "v2 (Ren'Py 8)" if raw[:10] == b"RENPY RPC2" else "v1 (Ren'Py 6/7)"


@dataclass
class FileStat:
    name: str                 # path relative to game/
    origin: str               # "loose" | "rpa:<archive>"
    loaded: bool
    version: Optional[str] = None
    dialogue: int = 0
    strings: int = 0
    error: Optional[str] = None


@dataclass
class ArchiveStat:
    name: str
    version: Optional[float]
    scripts: int              # .rpy/.rpyc inside


@dataclass
class StructureReport:
    source: Path
    game_dir: Optional[Path]
    shape: List[str] = field(default_factory=list)
    archives: List[ArchiveStat] = field(default_factory=list)
    files: List[FileStat] = field(default_factory=list)
    rpy_only: List[str] = field(default_factory=list)   # .rpy without a .rpyc
    character_names: List[str] = field(default_factory=list)
    mods: List[tuple] = field(default_factory=list)     # (rel_path, category)
    warnings: List[str] = field(default_factory=list)

    # -- aggregates ----------------------------------------------------------
    @property
    def rpyc_ok(self) -> int:
        return sum(1 for f in self.files if f.loaded)

    @property
    def rpyc_failed(self) -> int:
        return sum(1 for f in self.files if not f.loaded)

    @property
    def dialogue_total(self) -> int:
        return sum(f.dialogue for f in self.files)

    @property
    def strings_total(self) -> int:
        return sum(f.strings for f in self.files)

    @property
    def coverage_pct(self) -> int:
        total = self.rpyc_ok + self.rpyc_failed
        return round(100 * self.rpyc_ok / total) if total else 0

    # -- rendering -----------------------------------------------------------
    def summary_lines(self) -> List[str]:
        """Short lines for the GUI log / console."""
        lines = [f"Structure: {', '.join(self.shape) or 'unknown'}"]
        if self.archives:
            lines.append("Archives: " + ", ".join(
                f"{a.name} (RPA {a.version}, {a.scripts} scripts)" for a in self.archives))
        lines.append(
            f"Scripts: {self.rpyc_ok} readable .rpyc"
            + (f", {self.rpyc_failed} unreadable" if self.rpyc_failed else "")
            + (f", {len(self.rpy_only)} .rpy-only" if self.rpy_only else ""))
        lines.append(
            f"Translatable: ~{self.dialogue_total} dialogue + {self.strings_total} strings"
            f"  ·  {len(self.character_names)} character names  ·  coverage {self.coverage_pct}%")
        if self.mods:
            cats = sorted({c for _r, c in self.mods})
            lines.append(f"Mods detected: {len(self.mods)} file(s) [{', '.join(cats)}] "
                         f"— translated too (use exclude-mods to skip).")
        for w in self.warnings[:5]:
            lines.append(f"⚠️  {w}")
        return lines

    def to_markdown(self) -> str:
        out = ["# LocalTranslate — Structure Report", "",
               f"**Source**: `{self.source}`",
               f"**Resolved game/**: `{self.game_dir}`", "",
               "## Shape", ""]
        out += [f"- {s}" for s in (self.shape or ["unknown"])]
        out += ["", "## Coverage", "",
                f"- Readable `.rpyc`: **{self.rpyc_ok}**" + (f" / unreadable: **{self.rpyc_failed}**" if self.rpyc_failed else ""),
                f"- `.rpy`-only (no compiled twin): **{len(self.rpy_only)}**",
                f"- Translatable: **~{self.dialogue_total}** dialogue + **{self.strings_total}** strings",
                f"- Character names: **{len(self.character_names)}**",
                f"- Estimated coverage: **{self.coverage_pct}%**", ""]
        if self.archives:
            out += ["## Archives", ""]
            out += [f"- `{a.name}` — RPA {a.version}, {a.scripts} script(s)" for a in self.archives]
            out.append("")
        if self.mods:
            out += ["## Player mods detected", ""]
            out += [f"- `{rel}` — {cat}" for rel, cat in self.mods]
            out += ["", "_These are translated like any other script; pass exclude-mods "
                    "to skip them._", ""]
        if self.warnings:
            out += ["## Warnings", ""] + [f"- {w}" for w in self.warnings] + [""]
        if self.character_names:
            preview = ", ".join(sorted(self.character_names)[:25])
            out += ["## Character names (glossary preview)", "", preview, ""]
        # Per-file detail (capped)
        out += ["## Files", ""]
        for f in self.files[:60]:
            status = f"{f.version}, {f.dialogue} dlg / {f.strings} str" if f.loaded else f"UNREADABLE ({f.error})"
            out.append(f"- `{f.name}` ({f.origin}) — {status}")
        return "\n".join(out)

    def write(self, output_dir: Path) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "structure_report.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
        return path


def _resolve_game_dir(source: Path) -> Path:
    from plugins.extractors.renpy import RenPyExtractor
    return RenPyExtractor()._resolve_game_dir(Path(source))


def analyze_game(source) -> StructureReport:
    """Read-only structural analysis of a Ren'Py game."""
    source = Path(source)
    game_dir = _resolve_game_dir(source)
    report = StructureReport(source=source, game_dir=game_dir)

    # Distribution shape.
    if any(p.suffix == ".app" for p in [source, *source.glob("*.app")]) or ".app" in str(game_dir):
        report.shape.append("macOS .app bundle")

    loose_rpyc, loose_rpy = [], []
    if game_dir and game_dir.is_dir():
        for root, dirs, files in os.walk(game_dir):
            if "tl" in dirs:
                dirs.remove("tl")
            if _is_renpy_common(root):
                continue
            for fn in files:
                if fn.endswith(".rpyc"):
                    loose_rpyc.append(Path(root) / fn)
                elif fn.endswith(".rpy"):
                    loose_rpy.append(Path(root) / fn)

    # Build the analysis set: (relname, origin, raw_bytes).
    targets = []
    for p in sorted(loose_rpyc):
        rel = p.relative_to(game_dir).as_posix()
        try:
            targets.append((rel, "loose", p.read_bytes()))
        except Exception as e:
            report.warnings.append(f"Cannot read {rel}: {e}")

    rpa_files = sorted(game_dir.rglob("*.rpa")) if game_dir and game_dir.is_dir() else []
    if rpa_files:
        from core.rpa_extractor import RPAExtractor
        for rpa in rpa_files:
            scripts = 0
            version = None
            try:
                with RPAExtractor(rpa) as ar:
                    version = ar.version
                    for fn in ar.list_files():
                        if fn.endswith(".rpyc") and not _is_renpy_common(fn):
                            scripts += 1
                            rel = fn.replace("\\", "/").split("game/")[-1].lstrip("/")
                            targets.append((rel, f"rpa:{rpa.name}", ar.read_file(fn)))
                        elif fn.endswith(".rpy") and not _is_renpy_common(fn):
                            scripts += 1
            except Exception as e:
                report.warnings.append(f"Cannot read archive {rpa.name}: {e}")
            report.archives.append(ArchiveStat(name=rpa.name, version=version, scripts=scripts))

    # Analyze each .rpyc (load + count + names).
    rpyc_rel = {t[0] for t in targets}
    names = set()
    for rel, origin, raw in targets:
        stat = FileStat(name=rel, origin=origin, loaded=False)
        try:
            stat.version = _rpyc_version(raw)
        except Exception:
            pass
        stmts, warn = load_ast_safe(raw)
        if stmts is None:
            stat.error = (warn or "unreadable").split(":")[-1].strip()[:60]
            report.warnings.append(f"Unreadable .rpyc (obfuscated?): {rel}")
        else:
            stat.loaded = True
            if warn:  # loaded only thanks to the deobfuscation fallback
                report.warnings.append(f"Deobfuscated (recovered) .rpyc: {rel}")
            try:
                stat.dialogue = len(walk_dialogue(stmts))
                stat.strings = len(walk_strings(stmts))
                names |= collect_character_names(stmts)
            except Exception as e:
                report.warnings.append(f"Walk failed for {rel}: {e}")
        report.files.append(stat)

    # .rpy-only files (no compiled twin) -> regex fallback, approximate ids.
    for p in sorted(loose_rpy):
        rel = p.relative_to(game_dir).as_posix()
        if rel + "c" not in rpyc_rel and rel not in rpyc_rel:
            report.rpy_only.append(rel)

    report.character_names = sorted(names)

    # Detect player mods (cheat / walkthrough / console / gallery unlock).
    seen_rels = {t[0] for t in targets}
    if game_dir and game_dir.is_dir():
        seen_rels |= {p.relative_to(game_dir).as_posix() for p in loose_rpy}
    for rel in sorted(seen_rels):
        cat = classify_mod(rel)
        if cat:
            report.mods.append((rel, cat))

    # Shape tags.
    if report.archives:
        report.shape.append(f"{len(report.archives)} .rpa archive(s)")
    if loose_rpyc and not loose_rpy:
        report.shape.append(".rpyc only (compiled, no source)")
    elif loose_rpy:
        report.shape.append("loose .rpy source present")
    if not targets and not loose_rpy:
        report.shape.append("no Ren'Py scripts found")
        report.warnings.append("No translatable Ren'Py scripts were found.")
    if report.rpy_only:
        report.warnings.append(
            f"{len(report.rpy_only)} .rpy-only file(s): dialogue ids will be approximate "
            f"(no .rpyc to read exact identifiers).")

    return report
