"""
Tests for the obfuscated-`.rpyc` fallback (``try_harder`` → vendored deobfuscate).

We have no naturally-obfuscated game in the corpora, so we synthesize realistic
obfuscated containers entirely in memory — no game files needed. Each variant
mirrors one real-world technique the vendored ``deobfuscate`` module defends
against (changed magic, layered base64/hex/zlib/string-escape encoding). The
inner payload is a plain ``(meta, stmts)`` tuple that round-trips through the
restricted unpickler, so the fixtures stay self-contained and mock-friendly.
"""
import base64
import pickle
import struct
import zlib

import pytest

from core.renpy_ast.loader import load_ast, load_ast_safe


# Plain, pickle-safe stand-ins for AST nodes (the unpickler allows builtins).
SAMPLE_STMTS = ["say hello", "say world", 7]


def _pickle_blob(stmts=SAMPLE_STMTS) -> bytes:
    """zlib(pickle((meta, stmts))) — the compressed blob a real slot 1 holds."""
    return zlib.compress(pickle.dumps(("meta", stmts), protocol=2))


def _rpyc(slot1: bytes, magic: bytes = b"RENPY RPC2") -> bytes:
    """Wrap a slot-1 payload in a two-slot ``RENPY RPC2`` container.

    Two slots + a trailing pad reproduce the real layout closely enough that
    every deobfuscation strategy (incl. headerscan, which keys off the 2nd slot)
    can engage, and no slot ends exactly at EOF (which would trip the extractor's
    ``start + length >= len(data)`` guard).
    """
    slot2 = b"\x00"
    header_len = 10 + 12 * 3  # magic + (slot1, slot2, terminator)
    start1 = header_len
    start2 = start1 + len(slot1)
    header = (
        magic
        + struct.pack("<III", 1, start1, len(slot1))
        + struct.pack("<III", 2, start2, len(slot2))
        + struct.pack("<III", 0, 0, 0)
    )
    return header + slot1 + slot2 + b"\x00"


def _clean() -> bytes:
    return _rpyc(_pickle_blob())


def _variants() -> dict:
    """The five obfuscation families, each as raw `.rpyc` bytes."""
    blob = _pickle_blob()
    clean = _rpyc(blob)
    return {
        "magic_scrambled": b"XXXXX RPC2" + clean[10:],          # tampered header magic
        "base64_layer": _rpyc(base64.b64encode(blob)),          # slot1 = base64(zlib)
        "double_zlib": _rpyc(zlib.compress(blob)),              # slot1 = zlib(zlib(pickle))
        "string_escape": _rpyc(blob.decode("latin1").encode("unicode-escape")),
        "base64_then_zlib": _rpyc(base64.b64encode(zlib.compress(blob))),  # two layers
    }


VARIANT_NAMES = list(_variants().keys())


# --- baseline ---------------------------------------------------------------

def test_clean_rpyc_loads_strict():
    assert load_ast(_clean()) == SAMPLE_STMTS


def test_clean_rpyc_safe_has_no_recovery_note():
    stmts, note = load_ast_safe(_clean())
    assert stmts == SAMPLE_STMTS
    assert note is None


# --- the strict path must REFUSE obfuscated input (no silent guessing) -------

@pytest.mark.parametrize("name", VARIANT_NAMES)
def test_strict_load_fails_on_obfuscated(name):
    data = _variants()[name]
    with pytest.raises(Exception):
        load_ast(data)  # try_harder defaults to False


# --- try_harder recovers every family, byte-for-byte identical AST ----------

@pytest.mark.parametrize("name", VARIANT_NAMES)
def test_try_harder_recovers_obfuscated(name):
    data = _variants()[name]
    assert load_ast(data, try_harder=True) == SAMPLE_STMTS


# --- load_ast_safe defaults to try_harder and reports the recovery ----------

@pytest.mark.parametrize("name", VARIANT_NAMES)
def test_safe_recovers_with_note(name):
    data = _variants()[name]
    stmts, note = load_ast_safe(data)  # try_harder defaults to True
    assert stmts == SAMPLE_STMTS
    assert note and "deobfuscation" in note.lower()


def test_safe_without_try_harder_does_not_recover():
    data = _variants()["base64_layer"]
    stmts, note = load_ast_safe(data, try_harder=False)
    assert stmts is None
    assert note and "Could not load" in note


def test_safe_truly_broken_returns_warning():
    stmts, note = load_ast_safe(b"this is not an rpyc at all")
    assert stmts is None
    assert note and "Could not load" in note


def test_try_harder_truly_broken_still_raises():
    with pytest.raises(Exception):
        load_ast(b"this is not an rpyc at all", try_harder=True)
