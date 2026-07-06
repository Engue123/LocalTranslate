# Architecture

LocalTranslate is a layered pipeline. The **engine** is the single orchestrator;
the GUI and CLI are thin drivers. Plugins (extractors/generators) make new file
formats pluggable. The **`core/renpy_ast/` subsystem** is the heart: it reads the
compiled `.rpyc` AST and computes the *exact* identifiers Ren'Py uses.

> See `README.md` for features, install and usage, and `DESIGN_RENPY.md` for the
> AST-first design rationale.

## Modules & responsibilities

| Module | Responsibility |
|---|---|
| `main.py` | Entry point. CLI args (`--model q6/nemo`, …); launches GUI when none given. |
| `ui/app.py` | CustomTkinter GUI: dirs, project type, target lang, backend, **model selector** (status + ⓘ + download), progress bar, log, Run / Dry-Run / Cancel. |
| `core/engine.py` | `TranslationEngine`: builds **jobs** (`.rpyc` preferred over `.rpy`, incl. `.rpa`), glossary pre-pass, per-text **tag-safe** translate, `finalize()`, quality report, resumable state. |
| `core/pipeline.py` | `TranslationPipeline` — alias of `TranslationEngine`. |
| `core/models.py` | `TranslationUnit`, `ParseResult`, `EngineResult`. |
| `core/translator.py` | `BaseTranslator`, `MockTranslator`, `LlamaCppTranslator` — two modes: **MT** (official HY-MT prompt + sampling + terminology/glossary, the universal tier) and **instruct** (`instruct=True` → `create_chat_completion` + `build_instruct_system_prompt`: multilingual VN system prompt with speaker gender / register / frozen tags, the Quality tier). `language_name()`. |
| `core/safeguards.py` | `TagProtector` mask/unmask; `markup_intact()` + `translate_preserving_tags()` (L5 guarantee). |
| `core/postedit.py` | Deterministic **French post-edit** (`fix_french_elision`): repairs missing elisions ("te endors"→"t'endors", "ma assistante"→"mon assistante"); safe-by-construction, applied to genuinely-translated FR output only. |
| **`core/renpy_ast/`** | **AST-first Ren'Py core** (see below). |
| `core/model_registry.py` | `ModelSpec` for the universal (Q6) and Quality (12B) models (label, quant, size, vram, url). |
| `core/fonts.py` | Font-coverage detection + automatic patch font adaptation. |
| `core/languages.py` | Per-language quality metadata surfaced in the UI. |
| `core/model_manager.py` | `local_path`/`is_ready`/`resolve_path`/`download` (streamed, resumable, size-verified). |
| `core/rpa_extractor.py` | Pure-Python `.rpa` reader (RPA 2.0/3.0/3.2). |
| `core/decompiler.py` | unrpyc subprocess decompile — legacy fallback (no longer the primary path). |
| `core/{detector,encoding,syntax_check,quality_check,diff_engine,dryrun,benchmark,profiles}.py` | Detection, encoding, checks, reports, settings persistence. |
| `plugins/extractors/renpy.py` | `_resolve_game_dir` (folder/`.app`/deep) + `extract_rpyc` (AST dialogue + menus + `_()`/screen, with `try_harder` deobfuscation) + `extract_rpy` (P6 faithful `.rpy`-only parser). |
| `plugins/extractors/plaintext.py` | `.txt`/`.md` (lines) and `.json` (structural). |
| `plugins/generators/renpy.py` | Mode A (`translate <lang> <id>:`) / Mode B (fallback strings); **global** string de-dup → one consolidated `localtranslate_strings.rpy` + complete escaping (`escape_renpy_string`); `validate_patch()` (rejects duplicate `old`/id, unterminated strings); `finalize()` = shared **auto-discovering** language selector + INSTALL. |
| `plugins/generators/plaintext.py` | Mirror `.txt`/`.md`; rebuild `.json` preserving keys/types. |
| `vendor/unrpyc-master/` | **Vendored decompiler** — provides the fake `renpy` AST + `say_get_code` + decompiler. |

### `core/renpy_ast/` subsystem
| Module | Responsibility |
|---|---|
| `loader.py` | `load_ast(.., try_harder)` (`.rpyc` v1/v2 → AST via vendored `renpycompat`); on a failed parse, **deobfuscation fallback** (header/zlib scan + layered base64/hex/zlib/string-escape decoders). `decompile_to_text` (in-memory); `ast_classes()`. |
| `identifiers.py` | Reuses vendored `say_get_code`; `group_digest` + `IdentifierAllocator` → the **exact** dialogue id. |
| `walker.py` | `DialogueWalker` (dialogue ids, incl. **custom-statement / `UserStatement` dialogue** — P5a), `StringWalker` (menu choices), `collect_character_names` (glossary; tolerates `Character (…)` with a space + factory variants). |
| `strings.py` | `scan_marked_strings` — `_()`/`__()`/`_p()` markers + screen text from decompiled source. |
| `rpy_parser.py` | **P6** — faithful `.rpy`-only parser: reconstructs `say_get_code` from source text to compute exact ids when no `.rpyc` exists. |
| `structure.py` | **P4** — `analyze_game()` read-only audit → `StructureReport` (shape, `.rpa` archives, readable/unreadable/recovered `.rpyc`, dialogue/strings counts, character names, coverage %). |
| `mods.py` | **P5b** — `classify_mod()` flags player mods (cheat / walkthrough / console / gallery-unlock / urm) for optional exclusion. |

## Data flow (Ren'Py)

```
source → _collect_renpy_jobs (loose + .rpa, prefer .rpyc) → glossary pre-pass
  → per file: load_ast (try_harder deobfuscation on failure) → extract_rpyc
       → dialogue (exact ids, incl. UserStatement) + menu strings + _()/screen strings
  → per text: translate_preserving_tags (mask → LLM → validate → retry → fallback)
       → French post-edit (fix_french_elision) on genuinely-translated FR text
  → generator Mode A (translate <id>: + strings:) → finalize (lang button + INSTALL)
  → quality_report.md.        Source is NEVER written (read-only).
```

## Key architectural decisions

- **AST-first for Ren'Py.** The `.rpyc` is the AST the engine runs (it can desync
  from `.rpy` text), so dialogue identifiers are computed from it with Ren'Py's
  exact algorithm. This is what makes the patch actually bind. Regex-on-`.rpy` is
  a fallback only.
- **Strings channel is text-matched** (desync-immune): menus from the AST, `_()`/
  screen from in-memory decompile + regex (mirrors Ren'Py's own string scanner).
- **Structure perfect, phrasing best-effort.** Ren'Py markup is *guaranteed* intact
  (validate + retry + fallback-to-original); translation phrasing may vary.
- **Robust ingestion.** A failed `.rpyc` parse retries via deobfuscation strategies
  (`try_harder`); `.rpy`-only games get exact ids from a faithful text parser (P6);
  player mods are detected and optionally excluded (P5b); `analyze_game()` (P4)
  audits coverage read-only *before* any LLM time is spent.
- **Deterministic French cleanup.** Where formulation can be improved *safely*,
  `fix_french_elision` repairs mechanical elision slips on FR output — no model, no
  structure risk, only on genuinely-translated text.
- **HY-MT is a pure MT model.** Official prompt + sampling + fixed seed; no
  free-form style instructions (ignored). Register is nudged via the native
  terminology lever (`you -> tu`), not instructions.
- **Llama.cpp backend only.** Single self-contained GGUF, Metal on Apple Silicon.
- **Read-only source.** Decompilation/extraction go to `output_dir` temp only.
- **Mock-first testing.** The real GGUF is not exercised by the fast suite.
- **Model choice is the user's.** The universal model ships; the Quality model
  downloads on demand with clear UX.

No import cycles. `core.models` is the shared leaf; `core.renpy_ast.loader` bootstraps
the vendored decompiler.
