import hashlib
import re
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Set, Tuple, Optional
from core.models import TranslationUnit, UnitType

# Human-readable names used on the in-game language button.
LANG_NAMES = {
    "en": "English", "fr": "Français", "es": "Español", "it": "Italiano",
    "de": "Deutsch", "pt": "Português", "ru": "Русский", "ja": "日本語",
    "zh": "中文", "ko": "한국어",
}

# Universal, self-contained Ren'Py language selector. ONE shared file (not per
# language): it auto-discovers every installed translation via
# renpy.known_languages(), so each language you add appears automatically. A
# clearly visible "Language" button (overlay, top-right, menus + in-game) opens a
# centered popup listing Original + every language. It edits no original file.
# NOTE: this text is written verbatim (it contains Python braces) — never .format() it.
LANGUAGE_LAUNCHER = '''\
# LocalTranslate — universal in-game language selector (auto-generated, shared).
# Lives in game/tl/ and serves EVERY installed language at once. It auto-discovers
# translations via renpy.known_languages(), so adding more language patches later
# makes them appear here automatically — nothing to regenerate. Edits no original file.
#
# For the player: a "Language" button appears ONLY on the main menu and the
# Preferences screen (never over in-game UI like phone / inventory). It opens a
# popup listing "Original" + every installed language. Pick one. Done.

init -1 python:
    # Pretty display names; unknown codes fall back to a capitalized form.
    _lt_lang_names = {
        "fr": "Fran\\u00e7ais", "en": "English", "es": "Espa\\u00f1ol",
        "it": "Italiano", "de": "Deutsch", "pt": "Portugu\\u00eas",
        "ru": "\\u0420\\u0443\\u0441\\u0441\\u043a\\u0438\\u0439",
        "ja": "\\u65e5\\u672c\\u8a9e", "zh": "\\u4e2d\\u6587",
        "ko": "\\ud55c\\uad6d\\uc5b4", "tr": "T\\u00fcrk\\u00e7e",
        "pl": "Polski", "nl": "Nederlands", "cs": "\\u010ce\\u0161tina",
        "hu": "Magyar", "uk": "\\u0423\\u043a\\u0440\\u0430\\u0457\\u043d\\u0441\\u044c\\u043a\\u0430",
        "ar": "\\u0627\\u0644\\u0639\\u0631\\u0628\\u064a\\u0629",
        "ca": "Catal\\u00e0", "nb": "Norsk",
    }

    def _lt_label(lang):
        if not lang:
            return "Original"
        return _lt_lang_names.get(lang, lang.replace("_", " ").capitalize())

    def _lt_languages():
        try:
            langs = sorted(renpy.known_languages())
        except Exception:
            langs = []
        # A stray tl/None folder (tooling accident) must not appear as a language.
        return [None] + [l for l in langs if l and l != "None"]

    def _lt_on_menu():
        # Show the button ONLY in front-end menus (main menu / preferences /
        # the in-game menu container), never during gameplay — so it can't
        # cover phone/inventory/etc. Several names are checked for robustness
        # across games that rename their preferences screen.
        try:
            for _s in ("main_menu", "preferences", "game_menu"):
                if renpy.get_screen(_s):
                    return True
        except Exception:
            pass
        return False

screen lt_language_menu():
    modal True
    zorder 32767
    add Solid("#000000c8")
    frame:
        align (0.5, 0.5)
        padding (50, 38)
        background "#15171cf2"
        vbox:
            spacing 12
            xalign 0.5
            text "Language / Langue" xalign 0.5 size 30 color "#ffffff"
            null height 10
            for _lt_l in _lt_languages():
                textbutton _lt_label(_lt_l):
                    xalign 0.5
                    text_size 22
                    action [Hide("lt_language_menu"), Language(_lt_l)]
            null height 10
            textbutton "Close":
                xalign 0.5
                text_size 18
                action Hide("lt_language_menu")

screen lt_language_button():
    zorder 1000
    # Only on the main menu and the Preferences screen — never over in-game UI.
    if _lt_on_menu():
        frame:
            align (0.5, 0.972)
            padding (14, 8)
            background "#000000b0"
            textbutton "Language / Langue":
                text_size 16
                text_color "#ffffff"
                action Show("lt_language_menu")

init 999 python:
    # NOT config.overlay_screens: the engine SUPPRESSES overlays on the main
    # menu and inside the game menu (00gamemenu.rpy: suppress_overlay = True)
    # — exactly the two places this button must live. always_shown_screens is
    # displayed unconditionally every interaction (same mechanism as Ren'Py's
    # own console trace screen); _lt_on_menu() then gates it to those menus.
    if hasattr(config, "always_shown_screens"):
        if "lt_language_button" not in config.always_shown_screens:
            config.always_shown_screens.append("lt_language_button")
    elif "lt_language_button" not in config.overlay_screens:
        config.overlay_screens.append("lt_language_button")
'''

def escape_renpy_string(text: str) -> str:
    """Escape a string for safe embedding inside a Ren'Py `"..."` literal.

    Order matters: backslashes FIRST (so we don't double the ones we add next),
    then double quotes, then real newlines/tabs → their escape sequences. A raw
    newline inside `"..."` is a Ren'Py parse error ("not terminated with a
    newline"), so the model occasionally emitting a multi-line answer must never
    leak through verbatim.
    """
    if text is None:
        return ""
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "\\n").replace("\t", "\\t")
    return text


# Back-compat alias (kept for any external import).
escape_quotes = escape_renpy_string

def get_dialogue_id(unit: TranslationUnit) -> str:
    """Returns the Ren'Py dialogue identifier for a unit.

    If the unit was extracted from the compiled AST it carries the *exact*
    engine identifier in metadata['renpy_id'] — always prefer it. The hash below
    is only a best-effort fallback for the legacy regex path (.rpy-only games).
    """
    if unit.metadata:
        rid = unit.metadata.get("renpy_id")
        if rid:
            return rid

    label = "start"
    if unit.context and unit.context.startswith("label:"):
        label = unit.context.split(":", 1)[1]
    
    # Keep label alphanumeric to be safe
    label_clean = "".join(c for c in label if c.isalnum() or c == "_")
    if not label_clean:
        label_clean = "start"
        
    h = hashlib.md5(unit.original_text.encode("utf-8")).hexdigest()[:8]
    return f"{label_clean}_{h}"

class RenPyGenerator:
    """Generator for Ren'Py translation files."""
    
    def __init__(self, mode: str = "A", *args, **kwargs):
        self.mode = mode
        
    def generate(
        self,
        translated_units: List[TranslationUnit],
        source_dir: Path,
        output_dir: Path,
        target_lang: str,
        mode: str = "A",
        output_subdir: Optional[str] = None,
    ) -> None:
        """
        Generates Ren'Py translation files based on the translated units.
        In Mode A, mirrors the hierarchical directory structure inside game/tl/target_lang/
        In Mode B, generates a single fallback.rpy file.

        `output_subdir` (DELTA mode): nests every generated file under
        game/tl/<lang>/<subdir>/ so a delta patch never overwrites the existing
        patch's files. The language is still <lang> (Ren'Py keys language on the
        folder directly under tl/, not the sub-path), so `translate <lang>` is unchanged.
        """
        source_dir = Path(source_dir)
        output_dir = Path(output_dir)
        target_lang = target_lang.lower()
        mode = mode.upper()
        lang_root = output_dir / "game" / "tl" / target_lang
        if output_subdir:
            lang_root = lang_root / output_subdir
        
        if mode == "B":
            # Mode B: Fallback translation dictionary in fallback.rpy
            fallback_file = lang_root / "fallback.rpy"
            fallback_file.parent.mkdir(parents=True, exist_ok=True)
            
            unique_translations: Dict[str, str] = {}
            for u in translated_units:
                if u.translated_text and u.original_text.strip():
                    unique_translations[u.original_text] = u.translated_text
            
            lines = []
            lines.append(f"# RenPy AutoTranslate - Fallback Strings for {target_lang.capitalize()}\n")
            lines.append(f"translate {target_lang} strings:\n")
            
            for old, new in sorted(unique_translations.items()):
                escaped_old = escape_quotes(old)
                escaped_new = escape_quotes(new)
                lines.append(f"    old \"{escaped_old}\"\n")
                lines.append(f"    new \"{escaped_new}\"\n\n")
                
            with open(fallback_file, "w", encoding="utf-8") as f:
                f.writelines(lines)
                
        else:
            # Mode A: dialogue mirrors the source tree (one file per source file),
            # but STRING translations are GLOBAL in Ren'Py — the same `old "X"`
            # may appear only once across the whole tl/<lang>/, so we collect them
            # globally and emit ONE consolidated strings file. Dialogue ids are
            # likewise de-duplicated across files (defensive; labels are global).
            units_by_file = defaultdict(list)
            for u in translated_units:
                units_by_file[u.file_path].append(u)

            seen_ids: set = set()           # dialogue ids already emitted (any file)
            global_strings: Dict[str, str] = {}   # original -> translated (first wins)
            strings_order: List[str] = []

            for src_file, file_units in units_by_file.items():
                dialogue_units = [u for u in file_units if u.unit_type == UnitType.DIALOGUE]
                ui_units = [u for u in file_units if u.unit_type in (UnitType.UI_STRING, UnitType.MENU)]

                # Strings: accumulate globally; written once, below.
                for u in ui_units:
                    ot = u.original_text
                    if ot and ot.strip() and ot not in global_strings:
                        global_strings[ot] = u.translated_text if u.translated_text is not None else ot
                        strings_order.append(ot)

                if not dialogue_units:
                    continue  # nothing file-specific to write

                # Group dialogue by id; skip ids already emitted in another file.
                dialogue_groups = defaultdict(list)
                group_order = []
                for u in dialogue_units:
                    did = get_dialogue_id(u)
                    if did in seen_ids:
                        continue
                    if did not in dialogue_groups:
                        group_order.append(did)
                    dialogue_groups[did].append(u)
                if not group_order:
                    continue
                seen_ids.update(group_order)

                parts = list(src_file.parts)
                tail = parts[1:] if parts and parts[0] == "game" else parts
                out_path = lang_root.joinpath(*tail)
                out_path.parent.mkdir(parents=True, exist_ok=True)

                lines = []
                for diag_id in group_order:
                    lines.append(f"translate {target_lang} {diag_id}:\n")
                    for u in dialogue_groups[diag_id]:
                        escaped_orig = escape_renpy_string(u.original_text)
                        escaped_trans = escape_renpy_string(u.translated_text if u.translated_text is not None else u.original_text)
                        if u.character:
                            lines.append(f"    # {u.character} \"{escaped_orig}\"\n")
                            lines.append(f"    {u.character} \"{escaped_trans}\"\n")
                        else:
                            lines.append(f"    # \"{escaped_orig}\"\n")
                            lines.append(f"    \"{escaped_trans}\"\n")
                    lines.append("\n")

                with open(out_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)

            # One consolidated, globally-deduped strings file.
            if global_strings:
                strings_path = lang_root / "localtranslate_strings.rpy"
                strings_path.parent.mkdir(parents=True, exist_ok=True)
                slines = [f"# LocalTranslate — global string translations (deduplicated)\n",
                          f"translate {target_lang} strings:\n"]
                for ot in strings_order:
                    slines.append(f"    old \"{escape_renpy_string(ot)}\"\n")
                    slines.append(f"    new \"{escape_renpy_string(global_strings[ot])}\"\n\n")
                with open(strings_path, "w", encoding="utf-8") as f:
                    f.writelines(slines)

    def finalize(self, output_dir: Path, target_lang: str) -> None:
        """
        Called once after all files are generated. Produces the turnkey extras:
          - game/tl/localtranslate_language.rpy : the SHARED, auto-discovering
            language selector (one file serving every installed language)
          - INSTALL_INSTRUCTIONS.md : how to drop the patch into the target game
        """
        output_dir = Path(output_dir)
        lang = target_lang.lower()
        label = LANG_NAMES.get(lang, lang.capitalize())

        # Shared launcher at tl/ ROOT: it ships inside the copied tl/ folder, is
        # never duplicated per language (duplication would redefine its screens),
        # and lists every installed language dynamically (renpy.known_languages()).
        tl_dir = output_dir / "game" / "tl"
        tl_dir.mkdir(parents=True, exist_ok=True)
        (tl_dir / "localtranslate_language.rpy").write_text(LANGUAGE_LAUNCHER, encoding="utf-8")

        (output_dir / "INSTALL_INSTRUCTIONS.md").write_text(
            self._install_instructions(lang, label), encoding="utf-8"
        )

    @staticmethod
    def _install_instructions(lang: str, label: str) -> str:
        return f"""# LocalTranslate — Installing the {label} patch

A turnkey Ren'Py translation patch. **No code editing needed.**

## Install
1. Copy the **`tl/` folder** into the target game's `game/` folder
   (you'll end up with `…/YourGame/game/tl/{lang}/`).
2. Launch the game. A **"Language"** button appears in the **top-right corner**
   (both in menus and in-game).
3. Click it and pick **{label}** — or any other installed language. Done.

The selector (`tl/localtranslate_language.rpy`) is one shared file that
**auto-discovers** every language you install: add more patches later and they
appear in the list automatically. It edits no original game file.

## (Optional) Make {label} the default at first launch
Add to any script (e.g. `options.rpy`):

```renpy
define config.default_language = "{lang}"
```
"""


# ---------------------------------------------------------------------------
# Patch validation — a safety net that re-reads a generated tl/<lang>/ patch and
# reports the structural errors Ren'Py rejects at load time. Cheap, no Ren'Py
# needed. The engine runs it after generation so a bad patch fails loudly here
# instead of crashing the player's game.
# ---------------------------------------------------------------------------

_OLD_LINE_RE = re.compile(r'^\s*old\s+"((?:[^"\\]|\\.)*)"\s*$')
_NEW_LINE_RE = re.compile(r'^\s*new\s+"((?:[^"\\]|\\.)*)"\s*$')
_TRANSLATE_ID_RE = re.compile(r'^\s*translate\s+\w+\s+(\w+)\s*:\s*$')
_QUOTED_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _last_quoted(line: str):
    """The LAST double-quoted string on a line (the say-translation text, after an
    optional speaker token). Returns None if the line has no quoted string."""
    m = None
    for m in _QUOTED_RE.finditer(line):
        pass
    return m.group(1) if m else None


def _quotes_balanced(line: str) -> bool:
    """True if `"` are balanced once `\\\\` and `\\"` are accounted for."""
    stripped = line.replace("\\\\", "").replace('\\"', "")
    return stripped.count('"') % 2 == 0


def validate_patch(patch_root, target_lang: str = None) -> List[str]:
    """Scan a generated patch for the errors Ren'Py would reject:

      - a duplicate string `old "X"` anywhere in the whole tl/<lang>/
        (`A translation for "X" already exists`),
      - a duplicate dialogue id across files,
      - an unterminated / unescaped quoted string (a raw newline splits the line,
        leaving the quote unbalanced → `is not terminated with a newline`).

    Returns a list of human-readable problems; empty means structurally sound.
    """
    problems: List[str] = []
    seen_old: Dict[str, str] = {}
    seen_id: Dict[str, str] = {}

    for rpy in sorted(Path(patch_root).rglob("*.rpy")):
        name = rpy.name
        # macOS "._*"/.DS_Store sidecars (exFAT drives) are NOT our files — skip
        # them, don't flag them as "not valid UTF-8" structural problems.
        if name.startswith("._") or name == ".DS_Store":
            continue
        try:
            text = rpy.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{name}: not valid UTF-8")
            continue

        for i, line in enumerate(text.splitlines(), 1):
            mo = _OLD_LINE_RE.match(line)
            if mo:
                key = mo.group(1)
                if key in seen_old:
                    problems.append(
                        f'duplicate string `old "{key}"` at {name}:{i} (first at {seen_old[key]})')
                else:
                    seen_old[key] = f"{name}:{i}"
                continue

            mt = _TRANSLATE_ID_RE.match(line)
            if mt:
                tid = mt.group(1)
                if tid != "strings":
                    if tid in seen_id:
                        problems.append(
                            f"duplicate dialogue id `{tid}` at {name}:{i} (first at {seen_id[tid]})")
                    else:
                        seen_id[tid] = f"{name}:{i}"
                continue

            # Escaping / termination: any non-comment line carrying a quote must
            # have balanced quotes (a raw newline would have split it).
            stripped = line.lstrip()
            if stripped and not stripped.startswith("#") and '"' in line:
                if not _quotes_balanced(line):
                    problems.append(
                        f"unterminated/unescaped string at {name}:{i}: {stripped[:60]}")

    return problems


# The source comment Ren'Py writes under a `translate <lang> <id>:` line:
#   `# "*RING RING*"`  or with a speaker  `# e "Hello."`  (capture the quote).
_COMMENT_SRC_RE = re.compile(r'^\s*#\s*(?:[\w.]+\s+)?"((?:[^"\\]|\\.)*)"\s*$')

# ISO code <-> Ren'Py English folder name, for auto-detecting an existing patch
# whose folder name differs from the target ISO (e.g. tl/french vs --tgt-lang fr).
_LANG_ALIASES = {
    "fr": ["french"], "en": ["english"], "es": ["spanish"], "it": ["italian"],
    "de": ["german"], "pt": ["portuguese", "brazilian"], "ru": ["russian"],
    "ja": ["japanese"], "zh": ["chinese", "schinese", "tchinese"], "ko": ["korean"],
    "pl": ["polish"], "nl": ["dutch"], "tr": ["turkish"], "uk": ["ukrainian"],
    "cs": ["czech"], "ar": ["arabic"], "vi": ["vietnamese"], "th": ["thai"],
}


def scan_existing_translation(tl_lang_dir) -> Tuple[Dict[str, str], Set[str]]:
    """Collect what an EXISTING tl/<lang>/ patch already translates, so DELTA mode
    can complete it without re-translating or colliding.

    Returns (covered_ids, covered_olds):
      - covered_ids  : {dialogue identifier -> the SOURCE text it was translated
                       from} (the `# "source"` comment). Dialogue binds by id (the
                       robust, desync-immune key); keeping the source lets DELTA
                       re-translate a line whose id is reused but whose source
                       CHANGED between versions. Empty string = id covered, source
                       unknown (no comment) -> treated as covered.
      - covered_olds : source strings from `old "..."` (the `strings:` channel,
                       matched by exact source text), UNESCAPED to compare against
                       raw extracted text.
    Read-only; a missing/empty directory yields an empty map and set.
    """
    from core.renpy_ast.rpy_parser import _unescape  # deferred (import cycle)
    covered_ids: Dict[str, str] = {}
    covered_olds: Set[str] = set()
    root = Path(tl_lang_dir)
    if not root.is_dir():
        return covered_ids, covered_olds

    for rpy in sorted(root.rglob("*.rpy")):
        if rpy.name.startswith("._") or rpy.name == ".DS_Store":
            continue                                  # macOS sidecar junk (exFAT)
        try:
            text = rpy.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        pending = None   # an id awaiting its `# "source"` comment on a later line
        for line in text.splitlines():
            mt = _TRANSLATE_ID_RE.match(line)
            if mt:
                tid = mt.group(1)
                pending = tid if tid != "strings" else None
                if pending:
                    covered_ids.setdefault(pending, "")   # covered even w/o comment
                continue
            if pending:
                mc = _COMMENT_SRC_RE.match(line)
                if mc:
                    covered_ids[pending] = _unescape(mc.group(1))
                    pending = None
                    continue
                if line.strip():        # any other non-blank line closes the window
                    pending = None
            mo = _OLD_LINE_RE.match(line)
            if mo:
                covered_olds.add(_unescape(mo.group(1)))
    return covered_ids, covered_olds


def reconstruct_units_from_patch(tl_lang_dir) -> List[TranslationUnit]:
    """Rebuild (source, translation) units from an on-disk tl/<lang>/ patch.

    The post-translation quality audit normally runs on the units produced THIS
    run. A resume or Mock run skips every file (nothing to translate) → zero fresh
    units → `quality_report.md` was never written, leaving the wording quality of a
    finished patch invisible. This re-derives the units by reading the patch back:
    the `# "source"` comment is the original, the say / `new "..."` line is the
    translation. Read-only; mirrors scan_existing_translation but also captures the
    TRANSLATION. macOS sidecars / unreadable files are skipped (exFAT-safe).
    """
    from core.renpy_ast.rpy_parser import _unescape   # deferred (import cycle)
    units: List[TranslationUnit] = []
    root = Path(tl_lang_dir)
    if not root.is_dir():
        return units

    for rpy in sorted(root.rglob("*.rpy")):
        if rpy.name.startswith("._") or rpy.name == ".DS_Store":
            continue                                   # macOS sidecar junk (exFAT)
        try:
            lines = rpy.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        i, n = 0, len(lines)
        while i < n:
            mt = _TRANSLATE_ID_RE.match(lines[i])
            if mt and mt.group(1) != "strings":        # dialogue block
                tid = mt.group(1)
                src = trans = None
                j = i + 1
                if j < n:
                    mc = _COMMENT_SRC_RE.match(lines[j])
                    if mc:
                        src = _unescape(mc.group(1))
                        j += 1
                while j < n and not lines[j].strip():   # blank lines before the say
                    j += 1
                if j < n and not lines[j].lstrip().startswith("#"):
                    q = _last_quoted(lines[j])          # say line: optional speaker + "text"
                    if q is not None:
                        trans = _unescape(q)
                if src is not None or trans is not None:
                    units.append(TranslationUnit(
                        file_path=rpy, line_number=i + 1,
                        original_text=src if src is not None else (trans or ""),
                        translated_text=trans, unit_type=UnitType.DIALOGUE,
                        metadata={"renpy_id": tid}))
                i = j + 1
                continue
            mo = _OLD_LINE_RE.match(lines[i])           # strings block: old / new
            if mo:
                src = _unescape(mo.group(1))
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                mnew = _NEW_LINE_RE.match(lines[j]) if j < n else None
                units.append(TranslationUnit(
                    file_path=rpy, line_number=i + 1, original_text=src,
                    translated_text=_unescape(mnew.group(1)) if mnew else None,
                    unit_type=UnitType.UI_STRING))
                i = (j + 1) if mnew else (i + 1)
                continue
            i += 1
    return units


def detect_existing_patch(game_dir, target_lang: str):
    """Find an existing tl/<lang>/ patch to COMPLETE (delta), matching the target
    ISO code or a Ren'Py English alias (tl/french for --tgt-lang fr). Returns the
    folder Path (non-empty, contains .rpy) or None. Prefers an exact code match.
    Includes our OWN previous output -> re-running is a free incremental pass.
    """
    tl = Path(game_dir) / "tl"
    if not tl.is_dir():
        return None
    code = (target_lang or "").lower()
    candidates = [code] + _LANG_ALIASES.get(code, [])
    for iso, names in _LANG_ALIASES.items():     # reverse: --tgt-lang french -> fr
        if code in names:
            candidates.append(iso)
    existing = {p.name.lower(): p for p in tl.iterdir() if p.is_dir()}
    seen = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        p = existing.get(cand)
        if p and any(p.rglob("*.rpy")):
            return p
    return None
