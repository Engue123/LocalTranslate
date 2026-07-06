"""
Deterministic French post-editing — fix the mechanical elision/euphony slips the
MT model makes (e.g. "te endors" -> "t'endors", "ma assistante" -> "mon
assistante", "que il" -> "qu'il").

SAFE BY CONSTRUCTION: every rule fires ONLY on the error pattern — a word that
must contract/adjust, then a SPACE, then a vowel-initial word. Correct French
already has the contracted form (no space), so these rules can only *fix*, never
break a good output. We deliberately:
  - skip h- and y-initial next-words (aspirated h is unpredictable: "le héros"
    takes no elision; brackets `[`/`{` aren't vowels either, so tags/placeholders
    are untouched);
  - skip a short list of vowel-initial words that don't elide ("onze", "oui", …).

Applied to French output only (gated by target language in the engine).
"""
import re

APO = "’"  # ’ — matches the curly apostrophe HY-MT already emits

# Vowel-initial trigger. NOTE: h and y are intentionally absent (aspirated h is
# unpredictable; "y"/"yaourt" don't elide). `[` and `{` aren't here either, so
# Ren'Py placeholders/tags right after the space never trigger a (wrong) elision.
_V = "aàâäeéèêëiîïoôöuùûüAÀÂÄEÉÈÊËIÎÏOÔÖUÙÛÜ"

# Vowel-initial words that do NOT elide (cardinals / idiomatic). Compared lowercased.
# "i" guards the English pronoun "I" (no standalone "i" word exists in French), in
# case a stray untranslated word slips through — the engine also only post-edits
# genuinely translated lines (never an L5 fallback-to-original English line).
_NO_ELISION = {"onze", "onzième", "oui", "ululer", "ululement", "uhlan", "yo", "i"}

# word -> contracted stem (takes an apostrophe)
_ELIDE = {"le": "l", "la": "l", "je": "j", "me": "m", "te": "t",
          "se": "s", "ne": "n", "de": "d", "que": "qu"}
# possessive -> pre-vowel form (no apostrophe; the space stays)
_POSS = {"ma": "mon", "ta": "ton", "sa": "son"}

# `(?<!-)` skips hyphen-attached (enclitic) forms: in inversions like
# "Puis-je en avoir", "donne-le à Marie", "ai-je une chance", the word is
# attached to the PRECEDING verb and must NOT elide with what follows.
_NEXT = rf"([{_V}][\w{APO}'-]*)"
_ELIDE_RE = re.compile(rf"(?<!-)\b({'|'.join(_ELIDE)})\s+{_NEXT}", re.IGNORECASE)
_POSS_RE = re.compile(rf"(?<!-)\b({'|'.join(_POSS)})\s+{_NEXT}", re.IGNORECASE)
_SI_RE = re.compile(r"(?<!-)\b(si)\s+(ils?)\b", re.IGNORECASE)


def _cap(stem: str, like: str) -> str:
    """Match the leading capitalization of the original word."""
    return stem[0].upper() + stem[1:] if like[:1].isupper() else stem


def fix_french_elision(text: str) -> str:
    """Repair missing French elisions/euphony in `text`. Idempotent and safe."""
    if not text or " " not in text:
        return text

    def elide(m):
        w, nxt = m.group(1), m.group(2)
        if nxt.lower() in _NO_ELISION:
            return m.group(0)
        return _cap(_ELIDE[w.lower()], w) + APO + nxt

    def poss(m):
        w, nxt = m.group(1), m.group(2)
        if nxt.lower() in _NO_ELISION:
            return m.group(0)
        return _cap(_POSS[w.lower()], w) + " " + nxt

    def si(m):
        return _cap("s", m.group(1)) + APO + m.group(2)

    out = _ELIDE_RE.sub(elide, text)
    out = _POSS_RE.sub(poss, out)
    out = _SI_RE.sub(si, out)
    return out
