"""
Faithful Ren'Py translation-identifier computation.

We deliberately reuse the vendored `say_get_code` (a Ren'Py-faithful port that
already handles who/attributes/with/arguments/explicit-id) as the single source
of truth, then apply the exact digest + uniqueness rules from Ren'Py's
`Restructurer`:

    digest = md5( say_get_code(node) + "\\r\\n" , for each node in group ).hexdigest()[:8]
    id     = label.replace(".", "_") + "_" + digest      # or just digest if label is None
    # within one file, collisions get _1, _2, … suffixes in source order
"""
import hashlib
from typing import List, Optional, Any

from core.renpy_ast.loader import _bootstrap


def _say_get_code(node, inmenu: bool = False) -> str:
    _bootstrap()
    from decompiler.util import say_get_code  # vendored, Ren'Py-faithful
    return say_get_code(node, inmenu)


def node_canonical_code(node, inmenu: bool = False) -> str:
    """Canonical code string for a single AST node (Say or UserStatement)."""
    _bootstrap()
    import renpy
    if isinstance(node, renpy.ast.Say):
        return _say_get_code(node, inmenu)
    # UserStatement and other translatable statements use their source line.
    line = getattr(node, "line", None)
    if line is not None:
        return line
    raise TypeError(f"Cannot derive canonical code for {type(node).__name__}")


def group_digest(nodes: List[Any]) -> str:
    """8-hex md5 digest over a group of consecutive translatable nodes."""
    md5 = hashlib.md5()
    for n in nodes:
        md5.update(node_canonical_code(n).encode("utf-8") + b"\r\n")
    return md5.hexdigest()[:8]


class IdentifierAllocator:
    """Assigns unique identifiers within a single file (mirrors unique_identifier)."""

    def __init__(self):
        self._seen = set()

    def allocate(self, label: Optional[str], digest: str) -> str:
        base = digest if label is None else label.replace(".", "_") + "_" + digest
        i, suffix = 0, ""
        while base + suffix in self._seen:
            i += 1
            suffix = f"_{i}"
        identifier = base + suffix
        self._seen.add(identifier)
        return identifier
