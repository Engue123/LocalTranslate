"""
P5b — detect player-added mods (cheat / walkthrough / console / gallery unlock).

Players routinely drop extra `.rpy/.rpyc` into a game's `game/` folder: cheat
menus, walkthrough overlays, gallery unlockers, console enablers. They are real
scripts (so our pipeline already translates their text), but it's useful to
DETECT and REPORT them, and to let the user include or exclude them.

Heuristic, by file path. High-precision signals only — we'd rather miss an
oddly-named mod than flag a legitimate game file.
"""
import re
from typing import Optional

_RULES = [
    ("walkthrough", re.compile(r'walk.?through', re.I)),
    ("cheat",       re.compile(r'cheat', re.I)),
    ("console",     re.compile(r'console|~dirty', re.I)),
    ("gallery unlock", re.compile(r'unlock.{0,6}galler|galler.{0,6}unlock', re.I)),
    ("mod",         re.compile(r'(^|/)mods?/|(^|[^a-z])mod\.rpym?c?$|\b(urm|unren|multimod)\b', re.I)),
]


def classify_mod(rel_path: str) -> Optional[str]:
    """Return a mod category for `rel_path`, or None if it looks like a base file."""
    p = (rel_path or "").replace("\\", "/")
    for category, rx in _RULES:
        if rx.search(p):
            return category
    return None


def is_mod(rel_path: str) -> bool:
    return classify_mod(rel_path) is not None
