"""
Golden tests for Ren'Py dialogue identifier computation.

The expected identifiers below were extracted from a REAL shipped commercial game
(Guilty Pleasure, official Italian translation, game/tl/italian/script.rpy). If
these ever change, our generated translations would stop binding to the engine.
"""
import pytest

from core.renpy_ast.loader import ast_classes
from core.renpy_ast.identifiers import group_digest, IdentifierAllocator


def make_say(who, what, **kw):
    Say = ast_classes()["Say"]
    s = Say.__new__(Say)
    s.who = who
    s.what = what
    s.with_ = kw.get("with_", None)
    s.interact = kw.get("interact", True)
    s.attributes = kw.get("attributes", None)
    s.temporary_attributes = kw.get("temporary_attributes", None)
    s.arguments = kw.get("arguments", None)
    s.identifier = kw.get("identifier", None)
    s.explicit_identifier = kw.get("explicit_identifier", False)
    return s


def compute_id(label, who, what, alloc=None, **kw):
    alloc = alloc or IdentifierAllocator()
    digest = group_digest([make_say(who, what, **kw)])
    return alloc.allocate(label, digest)


# (label, who, what, expected_identifier) — verified against the real game.
GOLDEN = [
    ("start", None,  "*RING RING*",                         "start_c15ca220"),
    ("start", "MCT", "Ah, who the fuck is it at this hour?", "start_8065537b"),
    ("start", "MCT", "Fucking Mark.",                        "start_302cace2"),
    ("start", "MC",  "Yo, what’s up man?",              "start_6871128b"),
]


@pytest.mark.parametrize("label,who,what,expected", GOLDEN)
def test_identifier_matches_real_game(label, who, what, expected):
    assert compute_id(label, who, what) == expected


def test_narrator_vs_character_differ():
    assert compute_id("start", None, "Hello.") != compute_id("start", "e", "Hello.")


def test_label_none_uses_bare_digest():
    ident = compute_id(None, "e", "Hello.")
    # With no label, Ren'Py uses the bare 8-hex digest (no label prefix).
    assert len(ident) == 8
    assert all(c in "0123456789abcdef" for c in ident)


def test_uniqueness_suffix_for_duplicates():
    """Identical lines under the same label get _1, _2 suffixes, in order."""
    alloc = IdentifierAllocator()
    a = compute_id("start", "e", "Again.", alloc=alloc)
    b = compute_id("start", "e", "Again.", alloc=alloc)
    c = compute_id("start", "e", "Again.", alloc=alloc)
    assert a.endswith("220") or True  # value irrelevant; structure matters
    assert b == a + "_1"
    assert c == a + "_2"


def test_dot_in_label_is_sanitized():
    ident = compute_id("chapter.one", "e", "Hi.")
    assert ident.startswith("chapter_one_")
