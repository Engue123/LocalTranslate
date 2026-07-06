import os
import json
import time
import threading
import inspect
from pathlib import Path
from typing import Any, List, Optional, Callable, Dict

from core.models import TranslationUnit, EngineResult, UnitType
from core.translator import BaseTranslator


def _is_renpy_common(path_str: str) -> bool:
    parts = Path(path_str).parts
    if any(p == "renpy" for p in parts) and any(p == "common" for p in parts):
        return True
    return "renpy/common" in Path(path_str).as_posix()


def _archive_rel(fn: str) -> str:
    """Normalize an in-archive path to a game-relative path (strip leading game/)."""
    s = fn.replace("\\", "/")
    if s.startswith("game/"):
        s = s[5:]
    elif s.startswith("/game/"):
        s = s[6:]
    return s.lstrip("/")


def _collect_renpy_jobs(game_dir: Path, output_path: Path, log_cb, result):
    """
    Collect Ren'Py translation jobs from a resolved game/ directory.

    Returns (jobs, temp_paths) where each job is (rel_path_str, kind, payload):
      - kind "rpyc": payload = (rpyc_source, rpy_fallback_or_None)  [AST path, exact ids]
      - kind "rpy" : payload = path to a .rpy                       [legacy regex fallback]

    The compiled .rpyc is always preferred: it is the AST the engine actually
    runs and can desync from the .rpy text, so identifiers must come from it.
    """
    sources: Dict[str, Dict[str, Any]] = {}   # rel(.rpy) -> {"rpyc": ..., "rpy": ...}
    temp_paths: List[Path] = []

    def add(rel: str, kind: str, payload: Any) -> None:
        sources.setdefault(rel, {}).setdefault(kind, payload)

    # macOS writes "._foo" AppleDouble sidecars (and .DS_Store) when a game is
    # copied to exFAT/FAT external drives. They are NOT game files — "._x.rpy"
    # would be parsed as a script and "._archive.rpa" as an archive, producing
    # junk and spurious errors. Skip them everywhere, so the drive never needs
    # manual cleaning first.
    def _is_junk(name: str) -> bool:
        return name.startswith("._") or name == ".DS_Store"

    # 1. Loose files on disk (skip tl/ and renpy/common).
    if game_dir.is_dir():
        for root, dirs, files in os.walk(game_dir):
            if "tl" in dirs:
                dirs.remove("tl")
            if _is_renpy_common(root):
                continue
            for f in files:
                if _is_junk(f):
                    continue
                p = Path(root) / f
                if f.endswith(".rpyc"):
                    add(p.relative_to(game_dir).with_suffix(".rpy").as_posix(), "rpyc", p)
                elif f.endswith(".rpy"):
                    add(p.relative_to(game_dir).as_posix(), "rpy", p)

    # 2. Scripts packed inside .rpa archives (skip AppleDouble "._*.rpa" junk).
    rpa_files = [p for p in sorted(game_dir.rglob("*.rpa"))
                 if not _is_junk(p.name)] if game_dir.is_dir() else []
    if rpa_files:
        from core.rpa_extractor import RPAExtractor
        rpy_temp = None
        log_cb(0.06, f"Found {len(rpa_files)} RPA archive(s). Scanning for scripts...")
        for rpa in rpa_files:
            try:
                with RPAExtractor(rpa) as ar:
                    for fn in ar.list_files():
                        if _is_renpy_common(fn):
                            continue
                        if fn.endswith(".rpyc"):
                            rel = _archive_rel(fn)[:-1]  # .rpyc -> .rpy
                            add(rel, "rpyc", ar.read_file(fn))  # bytes
                        elif fn.endswith(".rpy"):
                            rel = _archive_rel(fn)
                            if rpy_temp is None:
                                rpy_temp = output_path / "temp_decompilation" / "rpa_rpy"
                                rpy_temp.mkdir(parents=True, exist_ok=True)
                                temp_paths.append(rpy_temp)
                            dest = rpy_temp / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(ar.read_file(fn))
                            add(rel, "rpy", dest)
            except Exception as e:
                # Non-fatal: a single unreadable/custom-packed archive must NOT
                # fail the whole run or skip finalize — record a warning and go on.
                log_cb(0.06, f"Warning: failed to read RPA archive {rpa.name}: {e}")
                result.warnings.append(f"RPA archive skipped ({rpa.name}): {e}")

    jobs = []
    for rel in sorted(sources):
        d = sources[rel]
        if "rpyc" in d:
            jobs.append((rel, "rpyc", (d["rpyc"], d.get("rpy"))))
        elif "rpy" in d:
            jobs.append((rel, "rpy", d["rpy"]))
    return jobs, temp_paths


def _collect_renpy_glossary(jobs) -> Dict[str, str]:
    """Collect character display names across all .rpyc jobs, locked to themselves
    so they stay consistent / untranslated (terminology lever)."""
    from core.renpy_ast import load_ast, collect_character_names
    names = set()
    for _rel, kind, payload in jobs:
        if kind != "rpyc":
            continue
        try:
            names |= collect_character_names(load_ast(payload[0], try_harder=True))
        except Exception:
            pass
    return {n: n for n in names if n and len(n) <= 40}


def _collect_renpy_speaker_contexts(jobs, mc_gender):
    """One AST pass over all .rpyc jobs -> (per-tag {speaker, gender} contexts,
    addressee resolver) for the Quality tier: character defs (tag -> name),
    rename-variable defaults ([mi] -> "Miji"), pronoun-scan gender inference for
    the cast, declared gender for the MC. The resolver closes over the inferred
    cast genders so a per-line addressee gender ("tu es belle") can be derived
    from the text when the line names the listener."""
    from core.renpy_ast import load_ast, collect_character_defs, walk_dialogue
    from core.renpy_ast.gender import (
        collect_name_vars, infer_cast_genders, build_speaker_contexts,
        resolve_addressee_gender,
    )
    defs: Dict[str, str] = {}
    name_vars: Dict[str, str] = {}
    units = []
    for _rel, kind, payload in jobs:
        if kind != "rpyc":
            continue
        try:
            stmts = load_ast(payload[0], try_harder=True)
            defs.update(collect_character_defs(stmts))
            name_vars.update(collect_name_vars(stmts))
            units.extend(walk_dialogue(stmts))
        except Exception:
            pass
    cast_genders = infer_cast_genders(defs, units)
    ctx_map = build_speaker_contexts(defs, mc_gender, cast_genders, name_vars)

    def addressee_of(text):
        return resolve_addressee_gender(text, mc_gender, cast_genders)

    return ctx_map, addressee_of


class TranslationEngine:
    """Coordinates the chunked extraction, translation, and output generation process."""
    
    def __init__(
        self,
        source_dir: Path,
        output_dir: Path,
        source_lang: str = "en",
        target_lang: str = "fr",
        mode: str = "A",
        translator: Any = None,
        fallback_translator: Any = None,
        style_hint: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        translator_type: str = "mock",
        mc_gender: Optional[str] = None,
        **kwargs
    ):
        self.source_dir = Path(source_dir) if source_dir else None
        self.output_dir = Path(output_dir) if output_dir else None
        self.source_lang = source_lang.lower() if source_lang else "en"
        self.target_lang = target_lang.lower() if target_lang else "fr"
        self.mode = mode.upper() if mode else "A"
        self.translator = translator
        # Optional pure-MT fallback (q6): translate_preserving_tags tries it when the
        # primary model drops masked markup, before keeping the English original.
        self.fallback_translator = fallback_translator
        self.style_hint = style_hint
        self.progress_callback = progress_callback
        self.translator_type = translator_type.lower() if translator_type else "mock"
        # Declared main-character gender ("m"/"f"/…, normalized downstream). Only
        # an instruction-following model can apply it.
        self.mc_gender = mc_gender

    def _init_translator(self) -> BaseTranslator:
        from core.translator import MockTranslator, LlamaCppTranslator
        if self.translator_type == "llama":
            return LlamaCppTranslator(
                source_lang=self.source_lang,
                target_lang=self.target_lang,
                style_hint=self.style_hint
            )
        else:
            return MockTranslator(style_hint=self.style_hint)

    def run(
        self,
        source_path: Optional[Path] = None,
        output_path: Optional[Path] = None,
        extractor: Optional[Any] = None,
        generator: Optional[Any] = None,
        translator: Optional[Any] = None,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
        batch_size: int = 5,
        cancel_event: Optional[threading.Event] = None,
        state_file: Optional[Path] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        exclude_mods: bool = False,
        full: bool = False
    ) -> EngineResult:
        source_path = Path(source_path) if source_path is not None else self.source_dir
        output_path = Path(output_path) if output_path is not None else self.output_dir
        src_lang = source_lang if source_lang is not None else self.source_lang
        tgt_lang = target_lang if target_lang is not None else self.target_lang
        active_translator = translator if translator is not None else self.translator
        
        if active_translator is None:
            active_translator = self._init_translator()

        output_path.mkdir(parents=True, exist_ok=True)
        result = EngineResult(output_dir=output_path)

        # We need a log callback helper
        def log_cb(progress: float, message: str) -> None:
            if progress_callback:
                progress_callback(progress, message)
            elif self.progress_callback:
                self.progress_callback(progress, message)
            else:
                print(f"[{int(progress * 100):3d}%] {message}")

        log_cb(0.0, "Starting translation engine...")

        # Detect extractor / generator if None
        detected_generator = False
        if extractor is None or generator is None:
            detected_generator = True
            has_renpy = False
            if source_path.is_file():
                if source_path.suffix in (".rpy", ".rpa"):
                    has_renpy = True
            else:
                has_renpy = (
                    any(source_path.rglob("*.rpy")) or 
                    any(source_path.rglob("*.rpyc")) or 
                    any(source_path.rglob("*.rpa"))
                )
                
            if has_renpy:
                from plugins.extractors.renpy import RenPyExtractor
                from plugins.generators.renpy import RenPyGenerator
                extractor = RenPyExtractor()
                generator = RenPyGenerator()
            else:
                from plugins.extractors.plaintext import PlainTextExtractor
                from plugins.generators.plaintext import PlainTextGenerator
                extractor = PlainTextExtractor()
                generator = PlainTextGenerator()

        # Build the list of translation jobs: (rel_path_str, kind, payload).
        # For Ren'Py we prefer the compiled .rpyc (authoritative AST, exact ids).
        jobs = []
        temp_paths: List[Path] = []
        game_dir = None
        extractor_name = extractor.__class__.__name__

        if source_path.is_file():
            suffix = source_path.suffix.lower()
            if suffix == ".rpyc":
                jobs.append((Path(source_path.name).with_suffix(".rpy").as_posix(),
                             "rpyc", (source_path, None)))
            elif suffix == ".rpy":
                jobs.append((source_path.name, "rpy", source_path))
            else:
                jobs.append((source_path.name, "plain", source_path))
        elif extractor_name == "RenPyExtractor":
            game_dir = extractor._resolve_game_dir(source_path)
            jobs, temp_paths = _collect_renpy_jobs(game_dir, output_path, log_cb, result)
        else:
            for ext in ("*.txt", "*.md", "*.json"):
                for p in sorted(source_path.rglob(ext)):
                    try:
                        rel = p.relative_to(source_path).as_posix()
                    except ValueError:
                        rel = p.name
                    jobs.append((rel, "plain", p))

        # Stable de-dup by relative path.
        seen_rel = set()
        jobs = [j for j in jobs if not (j[0] in seen_rel or seen_rel.add(j[0]))]

        # Optionally drop player mods (cheat/walkthrough/console/…).
        if exclude_mods and extractor_name == "RenPyExtractor":
            from core.renpy_ast.mods import classify_mod
            before = len(jobs)
            jobs = [j for j in jobs if not classify_mod(j[0])]
            dropped = before - len(jobs)
            if dropped:
                log_cb(0.04, f"Excluding {dropped} mod file(s) from translation.")

        if not jobs:
            result.errors.append(f"No files found to translate in {source_path}")
            log_cb(1.0, "Error: No files found.")
            return result

        log_cb(0.05, f"Found {len(jobs)} files to translate.")

        # DELTA mode: auto-detect an existing tl/<lang> patch and translate ONLY the
        # gap (a 3rd-party patch at an older version, or our own previous run = free
        # incremental). The lower-risk default: completing is non-destructive (the
        # existing translations stay; we only add) where a silent FULL re-translate
        # would risk a "translation already exists" collision. `full=True` opts out.
        delta_ids: Dict[str, str] = {}
        delta_olds: set = set()
        delta_subdir: Optional[str] = None
        if extractor_name == "RenPyExtractor" and game_dir is not None and not full:
            try:
                from plugins.generators.renpy import (
                    detect_existing_patch, scan_existing_translation)
                existing = detect_existing_patch(game_dir, tgt_lang)
                if existing is not None:
                    delta_ids, delta_olds = scan_existing_translation(existing)
                    tgt_lang = existing.name        # complete THAT folder (e.g. french)
                    delta_subdir = "localtranslate_delta"
                    log_cb(0.05,
                           f"DELTA: existing '{existing.name}' patch found "
                           f"({len(delta_ids)} dialogue ids, {len(delta_olds)} strings). "
                           f"Translating ONLY the missing lines into "
                           f"tl/{existing.name}/{delta_subdir}/ — this COMPLETES the "
                           f"existing patch (ship both together). Use --full to "
                           f"re-translate everything.")
            except Exception as e:
                log_cb(0.05, f"Delta detection skipped: {e}")

        # Terminology glossary: lock character names for consistency (Ren'Py only).
        if extractor_name == "RenPyExtractor" and hasattr(active_translator, "glossary"):
            try:
                glossary = _collect_renpy_glossary(jobs)
                if glossary:
                    active_translator.glossary = {**glossary, **active_translator.glossary}
                    log_cb(0.05, f"Glossary: locked {len(glossary)} character name(s).")
            except Exception as e:
                log_cb(0.05, f"Glossary collection skipped: {e}")

        # Quality tier: per-line speaker/gender contexts (instruct models only —
        # a pure-MT model has no channel for context).
        ctx_map: Dict[str, dict] = {}
        addressee_resolver = None
        is_instruct = bool(getattr(active_translator, "instruct", False))
        if extractor_name == "RenPyExtractor" and is_instruct:
            try:
                ctx_map, addressee_resolver = _collect_renpy_speaker_contexts(jobs, self.mc_gender)
                if ctx_map:
                    n_gendered = sum(1 for c in ctx_map.values() if c.get("gender"))
                    log_cb(0.05, f"Context: {len(ctx_map)} character tag(s); gender "
                                 f"known for {n_gendered} (declared MC + pronoun scan).")
            except Exception as e:
                log_cb(0.05, f"Speaker-context collection skipped: {e}")
        elif self.mc_gender and extractor_name == "RenPyExtractor":
            log_cb(0.05, "Note: MC gender is declared but the active model is pure-MT "
                         "(it cannot use context) — select the Quality tier model.")

        def _unit_ctx(u) -> Optional[dict]:
            if not ctx_map or u.unit_type != UnitType.DIALOGUE:
                return None
            return ctx_map.get(u.character)

        def _unit_key(u) -> tuple:
            # De-dup key: same text is re-translated only when its prompt context
            # differs (speaker gender changes the agreement). MT mode: (text, None, None).
            c = _unit_ctx(u) or {}
            return (u.original_text, c.get("speaker"), c.get("gender"))

        # Load state registry
        state_file_path = state_file or (output_path / ".translate_state.json")
        state: Dict[str, list] = {}
        if state_file_path.exists():
            try:
                state = json.loads(state_file_path.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        if "completed_files" not in state:
            state["completed_files"] = []
        completed_files = set(state["completed_files"])

        total_files = len(jobs)
        start_time = time.time()

        for file_idx, (rel_path_str, kind, payload) in enumerate(jobs):
            if cancel_event and cancel_event.is_set():
                result.errors.append("Cancelled by user")
                log_cb(1.0, "Cancelled")
                return result

            pct = 0.1 + (file_idx / total_files * 0.8) if total_files else 0.1
            
            if rel_path_str in completed_files:
                log_cb(pct, f"Skipping completed file: {rel_path_str}")
                continue

            log_cb(pct, f"Processing file ({file_idx+1}/{total_files}): {rel_path_str}")

            # Extract units for this job. Ren'Py .rpyc -> AST (exact ids);
            # fall back to the .rpy regex path if the AST cannot be read.
            try:
                if kind == "rpyc":
                    rpyc_src, rpy_fallback = payload
                    try:
                        file_units = extractor.extract_rpyc(rpyc_src, Path(rel_path_str))
                    except Exception as e:
                        if rpy_fallback is not None:
                            log_cb(pct, f"AST unreadable for {rel_path_str} ({e}); using .rpy text")
                            file_units = extractor.extract_rpy(rpy_fallback, Path(rel_path_str))
                        else:
                            raise
                elif kind == "rpy":
                    file_units = extractor.extract_rpy(payload, Path(rel_path_str))
                else:
                    file_units = extractor.extract(payload)
            except Exception as e:
                result.errors.append(f"Extraction failed for {rel_path_str}: {e}")
                break

            # DELTA: drop units the existing patch already covers (dialogue by id,
            # strings by source text; a changed source for a reused id is re-translated).
            if delta_subdir and file_units and (delta_ids or delta_olds):
                from core.diff_engine import filter_untranslated
                before = len(file_units)
                file_units = filter_untranslated(file_units, delta_ids, delta_olds)
                skipped = before - len(file_units)
                if skipped:
                    log_cb(pct, f"  Delta: skipped {skipped} already-translated unit(s) "
                                f"in {rel_path_str}.")

            if not file_units:
                completed_files.add(rel_path_str)
                state["completed_files"] = list(completed_files)
                state_file_path.write_text(json.dumps(state, indent=4), encoding="utf-8")
                continue

            # Update metrics
            for u in file_units:
                if u.unit_type == UnitType.DIALOGUE:
                    result.dialogue_extracted += 1
                elif u.unit_type == UnitType.MENU:
                    result.menu_extracted += 1
                else:
                    result.ui_extracted += 1

            # Translate each unique work item: (text, speaker, gender) -> context.
            work: Dict[tuple, Optional[dict]] = {}
            for u in file_units:
                if u.original_text and u.original_text.strip():
                    work.setdefault(_unit_key(u), _unit_ctx(u))
            translated_map: Dict[tuple, str] = {}
            warnings_map: Dict[tuple, list] = {}
            file_failed = False

            kwargs = {}
            if self.style_hint:
                kwargs["style_hint"] = self.style_hint
            base_seed = getattr(active_translator, "seed", 42)
            is_renpy = extractor_name == "RenPyExtractor"

            def _cancelled() -> bool:
                if cancel_event and cancel_event.is_set():
                    result.errors.append("Cancelled by user")
                    log_cb(1.0, "Cancelled")
                    return True
                return False

            try:
                if is_renpy:
                    # Per-text tag-safe translation: guarantees Ren'Py markup is
                    # preserved exactly, or falls back to the original (never broken).
                    from core.safeguards import translate_preserving_tags
                    from core.postedit import fix_french_elision
                    french = str(self.target_lang).lower().startswith("fr")
                    for key, ctx in work.items():
                        if _cancelled():
                            return result
                        text = key[0]
                        kw = dict(kwargs)
                        # Per-line addressee gender ("tu es belle"): derived from THIS
                        # text (an [mc] ref / trailing vocative names the listener).
                        # Conservative — None on most lines (no clear signal).
                        addr = addressee_resolver(text) if addressee_resolver else None
                        if ctx or addr:
                            merged = dict(ctx) if ctx else {}
                            if addr:
                                merged["addressee_gender"] = addr
                            kw["contexts"] = [merged]
                        result_text, ok = translate_preserving_tags(
                            active_translator, text, base_seed=base_seed,
                            fallback=self.fallback_translator, **kw
                        )
                        # Deterministic French cleanup, only on genuinely translated
                        # text (never on an L5 fallback-to-original English line).
                        if ok and french:
                            result_text = fix_french_elision(result_text)
                        translated_map[key] = result_text
                        if not ok:
                            warnings_map[key] = [
                                "Ren'Py markup could not be preserved; kept original text."
                            ]
                else:
                    unique_texts = [k[0] for k in work]   # plaintext: context-free keys
                    for i in range(0, len(unique_texts), batch_size):
                        if _cancelled():
                            return result
                        batch = unique_texts[i:i + batch_size]
                        translated_batch = active_translator.translate_batch(batch, **kwargs)
                        for idx, text in enumerate(batch):
                            if translated_batch[idx] is not None:
                                translated_map[(text, None, None)] = translated_batch[idx]
            except Exception as e:
                result.errors.append(f"Translation failure in {rel_path_str}: {e}")
                file_failed = True

            if file_failed:
                break

            # Assign translated text (and flag units whose tags/placeholders were
            # lost or mangled by the translator so they surface for review).
            for u in file_units:
                key = _unit_key(u)
                if key in translated_map:
                    u.translated_text = translated_map[key]
                    lost = warnings_map.get(key)
                    if lost:
                        u.warnings.extend(lost)
                        u.needs_review = True
                        log_cb(pct, f"⚠️  {rel_path_str}:{u.line_number} — {lost[0]}")

            # Generate output for this file
            try:
                if generator.__class__.__name__ == "PlainTextGenerator":
                    generator.is_phase3 = not detected_generator
                    
                sig = inspect.signature(generator.generate)
                gkw = dict(translated_units=file_units, source_dir=source_path,
                           output_dir=output_path, target_lang=tgt_lang)
                if "mode" in sig.parameters:
                    gkw["mode"] = self.mode
                if delta_subdir and "output_subdir" in sig.parameters:
                    gkw["output_subdir"] = delta_subdir
                generator.generate(**gkw)
            except Exception as e:
                result.errors.append(f"Generation failed for {rel_path_str}: {e}")
                break

            result.units.extend(file_units)
            result.files_parsed.append(Path(rel_path_str))

            # Mark as completed and save state
            completed_files.add(rel_path_str)
            state["completed_files"] = list(completed_files)
            state_file_path.write_text(json.dumps(state, indent=4), encoding="utf-8")

            # Calculate ETA
            idx = file_idx + 1
            elapsed = time.time() - start_time
            avg_per_file = elapsed / idx if idx > 0 else 0
            remaining = avg_per_file * (total_files - idx)
            eta_str = f"~{int(remaining)}s remaining"
            log_cb(0.1 + 0.9 * (idx / total_files), f"Processed {idx}/{total_files} files — {eta_str}")

        # Sweep macOS "._*"/.DS_Store sidecars out of OUR output (exFAT drives
        # spawn them next to every file we write; shipped as-is they'd break
        # Ren'Py, which compiles every .rpy under tl/). Best-effort; the drive
        # may recreate them, so the install note still says to run dot_clean.
        if extractor_name == "RenPyExtractor":
            try:
                tl_root = output_path / "game" / "tl"
                if tl_root.is_dir():
                    junk = [p for p in tl_root.rglob("*")
                            if p.name.startswith("._") or p.name == ".DS_Store"]
                    for p in junk:
                        try:
                            p.unlink()
                        except OSError:
                            pass
                    if junk:
                        log_cb(0.96, f"Removed {len(junk)} macOS sidecar file(s) "
                                     f"(._*/.DS_Store) from the patch.")
            except Exception:
                pass

        # A patch exists if we produced units this run OR resumed a prior run.
        # Finalize/validate are gated on THAT, not on `not result.errors`: a
        # non-fatal warning (e.g. a bad archive) must never skip the language
        # selector or leave the run "failed" with everything actually translated.
        # This also lets an interrupted run be FINALIZED by simply re-running.
        have_patch = bool(result.units or completed_files)

        # Produce turnkey extras once (language button + install instructions).
        if have_patch and hasattr(generator, "finalize"):
            try:
                generator.finalize(output_path, tgt_lang)
                log_cb(0.97, "Generated language switcher + install instructions.")
            except Exception as e:
                result.errors.append(f"Finalize step failed: {e}")

        # Font adaptation: a translation is useless if the game's font can't draw the
        # target language (Latin accents, Cyrillic, or CJK = tofu). Ship a covering
        # font + override when needed. Best-effort — never fails the run.
        if have_patch and game_dir is not None and generator.__class__.__name__ == "RenPyGenerator":
            try:
                from core.fonts import apply_font_fix
                apply_font_fix(
                    game_dir, output_path, tgt_lang,
                    decompiled_dir=output_path / "temp_decompilation",
                    bundled_dir=Path(__file__).resolve().parent.parent / "resources" / "fonts",
                    log=lambda m: log_cb(0.97, m),
                    warn=lambda m: result.warnings.append(m))
            except Exception as e:
                log_cb(0.97, f"Font adaptation skipped: {e}")

        # Patch validation: catch the structural errors Ren'Py rejects at load
        # time (duplicate string `old`, duplicate dialogue id, unterminated quoted
        # string) BEFORE the player ever runs the game. Never ship a broken patch.
        if have_patch and generator.__class__.__name__ == "RenPyGenerator":
            try:
                from plugins.generators.renpy import validate_patch
                problems = validate_patch(output_path / "game" / "tl" / tgt_lang, tgt_lang)
                if problems:
                    for p in problems[:20]:
                        result.errors.append(f"Invalid Ren'Py patch — {p}")
                    log_cb(0.98, f"⚠️ Patch validation found {len(problems)} structural problem(s).")
                else:
                    log_cb(0.98, "Patch validation passed — no duplicate or unterminated strings.")
            except Exception as e:
                log_cb(0.98, f"Warning: patch validation could not run: {e}")

        # Post-translation quality audit (untranslated, lost placeholders, ratios).
        # Run on THIS run's units, or — on a resume/Mock run that produced none —
        # reconstruct them from the on-disk patch, so a finished patch's wording
        # quality is never left invisible (the report was previously skipped whenever
        # result.units was empty, i.e. every resume).
        audit_units = result.units
        if not audit_units and have_patch and generator.__class__.__name__ == "RenPyGenerator":
            try:
                from plugins.generators.renpy import reconstruct_units_from_patch
                audit_units = reconstruct_units_from_patch(
                    output_path / "game" / "tl" / tgt_lang)
            except Exception as e:
                log_cb(0.99, f"Warning: could not read patch for quality report: {e}")
        if audit_units:
            try:
                from core.quality_check import QualityReport
                report = QualityReport(audit_units)
                report.check_all()
                report.write(output_path)
                if report.issues:
                    log_cb(0.99, f"Quality report: {len(report.issues)} item(s) to review.")
            except Exception as e:
                log_cb(0.99, f"Warning: quality report failed: {e}")

        # Surface non-fatal warnings (skipped archives, etc.) without failing the run.
        if result.warnings:
            log_cb(0.99, f"Completed with {len(result.warnings)} non-fatal warning(s) "
                         f"(e.g. skipped archives) — see below; the patch is still valid.")

        # Cleanup state file on successful completion
        if not result.errors and len(completed_files) == total_files:
            if state_file_path.exists():
                state_file_path.unlink()

        # Cleanup temporary paths if any
        if 'temp_paths' in locals() and temp_paths:
            log_cb(0.98, "Cleaning up temporary decompiled files...")
            from core.decompiler import cleanup_temp_paths
            cleanup_temp_paths(temp_paths)

        return result
