"""
Scan decompiled Ren'Py source text for `strings:`-channel candidates.

These are matched by exact source text at runtime (no identifiers), exactly like
Ren'Py's own textual string scanner ("Generate Translations"):
  - `_( )`, `__( )`, `___( )`, `_p( )` translation markers, anywhere;
  - literal text on screen displayables: text / textbutton / button / label / tooltip.
"""
import re
from typing import List

from core.renpy_ast.walker import StringUnit

# _("..."), _p("..."), __("..."), ___("...") — but not part of an identifier
# like gettext_("..."). Either quote style; handles escaped quotes inside.
_MARKER_RE = re.compile(
    r"""(?<![A-Za-z0-9])_{1,3}p?\(\s*(["'])((?:\\.|(?!\1).)*?)\1\s*\)"""
)

# A screen displayable carrying a literal string as its first argument.
_SCREEN_RE = re.compile(
    r"""^\s*(?:text|textbutton|button|label|tooltip)\s+(["'])((?:\\.|(?!\1).)*?)\1""",
    re.MULTILINE,
)


def scan_marked_strings(text: str) -> List[StringUnit]:
    """Return de-duplicated `strings:`-channel candidates found in `text`."""
    out: List[StringUnit] = []
    seen = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            out.append(StringUnit(value, "ui"))

    for m in _MARKER_RE.finditer(text):
        add(m.group(2))
    for m in _SCREEN_RE.finditer(text):
        add(m.group(2))
    return out
