"""L5: Ren'Py markup safety — validate + fallback guarantee (no real model)."""
import re

from core.safeguards import markup_intact, translate_preserving_tags


class PreservingMock:
    """Keeps the __TAG_n__ tokens (well-behaved translator)."""
    def translate_batch(self, texts, seed=None, **kw):
        return ["[FR] " + t for t in texts]


class DroppingMock:
    """Drops every __TAG_n__ token (worst-case translator)."""
    def translate_batch(self, texts, seed=None, **kw):
        return [re.sub(r"__TAG_\d+__", "", t).strip() for t in texts]


class NoSeedMock:
    """A translator whose signature does NOT accept seed (must still work)."""
    def translate_batch(self, texts, source_lang=None, target_lang=None,
                        progress_callback=None, style_hint=None):
        return ["[FR] " + t for t in texts]


def test_markup_intact_basic():
    assert markup_intact("Hi {b}x{/b} [v]", "Salut {b}y{/b} [v]")
    assert not markup_intact("Hi {b}x{/b}", "Salut x")        # tag dropped
    assert not markup_intact("[a] and [b]", "[a] et rien")    # [b] missing


def test_preserve_ok_with_good_translator():
    out, ok = translate_preserving_tags(PreservingMock(), "You have [points] points!")
    assert ok is True
    assert "[points]" in out


def test_preserve_falls_back_to_original_when_markup_lost():
    text = "I didn't {i}hit{/i} anyone."
    out, ok = translate_preserving_tags(DroppingMock(), text)
    assert ok is False
    assert out == text                      # original kept — never broken Ren'Py
    assert markup_intact(text, out)         # ...and therefore markup is intact


def test_fallback_translator_rescues_dropped_markup():
    """When the primary drops the masked markup on every retry, a markup-preserving
    fallback (q6 in prod) is tried before we keep the English original (Bug 4)."""
    text = "{i}Hello there.{/i}"
    # a markup-preserving fallback -> its output is used, markup intact
    out, ok = translate_preserving_tags(DroppingMock(), text, fallback=PreservingMock())
    assert ok is True and "{i}" in out and "{/i}" in out
    # a fallback that ALSO drops markup -> still the safe original, never broken
    out2, ok2 = translate_preserving_tags(DroppingMock(), text, fallback=DroppingMock())
    assert ok2 is False and out2 == text


def test_preserve_no_tags_just_translates():
    out, ok = translate_preserving_tags(PreservingMock(), "Hello there.")
    assert ok is True
    assert out.startswith("[FR] ")


def test_preserve_handles_translator_without_seed_param():
    out, ok = translate_preserving_tags(NoSeedMock(), "Keep [x] please.")
    assert ok is True
    assert "[x]" in out


class CountingMock(PreservingMock):
    def __init__(self):
        self.calls = 0

    def translate_batch(self, texts, seed=None, **kw):
        self.calls += 1
        return super().translate_batch(texts, seed=seed, **kw)


def test_markup_only_lines_skip_the_model():
    """'[Vlad]....' or '{i}…{/i}?' have nothing translatable: the line IS its
    own translation. Measured on EuroLLM: calling the model on those made it
    improvise ('[Miji]?' -> 'Salut.') — so we don't call it at all."""
    tr = CountingMock()
    for line in ("[Vlad]....", "[Miji]?", "{i}…{/i}", "...", "[a] [b]!!"):
        out, ok = translate_preserving_tags(tr, line)
        assert (out, ok) == (line, True)
    assert tr.calls == 0

    # Real letters (any unicode script) DO reach the model.
    out, ok = translate_preserving_tags(tr, "[Vlad] Привет !")
    assert tr.calls == 1 and ok


def test_unmask_recovers_thinned_underscores():
    """EuroLLM sometimes thins __TAG_0__ to _TAG_0_ — still recoverable."""
    from core.safeguards import TagProtector
    masked, meta = TagProtector.mask("[Vlad] gets up.")
    assert masked.startswith("__TAG_0__")
    restored, warnings = TagProtector.unmask("_TAG_0_ se lève.", meta)
    assert restored == "[Vlad] se lève."
    assert not warnings
