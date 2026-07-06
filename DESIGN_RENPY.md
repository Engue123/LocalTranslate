# Design — Translating the majority of Ren'Py games (the real challenge)

> Status: **proposal / concept** (no code yet — awaiting GO).
> Goal: turn the demo into a tool that produces translation files Ren'Py
> *actually loads and applies*, across real-world games we never opened in the SDK.

## 0. The founding principle

> "Complete protection is impossible — for a game to run, it has to read all of
> the resources involved, so all the information is present on the system."
> — Lemma Soft Forums

Whatever the Ren'Py engine can read at runtime, we can read offline. The art is
in **detecting each game's structure** and **applying the exact mechanism Ren'Py
expects** — not approximating it.

## 1. How Ren'Py translation really works (researched, not guessed)

Ren'Py has **two independent translation channels**:

### (A) Dialogue channel — `say` statements
```renpy
translate french start_636ae3f5:
    e "Texte traduit."
```
The identifier `start_636ae3f5` is **computed at compile time** and is the make-or-break detail. The exact algorithm (from Ren'Py's `Restructurer`, mirrored in the vendored `unrpyc/decompiler/translate.py` + `util.py:say_get_code`):

```
canonical = say_get_code(node)          # e.g.  e "Texte original."
digest    = md5( canonical.encode() + b"\r\n" ).hexdigest()[:8]
id        = label.replace(".", "_") + "_" + digest    # or just digest if no label
# collisions within a file get suffixes _1, _2, … in source order
```
`say_get_code` = space-join of: `who` + say-attributes + `@`temp-attributes +
`encode_say_string(what)` + `nointeract?` + `id?` + `arguments?` + `with?`.

**This is why the current tool is broken**: `get_dialogue_id()` does
`label + "_" + md5(original_text)[:8]` — it hashes the *bare text*, not the
canonical `say_get_code`, and ignores who/attributes/with/uniqueness. The engine
therefore **never matches our dialogue blocks**. Dialogue — the whole point of a
VN — is effectively untranslated today.

### (B) String channel — UI / menus / `_()`
```renpy
translate french strings:
    old "Original"
    new "Traduit"
```
Matched by **exact source text** (no identifiers). Robust. Ren'Py extracts these
from `_()`, `__()`, `_p()`, and menu choices. The `{#context}` tag disambiguates
identical strings (`"New{#project}"` vs `"New{#game}"`).

### Language selection (so the patch is actually reachable)
Resolution order: `RENPY_LANGUAGE` env → `config.language` → stored
`_preferences.language` → autodetect → `config.default_language` → None.
Runtime switch: `Language("french")` action / `renpy.change_language("french")`.
Our generated switcher + the optional `config.default_language` snippet are correct.

## 2. The barriers, and our answer to each

| # | Barrier (real games) | Solution |
|---|---|---|
| B1 | **Wrong dialogue identifiers** (current md5-of-text) | Rebuild the extractor **AST-first**: compute `id` with the *exact* `say_get_code`+md5 algorithm, per-file uniqueness, label tracking. Reuse vendored `say_get_code`/`unique_identifier`. |
| B2 | **Game ships only `.rpyc`** (no source) | `.rpyc` *is* the pickled AST. Load it via vendored `read_ast_from_file` (handles RPYC **v1** zlib-blob and **v2** `RENPY RPC2` slot archive). No decompilation-to-text needed for extraction. |
| B3 | **Assets/scripts inside `.rpa`** | Already have a pure-Python RPA reader (2.0/3.0/3.2). Extend: detect renamed archives by **magic bytes**, enumerate `.rpyc` inside, feed to B2. |
| B4 | **Obfuscated `.rpyc`** (mangled header/slots, custom pickle) | unrpyc’s `get_ast(try_harder=True)` → `deobfuscate.read_ast`. Wire a fallback: normal load → on failure, try_harder. |
| B5 | **Ren'Py version drift** (6/7/8, py2 pickles) | Loader already flags v1/py2; AST classes are version-tolerant `FakeStrict`. Detect version from header and record it in the structure report. |
| B6 | **`.app`/`.exe` packaging, nested layouts** | Keep `_resolve_game_dir` (folder/.app/deep search); already solid + E2E-tested. |
| B7 | **Duplicate lines / same text under same label** | Handled *for free* by the AST walk + `unique_identifier` suffixes — impossible with per-text md5. |
| B8 | **Character names, `[vars]`, `{tags}`** | Don’t translate unmarked `Character("X")`; keep tag/placeholder masking; flag losses (already done). |
| B9 | **Strings split across screens / `_()` everywhere** | Strings channel by exact text; honor `{#context}`, `_p()` whitespace rules. |
| B10 | **Can’t decompile at all** (exotic protection) | Secondary patch mode: runtime source→target **dict substitution** installed via an `init python` hook (no identifiers). Lower fidelity, last resort. |

## 3. Proposed architecture (evolution, not rewrite)

```
core/renpy/
  structure.py   # PRE-FLIGHT analyzer: shape, version, archives, obfuscation,
                 #   chosen strategy + coverage report  (the "ruse")
  rpyc_loader.py # thin wrapper over vendored unrpyc: bytes/.rpyc -> AST stmts,
                 #   with try_harder fallback; version detection
  ast_walk.py    # walk stmts; track label; group; compute EXACT identifiers
                 #   (reuse say_get_code + unique_identifier); yield dialogue
                 #   + string units; descend into Menu / screens(SL2) where possible
plugins/extractors/renpy.py   # becomes a thin driver: structure -> loader -> walk
plugins/generators/renpy.py   # already emits translate <id>: / strings: ; switch
                              #   to identifiers coming from the AST (verbatim)
```

- The **extractor** stops regex-guessing and instead consumes the AST. A regex
  path stays only as a *fallback* for `.rpy`-only games, reconstructing
  `say_get_code` for the common `who "text"` shape.
- The **generator** is already 90% right; it just needs to consume the
  AST-provided identifier instead of `get_dialogue_id()`.
- Everything still writes to `output_dir/game/tl/<lang>/…` (read-only source).

## 4. How we’ll *prove* identifiers are correct (not hope)

1. **Equivalence test**: build a small AST, compute the id our way and via the
   vendored `Translator.create_translate`; assert byte-equal. Same algorithm ⇒
   equal by construction.
2. **Golden fixture**: a known snippet whose expected id (`start_<hash>`) is
   precomputed; lock it in a test.
3. **Round-trip**: extract → generate `tl/fr` → re-load with unrpyc’s
   `saving_translations` Translator and confirm our blocks bind to the same nodes.
4. Coverage metric in the structure report: `% dialogue with computed id`,
   `% strings`, `# unresolved` — surfaced in the UI/log.

## 5. Phased plan (each phase = green tests + commit)

- **P1 — `loader`** ✅ DONE: load v1/v2 → AST. (`core/renpy_ast/loader.py`)
- **P2 — `walker` + exact identifiers** ✅ DONE: dialogue ids matching the algorithm;
  golden tests locked to a real game; engine pivoted to prefer `.rpyc`.
  *Core fix (B1/B2/B7).* Validated on 4 games (182 `.rpyc`, 0 load failures).
- **P3 — strings channel** ✅ DONE:
  - P3a: menu choices straight from the AST.
  - P3b: `_()`/`__()`/`_p()` markers + screen text, via in-memory decompile +
    textual scan (mirrors Ren'Py's own scanner; desync-immune).
- **P4 — `structure` analyzer** ⏳ TODO: detect shape/version/archives/obfuscation,
  pick strategy, emit a coverage report; wire into engine + GUI log.
- **P5 — archive & obfuscation hardening** ⏳ TODO (B3/B4): magic-byte archive
  detect, `try_harder`/deobfuscate path, graceful per-file degradation.
- **P6 — `.rpy`-only fallback parser** ⏳ TODO + optional **runtime-substitution** (B10).

## 6. Out of scope / explicit non-goals
- Editing original game files in place (what some tools do) — violates read-only.
- Translating baked-in **image text** — detect & warn only.
- Defeating DRM beyond what the running engine itself must decode.

## Sources
- Ren'Py docs: Translation, Translating Ren'Py, Dialogue.
- Vendored `unrpyc/decompiler/translate.py`, `util.py`, `renpycompat.py` (Ren'Py-faithful).
- Lemma Soft Forums (protection limits), argentgames blog (ID verification), teo-lin/renpy-translator (prior art, in-place rewrite approach we avoid).
