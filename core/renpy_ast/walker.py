"""
Walk a Ren'Py AST and yield translatable dialogue units with correct identifiers.

This mirrors Ren'Py's `Restructurer` (as ported in the vendored
`unrpyc/decompiler/translate.py`): linear walk tracking the current label,
grouping consecutive translatable statements, and assigning each group the
identifier the engine will look for at runtime.
"""
import re
from dataclasses import dataclass
from typing import List, Optional, Any, Set

from core.renpy_ast.loader import _bootstrap
from core.renpy_ast.identifiers import group_digest, IdentifierAllocator

# define e = Character("Eileen")  /  Character(_("Protagonist"), ...)
# Allow a space before "(" — valid Python, and how some games write it
# (e.g. `Character ("Eliza", …)`). The trailing `Character` also
# matches factory variants (DynamicCharacter, ADVCharacter, NVLCharacter, …).
_CHARACTER_NAME_RE = re.compile(r'Character\s*\(\s*_?\(?\s*["\']([^"\']+)["\']')

# A say-like custom statement line:  token "text"  /  "text"  (nothing trailing).
_US_SAY_RE = re.compile(
    r'^(?:(?P<who>[A-Za-z_]\w*)\s+)?(?P<q>["\'])(?P<what>(?:\\.|(?!(?P=q)).)*?)(?P=q)\s*$'
)


def _userstatement_say(line):
    """If a translatable UserStatement is say-like (`token "text"` / `"text"`),
    return (who, what); otherwise (None, None). Reproduced verbatim as the same
    statement with translated text, so Ren'Py runs the custom statement again."""
    m = _US_SAY_RE.match((line or "").strip())
    if not m:
        return (None, None)
    from core.renpy_ast.rpy_parser import _unescape  # deferred (avoids import cycle)
    return (m.group("who"), _unescape(m.group("what")))


@dataclass
class DialogueUnit:
    identifier: str           # e.g. "start_c15ca220" — what Ren'Py looks up
    label: Optional[str]      # enclosing label (None outside any label)
    who: Optional[str]        # character expression, e.g. "e" (None = narrator)
    what: str                 # the dialogue text to translate
    linenumber: Optional[int] = None


@dataclass
class StringUnit:
    text: str                 # exact source string (matched at runtime, old/new)
    kind: str                 # "menu" | "ui"
    linenumber: Optional[int] = None


class DialogueWalker:
    def __init__(self):
        _bootstrap()
        import renpy
        self._ast = renpy.ast
        self.label: Optional[str] = None
        self.alternate: Optional[str] = None
        self.alloc = IdentifierAllocator()
        self.units: List[DialogueUnit] = []
        self.skipped_us = 0   # translatable UserStatements not handled (multi/non-say-like)

    # -- recursion into sub-blocks (mirrors Translator.walk) ------------------
    def _walk(self, node) -> None:
        a = self._ast
        if isinstance(node, (a.Init, a.Label, a.While, a.Translate, a.TranslateBlock)):
            self._translate_dialogue(node.block)
        elif isinstance(node, a.Menu):
            for item in node.items:
                if item[2] is not None:
                    self._translate_dialogue(item[2])
        elif isinstance(node, a.If):
            for entry in node.entries:
                self._translate_dialogue(entry[1])

    # -- main pass (mirrors Translator.translate_dialogue) -------------------
    def _translate_dialogue(self, children) -> None:
        a = self._ast
        group: List[Any] = []

        for i in children:
            if isinstance(i, a.Label) and not getattr(i, "hide", False):
                if i.name.startswith("_"):
                    self.alternate = i.name
                else:
                    self.label = i.name
                    self.alternate = None

            if not isinstance(i, a.Translate):
                self._walk(i)

            if isinstance(i, a.Say):
                group.append(i)
                self._emit(group)
                group = []
            elif getattr(i, "translatable", False):
                group.append(i)
            else:
                if group:
                    self._emit(group)
                    group = []

        if group:
            self._emit(group)

    def _emit(self, group: List[Any]) -> None:
        digest = group_digest(group)
        identifier = self.alloc.allocate(self.label, digest)
        # Ren'Py also reserves an alternate id under an "_"-prefixed label, which
        # consumes from the same uniqueness pool — replicate so suffixes match.
        if self.alternate is not None:
            self.alloc.allocate(self.alternate, digest)

        say = next((n for n in group if isinstance(n, self._ast.Say)), None)
        if say is not None:
            self.units.append(DialogueUnit(
                identifier=identifier,
                label=self.label,
                who=getattr(say, "who", None),
                what=say.what,
                linenumber=getattr(say, "linenumber", None),
            ))
            return

        # Custom-statement dialogue (P5a): a group of say-like translatable
        # UserStatements (e.g. `bardi_t "…"`) sharing ONE identifier. Each line
        # becomes a unit with the same id; the generator reproduces them as one
        # multi-line translate block. Only the id formula differs from Say
        # (group_digest already uses i.line for UserStatements).
        us = [n for n in group if isinstance(n, self._ast.UserStatement)]
        if not us:
            return
        parsed = [(_userstatement_say(getattr(n, "line", "")), getattr(n, "linenumber", None))
                  for n in us]
        # Emit only if EVERY statement is say-like (otherwise reproducing the block
        # could drop non-dialogue statements and break game flow — skip safely).
        if all(what is not None and what.strip() for (who, what), _ln in parsed):
            for (who, what), ln in parsed:
                self.units.append(DialogueUnit(
                    identifier=identifier, label=self.label, who=who, what=what, linenumber=ln,
                ))
        else:
            self.skipped_us += len(us)


def walk_dialogue(stmts) -> List[DialogueUnit]:
    """Return all dialogue units (with exact identifiers) for one file's AST."""
    walker = DialogueWalker()
    walker._translate_dialogue(stmts)
    return walker.units


class StringWalker:
    """Collects translatable strings handled by Ren'Py's `strings:` channel.

    These are matched by exact source text at runtime (no identifiers), exactly
    like Ren'Py's own "Generate Translations". Currently: menu choice captions.
    """

    def __init__(self):
        _bootstrap()
        import renpy
        self._ast = renpy.ast
        self.units: List[StringUnit] = []

    def walk(self, block) -> None:
        a = self._ast
        for i in block:
            if isinstance(i, a.Menu):
                line = getattr(i, "linenumber", None)
                for item in i.items:
                    label = item[0]
                    if isinstance(label, str) and label.strip():
                        self.units.append(StringUnit(label, "menu", line))
            # Recurse into every sub-block (mirrors the dialogue walker).
            if isinstance(i, (a.Init, a.Label, a.While, a.Translate, a.TranslateBlock)):
                self.walk(i.block)
            elif isinstance(i, a.Menu):
                for item in i.items:
                    if item[2] is not None:
                        self.walk(item[2])
            elif isinstance(i, a.If):
                for entry in i.entries:
                    self.walk(entry[1])


def walk_strings(stmts) -> List[StringUnit]:
    """Return all `strings:`-channel units (menu choices, …) for one file's AST."""
    walker = StringWalker()
    walker.walk(stmts)
    return walker.units


def collect_character_names(stmts) -> Set[str]:
    """
    Collect character display names from `define x = Character("Name")` (and
    `Character(_("Name"))`). Used to build a terminology glossary that keeps
    names consistent / untranslated.
    """
    _bootstrap()
    import renpy
    a = renpy.ast
    names: Set[str] = set()

    def code_source(node):
        code = getattr(node, "code", None)
        src = getattr(code, "source", None)
        return src if isinstance(src, str) else None

    def visit(block):
        for i in block:
            src = code_source(i)
            if src and "Character" in src:
                for m in _CHARACTER_NAME_RE.finditer(src):
                    names.add(m.group(1))
            if isinstance(i, (a.Init, a.Label, a.While, a.Translate, a.TranslateBlock)):
                visit(i.block)
            elif isinstance(i, a.Menu):
                for item in i.items:
                    if item[2] is not None:
                        visit(item[2])
            elif isinstance(i, a.If):
                for entry in i.entries:
                    visit(entry[1])

    visit(stmts)
    return names
