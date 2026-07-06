import os
import re
from pathlib import Path
from typing import List, Tuple, Optional, Callable
from core.models import TranslationUnit, UnitType
from core.safeguards import TAG_RE, PLACEHOLDER_RE

# Non-dialogue Ren'Py scripting keywords
KEYWORDS = {
    "scene", "show", "hide", "play", "stop", "queue", "voice", "window", "pause",
    "define", "default", "init", "label", "jump", "call", "return", "python",
    "if", "elif", "else", "while", "for", "pass", "image", "style", "screen",
    "menu", "translate", "strings", "import", "as", "from", "with", "config"
}

# Ren'Py system strings keywords to exclude
SYSTEM_KEYWORDS = {
    "self-voicing", "self voicing", "text-to-speech", "text to speech",
    "clipboard voicing", "console", "debugger", "accessibility",
    "high contrast", "monospace font", "large font", "renderer",
    "performance", "toggle console", "hide console", "input:",
    "updater", "checking for updates", "up-to-date", "new version",
    "downloading", "unpacking", "installing", "extraction failed",
    "console history", "debugger active"
}

# Only character names explicitly wrapped in _() are translatable, e.g.
#   define p = Character(_("Protagonist"))
# A bare `define e = Character("Eileen")` is a proper noun and must be left as-is
# (Ren'Py vigilance rule: do not auto-translate unmarked character names).
CHARACTER_DEF_RE = re.compile(
    r'^\s*define\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*Character\(\s*_\(\s*(["\'])((?:\\.|[^\\])*?)\2'
)
_RE = re.compile(r'_\((["\'])((?:\\.|[^\\])*?)\1\)|_p\((["\'])((?:\\.|[^\\])*?)\3\)')
MENU_ITEM_RE = re.compile(r'^\s*(["\'])((?:\\.|[^\\])*?)\1(?:\s+if\s+.*)?:$')
SCREEN_TEXT_RE = re.compile(r'^\s*(text|textbutton|button|label)\s+(["\'])((?:\\.|[^\\])*?)\2')
DIALOGUE_RE = re.compile(
    r'^\s*(?:([a-zA-Z_][a-zA-Z0-9_]*)\s+)?'  # Character ID
    r'(["\'])((?:\\.|[^\\])*?)\2'            # Quoted text
    r'(?:\s+with\s+[a-zA-Z_][a-zA-Z0-9_]*)?' # Optional transition
    r'(?:\s*|\s+#.*)$'                       # Optional comment
)

def clean_escaped_quotes(text: str) -> str:
    """Helper to restore normal quotes from escaped ones."""
    return text.replace(r'\"', '"').replace(r"\'", "'")

def is_system_string(text: str) -> bool:
    """Checks if the string belongs to Ren'Py system/engine interfaces."""
    t = text.lower().strip()
    for kw in SYSTEM_KEYWORDS:
        if kw in t:
            return True
    return False

def is_non_textual(text: str) -> bool:
    """Checks if the string is non-textual (variables only, color codes, too short)."""
    stripped = text.strip()
    if len(stripped) <= 1:
        if not stripped.isalpha():
            return True
            
    # Check for hex colors (e.g. #fff, #ffffff)
    if re.match(r'^#[0-9a-fA-F]{3,8}$', stripped):
        return True
        
    # Clean tags and placeholders
    cleaned = PLACEHOLDER_RE.sub("", text)
    cleaned = TAG_RE.sub("", cleaned).strip()
    
    # If no alphabetic characters are present, it does not need translation
    if not any(c.isalpha() for c in cleaned):
        return True
        
    return False

class RenPyExtractor:
    """Extractor for Ren'Py (.rpy) script files."""

    def _resolve_game_dir(self, path: Path) -> Path:
        """
        Find the real 'game/' directory inside a RenPy project.
        Handles: direct folder, .app bundles, nested structures.
        """
        if path.is_file():
            for p in path.parents:
                if p.name == "game":
                    return p
            return path.parent
        
        if not path.is_dir():
            return path
        
        # Case 1: direct game/ subfolder
        direct = path / "game"
        if direct.is_dir() and (any(direct.rglob("*.rpy")) or any(direct.rglob("*.rpyc")) or any(direct.rglob("*.rpa"))):
            return direct
        
        # Case 2: look inside any .app bundle
        for app in path.rglob("*.app"):
            if app.is_dir():
                candidates = [
                    app / "Contents" / "Resources" / "autorun" / "game",
                    app / "Contents" / "Resources" / "game",
                    app / "game",
                ]
                for cand in candidates:
                    if cand.is_dir() and (any(cand.rglob("*.rpy")) or any(cand.rglob("*.rpyc")) or any(cand.rglob("*.rpa"))):
                        return cand
        
        # Case 3: deep search any game/ folder (excluding renpy/common)
        best: Optional[Path] = None
        best_count = 0
        for candidate in path.rglob("game"):
            if candidate.is_dir() and "renpy/common" not in str(candidate):
                count = (
                    len(list(candidate.rglob("*.rpy"))) + 
                    len(list(candidate.rglob("*.rpyc"))) + 
                    len(list(candidate.rglob("*.rpa")))
                )
                if count > best_count:
                    best = candidate
                    best_count = count
        
        return best if best else path

    def _get_game_relative_path(self, file_path: Path, game_dir: Path) -> Path:
        try:
            return file_path.relative_to(game_dir)
        except ValueError:
            parts = file_path.parts
            if "game" in parts:
                idx = parts.index("game")
                return Path(*parts[idx+1:])
            return Path(file_path.name)

    def extract_rpyc(self, source, rel_path: Path) -> List[TranslationUnit]:
        """
        Extract dialogue units directly from a compiled `.rpyc` (path or bytes),
        carrying the EXACT engine identifier in metadata['renpy_id'].

        This is the authoritative path: the .rpyc is the AST the engine runs and
        can desync from the .rpy text, so identifiers must come from it.
        """
        from core.renpy_ast import load_ast, walk_dialogue, walk_strings

        stmts = load_ast(source, try_harder=True)  # deobfuscation fallback, else raises
        rel_path = Path(rel_path)
        units: List[TranslationUnit] = []

        # Dialogue channel: say statements, with exact engine identifiers.
        for du in walk_dialogue(stmts):
            text = du.what
            if not text or not text.strip():
                continue
            cleaned = clean_escaped_quotes(text)
            if is_system_string(cleaned) or is_non_textual(cleaned):
                continue
            units.append(TranslationUnit(
                file_path=rel_path,
                line_number=du.linenumber or 0,
                original_text=cleaned,
                unit_type=UnitType.DIALOGUE,
                character=du.who,
                context=f"label:{du.label}" if du.label else None,
                metadata={"renpy_id": du.identifier},
            ))

        # Strings channel: matched by exact text (old/new), so it is immune to
        # .rpy/.rpyc desync. Two complementary sources:
        #   - menu choices, straight from the AST (exact);
        #   - _() markers and screen text, scanned from the decompiled source
        #     (mirrors Ren'Py's own textual string scanner).
        seen_strings = set()

        def add_string(raw_text: str, unit_type: UnitType, context: str, line: int = 0) -> None:
            cleaned = clean_escaped_quotes(raw_text)
            if not cleaned.strip() or cleaned in seen_strings:
                return
            if is_system_string(cleaned) or is_non_textual(cleaned):
                return
            seen_strings.add(cleaned)
            units.append(TranslationUnit(
                file_path=rel_path,
                line_number=line,
                original_text=cleaned,
                unit_type=unit_type,
                context=context,
                metadata={"renpy_string": True},
            ))

        for su in walk_strings(stmts):
            add_string(su.text, UnitType.MENU, "menu", su.linenumber or 0)

        try:
            from core.renpy_ast.loader import decompile_to_text
            from core.renpy_ast.strings import scan_marked_strings
            source_text = decompile_to_text(stmts)
            for su in scan_marked_strings(source_text):
                add_string(su.text, UnitType.UI_STRING, "ui")
        except Exception:
            # Strings are a best-effort enrichment; never fail extraction over them.
            pass

        return units

    def extract_rpy(self, source_path, rel_path=None) -> List[TranslationUnit]:
        """
        Extract from a `.rpy` text file (no `.rpyc` available). Dialogue gets the
        EXACT Ren'Py identifier via the faithful text parser (validated 100% vs the
        AST); menu/`_()`/screen strings reuse the regex path (text-matched channel).
        """
        from core.renpy_ast.rpy_parser import parse_rpy_dialogue

        source_path = Path(source_path)
        if rel_path is None:
            rel_path = self._get_game_relative_path(source_path, self._resolve_game_dir(source_path))
        rel_path = Path(rel_path)
        units: List[TranslationUnit] = []

        text = ""
        try:
            text = source_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return units

        # Dialogue — exact identifiers from the text parser.
        for du in parse_rpy_dialogue(text):
            cleaned = clean_escaped_quotes(du.what)
            if not cleaned.strip() or is_system_string(cleaned) or is_non_textual(cleaned):
                continue
            units.append(TranslationUnit(
                file_path=rel_path, line_number=du.linenumber or 0,
                original_text=cleaned, unit_type=UnitType.DIALOGUE, character=du.who,
                context=f"label:{du.label}" if du.label else None,
                metadata={"renpy_id": du.identifier},
            ))

        # Strings (menus / _() / screen) — reuse the regex extractor, keep non-dialogue.
        for u in self.extract(source_path):
            if u.unit_type != UnitType.DIALOGUE:
                u.file_path = rel_path
                units.append(u)
        return units

    def can_handle(self, path: Path) -> bool:
        if path.is_file() and path.suffix in (".rpy", ".rpa"):
            return True
        if path.is_dir():
            game_dir = self._resolve_game_dir(path)
            return (
                any(game_dir.rglob("*.rpy")) or 
                any(game_dir.rglob("*.rpyc")) or 
                any(game_dir.rglob("*.rpa"))
            )
        return False
    
    def extract(self, source_path: Path, progress_callback: Optional[Callable] = None) -> List[TranslationUnit]:
        """
        Recursively parses all .rpy files in the source folder (ignoring 'tl/' and 'renpy/common/').
        Extracts dialogues, UI strings, menu items, screen texts, and character names.
        """
        path = Path(source_path)
        game_dir = self._resolve_game_dir(path)
        if game_dir != path and progress_callback:
            progress_callback(0.01, f"Found game directory: {game_dir.name}")

        # NOTE: decompilation of .rpyc is NOT done here. The extractor is a pure
        # parser of .rpy files. Invariant #1 (read-only source) forbids writing
        # next to the source; the engine decompiles into output_dir/temp first
        # (see core/decompiler.decompile_if_needed) and then feeds us .rpy files.

        units: List[TranslationUnit] = []
        
        # Scan for all .rpy files recursively
        rpy_files: List[Path] = []
        if path.is_file() and path.suffix == ".rpy":
            rpy_files.append(path)
        else:
            for root, dirs, files in os.walk(game_dir):
                # Ignore sub-folder 'tl'
                if "tl" in dirs:
                    dirs.remove("tl")
                
                # Exclude renpy/common directory
                parts = Path(root).parts
                if any(p == "renpy" for p in parts) and any(p == "common" for p in parts):
                    continue
                if "renpy/common" in Path(root).as_posix():
                    continue
                    
                for file in files:
                    if file.endswith(".rpy"):
                        fp = Path(root) / file
                        if "renpy/common" in fp.as_posix():
                            continue
                        if "/tl/" in fp.as_posix() or "\\tl\\" in fp.as_posix():
                            continue
                        rpy_files.append(fp)
                    
        for file_path in sorted(rpy_files):
            try:
                # Read UTF-8 robustly
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except Exception:
                continue
                
            current_label = "global"
            block_stack: List[Tuple[int, str]] = [] # list of (indent, block_type)
            
            # For mapping relative path
            rel_fp = self._get_game_relative_path(file_path, game_dir)
            
            for line_idx, raw_line in enumerate(lines):
                line_num = line_idx + 1
                line = raw_line.rstrip()
                stripped = line.strip()
                
                if not stripped:
                    continue
                    
                indent = len(line) - len(line.lstrip())
                
                # Check if we exited any blocks based on indentation
                while block_stack and indent <= block_stack[-1][0]:
                    block_stack.pop()
                    
                current_block = block_stack[-1][1] if block_stack else None
                
                # Skip comments
                if stripped.startswith("#"):
                    continue
                    
                # Check if starting a new block
                if stripped.endswith(":"):
                    if stripped.startswith("label "):
                        label_parts = stripped[6:-1].split()
                        if label_parts:
                            current_label = label_parts[0]
                        block_stack.append((indent, "label"))
                        continue
                    elif stripped.startswith("menu"):
                        block_stack.append((indent, "menu"))
                        continue
                    elif stripped.startswith("screen"):
                        block_stack.append((indent, "screen"))
                        continue
                    elif stripped.startswith("python") or stripped.startswith("init python"):
                        block_stack.append((indent, "python"))
                        continue
                    else:
                        block_stack.append((indent, "generic"))
                
                # Helper to process and validate extracted content
                def add_unit_if_valid(content: str, unit_type: UnitType, character: Optional[str] = None, context: Optional[str] = None) -> bool:
                    cleaned = clean_escaped_quotes(content)
                    if is_system_string(cleaned):
                        return False
                    if is_non_textual(cleaned):
                        return False
                    
                    # Avoid duplicates on the same line with same content and type
                    if any(u.line_number == line_num and u.original_text == cleaned and u.unit_type == unit_type for u in units):
                        return False
                        
                    unit = TranslationUnit(
                        file_path=rel_fp,
                        line_number=line_num,
                        original_text=cleaned,
                        unit_type=unit_type,
                        character=character,
                        context=context
                    )
                    units.append(unit)
                    return True

                # Check character definitions first
                char_def_match = CHARACTER_DEF_RE.match(stripped)
                if char_def_match:
                    content = char_def_match.group(3)
                    add_unit_if_valid(content, UnitType.UI_STRING, context="character_name")
                    continue

                # Look for _("...") or _p("...") anywhere on the line
                found_global_string = False
                for match in _RE.finditer(stripped):
                    content = match.group(2) or match.group(4)
                    if content:
                        add_unit_if_valid(content, UnitType.UI_STRING, context=f"label:{current_label}")
                        found_global_string = True

                is_in_python = any(b[1] == "python" for b in block_stack)
                is_in_screen = any(b[1] == "screen" for b in block_stack)
                
                if is_in_python:
                    continue
                    
                # Screen items
                if is_in_screen:
                    screen_match = SCREEN_TEXT_RE.match(stripped)
                    if screen_match:
                        content = screen_match.group(3)
                        add_unit_if_valid(content, UnitType.UI_STRING, context=f"screen:{current_label}")
                    continue

                # Menu items
                is_menu_item = False
                if current_block == "menu":
                    menu_match = MENU_ITEM_RE.match(stripped)
                    if menu_match:
                        content = menu_match.group(2)
                        if add_unit_if_valid(content, UnitType.MENU, context=f"label:{current_label}"):
                            is_menu_item = True

                # Dialogues
                if not is_menu_item and not found_global_string:
                    dialogue_match = DIALOGUE_RE.match(stripped)
                    if dialogue_match:
                        char_id = dialogue_match.group(1)
                        content = dialogue_match.group(3)
                        
                        if char_id in KEYWORDS:
                            continue
                            
                        add_unit_if_valid(content, UnitType.DIALOGUE, character=char_id, context=f"label:{current_label}")
                        
        return units
