"""
P6 — faithful `.rpy` text → dialogue identifiers (for games shipping NO `.rpyc`).

When a game ships only `.rpy` (no compiled `.rpyc`), we cannot read the AST, so we
parse the text and reconstruct the SAME canonical `say_get_code` Ren'Py would
compute, then apply the exact identifier algorithm (label + md5 + per-file
uniqueness). Validated against the `.rpyc` AST on files that ship both.

Scope: the common say forms (`"narrator"`, `who "text"`, `who attr… "text"`,
optional `with`). Exotic forms (string-who, args, nointeract) are best-effort.
"""
import re
from typing import List, Optional

from core.renpy_ast.loader import _bootstrap
from core.renpy_ast.identifiers import IdentifierAllocator
from core.renpy_ast.walker import DialogueUnit

# Statements that are never a `say` (first token).
_KEYWORDS = {
    "scene", "show", "hide", "play", "stop", "queue", "voice", "window", "pause",
    "define", "default", "init", "label", "jump", "call", "return", "python",
    "if", "elif", "else", "while", "for", "pass", "image", "style", "screen",
    "menu", "translate", "strings", "import", "from", "with", "config", "nvl",
    "transform", "camera", "add", "text", "textbutton", "button", "vbox", "hbox",
    "frame", "imagebutton", "bar", "input", "key", "timer", "use", "screen",
}

_SAY_RE = re.compile(
    r'^(?:(?P<who>[A-Za-z_]\w*)\s+(?P<attrs>(?:[A-Za-z_]\w*\s+)*?))?'
    r'(?P<q>["\'])(?P<what>(?:\\.|(?!(?P=q)).)*?)(?P=q)'
    r'(?P<rest>(?:\s+with\s+\w+)?\s*(?:#.*)?)$'
)
# String-who form:  "" "text"  /  "Sylvie" "text"  (ast.who keeps its quotes).
_SAY_STRWHO_RE = re.compile(
    r'^(?P<whoq>(?P<wq>["\'])(?:\\.|(?!(?P=wq)).)*?(?P=wq))\s+'
    r'(?P<q>["\'])(?P<what>(?:\\.|(?!(?P=q)).)*?)(?P=q)'
    r'(?P<rest>(?:\s+with\s+\w+)?\s*(?:#.*)?)$'
)
_WITH_RE = re.compile(r'\bwith\s+(\w+)')

# Block openers whose CONTENTS are never dialogue (skip them entirely).
_SKIP_BLOCKS = {"screen", "python", "image", "transform", "style",
                "layeredimage", "atl", "init", "style", "testcase"}


def _encode_say_string(s: str) -> str:
    _bootstrap()
    from decompiler.util import encode_say_string
    return encode_say_string(s)


def _strip_comment(s: str) -> str:
    """Remove a trailing Ren'Py comment, ignoring '#' inside strings (e.g. "#fff")."""
    quote = None
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            return s[:i].rstrip()
        i += 1
    return s.rstrip()


def _unescape(raw: str) -> str:
    """Reverse Ren'Py say-string escaping (\\\\, \\", \\', \\n, escaped space)."""
    out, i = [], 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            out.append({"n": "\n", '"': '"', "'": "'", "\\": "\\", " ": " "}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def say_code_from_fields(who: Optional[str], attributes: List[str], what: str,
                         with_: Optional[str], interact: bool = True) -> str:
    rv = []
    if who:
        rv.append(who)
    rv.extend(attributes)
    rv.append(_encode_say_string(what))
    if not interact:
        rv.append("nointeract")
    if with_:
        rv.append("with")
        rv.append(with_)
    return " ".join(rv)


def parse_rpy_dialogue(text: str) -> List[DialogueUnit]:
    """Parse `.rpy` source into dialogue units with exact Ren'Py identifiers."""
    import hashlib

    lines = text.splitlines()
    label: Optional[str] = None
    alloc = IdentifierAllocator()
    block_stack = []           # (indent, kind) — kind in {label,menu,screen,python,generic}
    units: List[DialogueUnit] = []

    for idx, raw_line in enumerate(lines):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = _strip_comment(stripped)   # drop trailing comments (label X: # …)
        if not stripped:
            continue

        while block_stack and indent <= block_stack[-1][0]:
            block_stack.pop()

        in_skip = any(k == "skip" for _, k in block_stack)

        # Block openers (end with ':').
        if stripped.endswith(":"):
            first_tok = stripped.split()[0].rstrip(":")
            if stripped.startswith("label "):
                name = stripped[6:-1].split("(")[0].split()[0] if stripped[6:-1].strip() else ""
                if name and not name.startswith("_"):
                    label = name
                block_stack.append((indent, "label"))
            elif first_tok in _SKIP_BLOCKS:
                block_stack.append((indent, "skip"))
            elif first_tok == "menu":
                block_stack.append((indent, "menu"))
            else:
                block_stack.append((indent, "generic"))
            continue

        if in_skip:
            continue

        first = stripped.split()[0] if stripped.split() else ""
        if first in _KEYWORDS or first.startswith("$"):
            continue

        m = _SAY_RE.match(stripped)
        if m and m.group("who") not in _KEYWORDS:
            who = m.group("who")
            attrs = (m.group("attrs") or "").split()
            what = _unescape(m.group("what"))
            rest = m.group("rest") or ""
        else:
            m2 = _SAY_STRWHO_RE.match(stripped)
            if not m2:
                continue
            who = m2.group("whoq")          # keeps quotes — matches ast.who
            attrs = []
            what = _unescape(m2.group("what"))
            rest = m2.group("rest") or ""
        with_m = _WITH_RE.search(rest)
        with_ = with_m.group(1) if with_m else None

        code = say_code_from_fields(who, attrs, what, with_)
        digest = hashlib.md5(code.encode("utf-8") + b"\r\n").hexdigest()[:8]
        identifier = alloc.allocate(label, digest)
        units.append(DialogueUnit(identifier=identifier, label=label,
                                  who=who, what=what, linenumber=idx + 1))

    return units
