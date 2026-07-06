"""
Load a Ren'Py `.rpyc` file into its AST statement list.

A `.rpyc` is either:
  - **v1**: a raw zlib-compressed pickle blob, or
  - **v2**: a `RENPY RPC2` slot archive where slot 1 holds that same blob.

We unpickle through the vendored unrpyc `renpycompat`, which registers a fake
`renpy` package so the AST classes (`renpy.ast.Say`, `Label`, …) resolve without
a real Ren'Py install. This is exactly how unrpyc reads compiled scripts.
"""
import sys
import zlib
import struct
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

_VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "unrpyc-master"

_renpycompat = None


def _bootstrap():
    """Put vendored unrpyc on the path and import renpycompat (sets up fake renpy)."""
    global _renpycompat
    if _renpycompat is None:
        if str(_VENDOR) not in sys.path:
            sys.path.insert(0, str(_VENDOR))
        from decompiler import renpycompat  # noqa: registers sys.modules["renpy"]
        _renpycompat = renpycompat
    return _renpycompat


def ast_classes() -> Dict[str, Any]:
    """Map of AST class name -> class (e.g. 'Say', 'Label', 'Menu', 'Init')."""
    rc = _bootstrap()
    return {c.__name__: c for c in rc.SPECIAL_CLASSES}


def _read_container(raw: bytes) -> bytes:
    """Return the compressed AST blob from v1 (raw) or v2 (RENPY RPC2) bytes."""
    if not raw.startswith(b"RENPY RPC2"):
        return raw  # v1: the blob is the whole file

    pos = 10
    chunks: Dict[int, bytes] = {}
    while True:
        slot, start, length = struct.unpack("<III", raw[pos:pos + 12])
        if slot == 0:
            break
        pos += 12
        chunks[slot] = raw[start:start + length]

    if 1 not in chunks:
        raise ValueError("RPYC header has no AST slot (1) — modified or obfuscated")
    return chunks[1]


class _DeobfContext:
    """Minimal context object for the vendored ``deobfuscate.read_ast``.

    That function only ever calls ``.log(message)`` to record its diagnosis; we
    keep the lines so a caller could surface them, but otherwise stay out of the
    way. ``set_state`` is a tolerated no-op in case the strategy set evolves.
    """

    def __init__(self):
        self.log_contents: List[str] = []

    def log(self, message):
        self.log_contents.append(message)

    def set_state(self, state):  # pragma: no cover - defensive, unused by read_ast
        pass


def _deobfuscate(raw: bytes) -> List[Any]:
    """Last-resort loader for obfuscated `.rpyc` via the vendored unrpyc strategies.

    Tries header/zlib scanning to slice out the AST slot even when the magic or
    slot table was tampered with, then peels layered base64/hex/zlib/string-escape
    encodings until a pickle is recovered. Only invoked after the standard path
    fails, so the happy path never pays for it. Raises ``ValueError`` if every
    strategy fails.
    """
    import io
    _bootstrap()
    import deobfuscate  # vendored; on sys.path via _bootstrap()
    return deobfuscate.read_ast(io.BytesIO(raw), _DeobfContext())


def _load_strict(raw: bytes, rc) -> List[Any]:
    """Standard v1/v2 load: extract slot 1, zlib-inflate, unpickle. Raises on failure."""
    blob = _read_container(raw)
    try:
        data = zlib.decompress(blob)
    except Exception as e:
        raise ValueError(f"RPYC blob is not zlib-compressed ({e}) — likely obfuscated") from None
    _, stmts = rc.pickle_safe_loads(data)
    return stmts


def load_ast(source, try_harder: bool = False) -> List[Any]:
    """Load `.rpyc` (path or bytes) into a list of AST statements. Raises on failure.

    With ``try_harder=True``, a failed standard parse falls back to the vendored
    deobfuscation strategies before giving up — this is what lets scripts whose
    container was obfuscated (changed magic, layered encoding) still load.
    """
    rc = _bootstrap()
    raw = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    try:
        return _load_strict(raw, rc)
    except Exception:
        if not try_harder:
            raise
    return _deobfuscate(raw)


def load_ast_safe(source, try_harder: bool = True) -> Tuple[Optional[List[Any]], Optional[str]]:
    """Non-raising variant. By default also attempts deobfuscation on failure.

    Returns one of:
      - ``(stmts, None)`` — loaded normally (happy path);
      - ``(stmts, "Recovered via deobfuscation: <name>")`` — loaded via fallback;
      - ``(None, "Could not load AST from <name>: <err>")`` — unrecoverable.
    """
    rc = _bootstrap()
    raw = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    name = "<bytes>" if isinstance(source, (bytes, bytearray)) else Path(source).name
    try:
        return _load_strict(raw, rc), None
    except Exception as e:
        if not try_harder:
            return None, f"Could not load AST from {name!r}: {e}"
    try:
        return _deobfuscate(raw), f"Recovered via deobfuscation: {name!r}"
    except Exception as e2:
        return None, f"Could not load AST from {name!r} (even with deobfuscation): {e2}"


def decompile_to_text(stmts) -> str:
    """
    Decompile an AST back to .rpy source text, in memory (no files, no subprocess).

    Used only to recover `strings:`-channel candidates (_() markers and screen
    text), which Ren'Py itself scans textually. Dialogue identifiers never depend
    on this — they come from the AST directly.
    """
    import io
    _bootstrap()
    from decompiler import pprint, Options
    buf = io.StringIO()
    pprint(buf, stmts, Options())
    return buf.getvalue()
