"""
Font adaptation for translated patches.

A translation is worthless if the game's font can't draw the target language:
- Latin+diacritics (fr/es/it/de/pt): English display fonts often lack é è à ç … .
- Cyrillic (ru): lacked entirely by Latin-only fonts.
- CJK (ja/zh/ko): NO English font has these — the whole translation would be tofu.

This module is the FONT LAYER: it is universal (works for any script) and fully
decoupled from the translation MODEL. Detection + coverage checks are pure and
testable without Ren'Py; the override it emits uses `config.font_replacement_map`
to redirect a game font that lacks the target glyphs to an accent-capable one.

Line breaking for CJK (no spaces between words) is deliberately NOT configured here:
it needs in-game tuning we can't verify offline, and at worst a glyph wraps — still
readable (a product decision). See the note in the generated file.
"""
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Representative glyphs that MUST be renderable for the language. Not exhaustive —
# enough to reliably catch a font that lacks the script. CJK samples = the most
# common characters + kana/hangul.
REQUIRED_GLYPHS: Dict[str, str] = {
    "fr": "àâäçéèêëîïôöùûüÿœ«»’“”",
    "es": "áéíóúñü¿¡",
    "it": "àèéìíòóù",
    "de": "äöüßÄÖÜ",
    "pt": "ãõáàâçéêíóôúü",
    "ru": "абвгдежзиклмнопрстуфхцчшыьэюяАБВГ",
    "ja": "あいうえおアイウエオ日本語私恋",
    "zh": "你我他的是不了在人有这中文爱",
    "ko": "안녕하세요가나다우리사랑",
}

# Script family → used to pick a sane fallback font when the game has none.
SCRIPT_OF: Dict[str, str] = {
    "fr": "latin", "es": "latin", "it": "latin", "de": "latin", "pt": "latin",
    "en": "latin", "ru": "cyrillic", "ja": "cjk", "zh": "cjk", "ko": "cjk",
}

# The gui font variables Ren'Py games use for on-screen text (the ones that matter
# for reading). Parsed from the game scripts to know WHICH fonts to check/replace.
_GUI_FONT_RE = re.compile(
    r'define\s+(gui\.[A-Za-z_]*font)\s*=\s*"([^"]+\.(?:ttf|otf|ttc))"', re.I)
_GUI_FONT_ALIAS_RE = re.compile(
    r'define\s+gui\.[A-Za-z_]*font\s*=\s*(gui\.[A-Za-z_]*font)\b', re.I)
# Inline fonts in screens/styles: text_font "x.ttf" / font "x.ttf".
_INLINE_FONT_RE = re.compile(r'\b(?:text_font|font)\s+"([^"]+\.(?:ttf|otf|ttc))"', re.I)
# Inline font TAGS embedded in dialogue text: {font=x.ttf}...{/font}. Games use these
# heavily for special narration (legends/memories) — and they bypass gui.*_font
# entirely, so a font used ONLY this way (missing some accents) is invisible unless
# we scan the tags too. This is a common "blank accents in special scenes" gap.
_FONT_TAG_RE = re.compile(r'\{font=([^}]+?\.(?:ttf|otf|ttc))\}', re.I)


def font_missing_glyphs(font_bytes: bytes, lang: str) -> Optional[List[str]]:
    """Return the required glyphs the font does NOT cover for `lang` (empty list =
    fully covers). None if coverage can't be determined (no fontTools / bad font) —
    caller treats None as "don't touch" (never break a working patch on a guess)."""
    required = REQUIRED_GLYPHS.get((lang or "").lower())
    if not required:
        return []                                  # unknown lang → nothing to require
    try:
        from fontTools.ttLib import TTFont, TTCollection
        from io import BytesIO
        buf = BytesIO(font_bytes)
        try:
            face = TTFont(buf, fontNumber=0, lazy=True)
        except Exception:
            buf.seek(0)
            face = TTCollection(buf, lazy=True).fonts[0]   # .ttc collection
        cmap = face.getBestCmap()
    except Exception:
        return None                                # fontTools missing / unreadable
    return [c for c in required if ord(c) not in cmap]


def font_covers(font_bytes: bytes, lang: str) -> bool:
    missing = font_missing_glyphs(font_bytes, lang)
    return missing == []                           # [] = covers; None/list = no


def collect_declared_fonts(scripts_text: str) -> List[str]:
    """Font filenames the game declares for on-screen text (gui.*_font + inline),
    resolving `gui.button_text_font = gui.interface_text_font` aliases. Order-preserved,
    de-duplicated. These are the fonts whose coverage we must guarantee."""
    var_to_file: Dict[str, str] = {}
    for m in _GUI_FONT_RE.finditer(scripts_text):
        var_to_file[m.group(1).lower()] = m.group(2)
    # resolve aliases (a gui font var pointing at another gui font var)
    for m in _GUI_FONT_ALIAS_RE.finditer(scripts_text):
        src_line = m.group(0)
        target_var = m.group(1).lower()
        alias_var = re.match(r'define\s+(gui\.[A-Za-z_]*font)', src_line, re.I)
        if alias_var and target_var in var_to_file:
            var_to_file[alias_var.group(1).lower()] = var_to_file[target_var]
    fonts: List[str] = []
    for f in (list(var_to_file.values())
              + _INLINE_FONT_RE.findall(scripts_text)
              + _FONT_TAG_RE.findall(scripts_text)):     # {font=x.ttf} inline tags
        base = f.split("/")[-1]
        if base not in fonts:
            fonts.append(base)
    return fonts


def choose_replacement(
    lang: str,
    game_fonts: Dict[str, bytes],
    bundled_dir: Optional[Path] = None,
) -> Optional[Tuple[str, bytes]]:
    """Pick a font that covers `lang`. Preference = CLEAN & READABLE over decorative
    (the product rule): try our curated bundled font first, then fall back to a
    covering font already in the game (needed for CJK when no Noto is bundled),
    avoiding bold/italic/decorative weights. Returns (filename, bytes) or None."""
    script = SCRIPT_OF.get((lang or "").lower(), "latin")
    prefs = {
        "latin": ["DejaVuSans.ttf"],
        "cyrillic": ["DejaVuSans.ttf"],
        "cjk": ["NotoSansCJK-Regular.ttc", "NotoSansCJKsc-Regular.otf",
                "NotoSansJP-Regular.otf", "NotoSansKR-Regular.otf",
                "NotoSansSC-Regular.otf", "NotoSansKR-Regular.ttf"],
    }.get(script, ["DejaVuSans.ttf"])
    # 1. our curated, clean, readable bundled font for the script
    if bundled_dir:
        for fname in prefs:
            p = Path(bundled_dir) / fname
            if p.is_file():
                data = p.read_bytes()
                if font_covers(data, lang):
                    return fname, data
    # 2. else a covering font already in the game — prefer REGULAR weight (a bold or
    #    decorative body font is ugly/hard to read), which is the whole point.
    def _rank(name: str):
        n = name.lower()
        heavy = any(k in n for k in ("bold", "italic", "oblique", "black",
                                     "light", "thin", "heavy", "-b.", "-bi."))
        return (heavy, len(name), name)
    for name in sorted(game_fonts, key=_rank):
        if font_covers(game_fonts[name], lang):
            return name, game_fonts[name]
    return None


def gather_game_fonts(game_dir: Path) -> Dict[str, bytes]:
    """Every font the game can draw with: loose files under game/, the sibling
    renpy/common/ (where DejaVuSans lives — full Latin+Cyrillic, always present),
    and fonts packed inside .rpa archives. {filename: bytes}."""
    game_dir = Path(game_dir)
    fonts: Dict[str, bytes] = {}
    roots = [game_dir]
    common = game_dir.parent / "renpy" / "common"
    if common.is_dir():
        roots.append(common)
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if (p.suffix.lower() in (".ttf", ".otf", ".ttc")
                    and not p.name.startswith("._")):
                try:
                    fonts.setdefault(p.name, p.read_bytes())
                except OSError:
                    pass
    for rpa in game_dir.rglob("*.rpa"):
        if rpa.name.startswith("._"):
            continue
        try:
            from core.rpa_extractor import RPAExtractor
            with RPAExtractor(rpa) as ar:
                for fn in ar.list_files():
                    if fn.lower().endswith((".ttf", ".otf", ".ttc")):
                        base = fn.split("/")[-1]
                        fonts.setdefault(base, ar.read_file(fn))
        except Exception:
            pass                                       # unreadable archive → skip (non-fatal)
    return fonts


def _scripts_text(*dirs) -> str:
    parts: List[str] = []
    for d in dirs:
        if d and Path(d).is_dir():
            for p in Path(d).rglob("*.rpy"):
                if p.name.startswith("._"):
                    continue
                try:
                    parts.append(p.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
    return "\n".join(parts)


def apply_font_fix(game_dir: Path, output_dir: Path, tgt_lang: str, *,
                   decompiled_dir: Optional[Path] = None,
                   bundled_dir: Optional[Path] = None,
                   log=None, warn=None) -> Optional[str]:
    """Detect game fonts that can't draw `tgt_lang` and ship a covering font +
    override in the patch. Returns the replacement filename, or None when nothing
    was needed / possible. Never raises on font issues (best-effort, patch stays
    valid). The whole point: a translated patch that actually RENDERS."""
    lang = (tgt_lang or "").lower()
    if lang not in REQUIRED_GLYPHS:
        return None                                    # no glyph profile → don't touch
    declared = collect_declared_fonts(
        _scripts_text(Path(game_dir), decompiled_dir))
    if not declared:
        return None                                    # unknown text fonts → don't guess
    game_fonts = gather_game_fonts(game_dir)
    # confirmed-insufficient only (font_covers None = undeterminable → leave alone)
    insufficient = [f for f in declared
                    if f in game_fonts and font_covers(game_fonts[f], lang) is False]
    if not insufficient:
        return None                                    # game already covers the language
    repl = choose_replacement(lang, game_fonts, bundled_dir=bundled_dir)
    if not repl:
        if warn:
            warn(f"Police : {', '.join(insufficient)} ne couvre(nt) pas '{lang}' et "
                 f"aucune police de secours n'a été trouvée — ajoute une police "
                 f"couvrant '{lang}' (ex. Noto Sans CJK) dans resources/fonts/.")
        return None
    repl_name, repl_bytes = repl
    tl_dir = Path(output_dir) / "game" / "tl" / lang
    (tl_dir / "fonts").mkdir(parents=True, exist_ok=True)
    (tl_dir / "fonts" / repl_name).write_bytes(repl_bytes)
    (tl_dir / "localtranslate_fonts.rpy").write_text(
        build_font_override(insufficient, repl_name, lang), encoding="utf-8")
    if log:
        log(f"Police : {', '.join(insufficient)} → {repl_name} (couvre '{lang}').")
    return repl_name


def build_font_override(insufficient_fonts: List[str], replacement_filename: str,
                        lang: str) -> str:
    """The `tl/<lang>/localtranslate_fonts.rpy` content: redirect every font that
    can't draw `lang` to the shipped replacement, via config.font_replacement_map
    (the config.font_replacement_map mechanism). Global on purpose — a language patch
    is played in its language; other languages only see a (readable) cosmetic change."""
    listed = ", ".join(insufficient_fonts)
    lines = [
        "# localtranslate_fonts.rpy — AUTO-GENERATED font fix.",
        f"# The game font(s) [{listed}] do not contain the glyphs needed for "
        f"'{lang}', so the",
        "# translated text would render as blanks/tofu. We redirect them to a font that",
        f"# covers the language: fonts/{replacement_filename} (shipped in this patch).",
        "#",
        "# CJK note: word-wrapping is not tuned here; at worst a glyph wraps — still",
        "# readable. To fine-tune, set the text style's `language` property in-game.",
        "",
        "init 1000 python:",
        f'    _lt_font = "fonts/{replacement_filename}"',
        "    for _lt_f in (" + ", ".join(f'"{f}"' for f in insufficient_fonts) + ",):",
        "        for _lt_b in (False, True):",
        "            for _lt_i in (False, True):",
        "                config.font_replacement_map[(_lt_f, _lt_b, _lt_i)] = "
        "(_lt_font, _lt_b, _lt_i)",
        "",
    ]
    return "\n".join(lines)
