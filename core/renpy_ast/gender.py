"""
Declarative main-character gender (Quality tier).

An instruction-following model applies grammatical agreement when the system
prompt states the speaker's gender. For the supporting cast, the model can
usually infer it from the speaker's display name; but the protagonist's name is
DYNAMIC (the player renames them: `[mc]`, `[player_name]`), so nothing can be
inferred. The player therefore declares the main character's gender in the
UI/CLI, and this module determines WHICH character tags are the main character.

Rule: the main character is the one whose display name contains a variable that
does NOT resolve to another character's static name. A name variable that maps
to a known character is that character's inner voice (not the hero); a variable
that resolves to nothing is the hero. Suffixed aliases (e.g. "Name, Thinking")
map back to their base character for the prompt.
"""
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, Optional, Set

from core.renpy_ast.loader import _bootstrap
from core.renpy_ast.walker import _CHARACTER_NAME_RE

_RENPY_TAG_RE = re.compile(r"\{[^}]*\}")          # {color=#fff}, {i}, ...
_VAR_RE = re.compile(r"\[(\w+)\]")                # [mc], [player_name]

# --- cast-gender inference (pronoun scan over the ENGLISH source text) ------
# Roughly half of spoken lines get a resolved speaker gender; verdicts rely on a
# strong pronoun majority, and ambiguous characters honestly stay unknown.
# Non-English sources simply yield no votes (clean degradation).
_F_PRON = re.compile(r"\b(she|her|hers|herself)\b", re.IGNORECASE)
_M_PRON = re.compile(r"\b(he|him|his|himself)\b", re.IGNORECASE)
_F_TITLE = (r"(?:Mrs\.?|Ms\.?|Miss|Lady|Aunt(?:ie)?|Mom|Mommy|Mother|Grandma"
            r"|Sister|Queen|Princess)")
_M_TITLE = (r"(?:Mr\.?|Sir|Lord|Uncle|Dad(?:dy)?|Father|Grandpa|Brother|King"
            r"|Prince)")
_TEXT_MARKUP = re.compile(r"\{[^}]*\}|\[[^\]]*\]")    # strip from spoken text
_SENT_SPLIT = re.compile(r"[.!?…]+")
_MIN_VOTES = 4          # verdict thresholds, calibrated on the probe's data
_RATIO = 2              # winner needs >= 2x the loser's votes

# Accepted spellings for the declared gender (UI/CLI input). Canonical values
# are the ones build_instruct_system_prompt understands: "man" / "woman".
_GENDER_ALIASES = {
    "m": "man", "man": "man", "male": "man", "homme": "man", "h": "man",
    "f": "woman", "woman": "woman", "female": "woman", "femme": "woman",
}


def normalize_mc_gender(value: Optional[str]) -> Optional[str]:
    """User input -> "man" / "woman" / None (= unspecified, no directive)."""
    return _GENDER_ALIASES.get((value or "").strip().lower())


def strip_renpy_tags(name: Optional[str]) -> str:
    """Display name without {tags}: '{color=#ab}Rosa{/color}' -> 'Rosa'."""
    return _RENPY_TAG_RE.sub("", name or "").strip()


def collect_character_defs(stmts) -> Dict[str, str]:
    """
    Map character tag -> raw display name from `define x = Character("Name")`
    (same walk as collect_character_names, but keeps the Define's varname so
    dialogue `who` tags can be resolved to display names).
    """
    _bootstrap()
    import renpy
    a = renpy.ast
    defs: Dict[str, str] = {}

    def visit(block):
        for i in block:
            code = getattr(i, "code", None)
            src = getattr(code, "source", None) if code is not None else None
            varname = getattr(i, "varname", None)
            if varname and isinstance(src, str) and "Character" in src:
                m = _CHARACTER_NAME_RE.search(src)
                if m:
                    defs[str(varname)] = m.group(1)
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
    return defs


# default mi = "Miji"  /  define ja = _("Jasmina") — rename-variable defaults.
_NAME_VAR_RE = re.compile(r'^\s*_?\(?\s*["\']([^"\']+)["\']\s*\)?\s*$')


def collect_name_vars(stmts) -> Dict[str, str]:
    """
    Map variable name -> literal string default (`default mi = "Miji"`).
    Many AVNs let the player rename CAST members too: their tags' display
    names are variables ([mi]) whose default is the character's real name.
    Resolving them keeps those tags out of the MC set and lets their lines
    inherit the character's inferred gender.
    """
    _bootstrap()
    import renpy
    a = renpy.ast
    out: Dict[str, str] = {}

    def visit(block):
        for i in block:
            code = getattr(i, "code", None)
            src = getattr(code, "source", None) if code is not None else None
            varname = getattr(i, "varname", None)
            if varname and isinstance(src, str):
                m = _NAME_VAR_RE.match(src)
                if m:
                    out[str(varname)] = m.group(1)
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
    return out


def _static_names(defs: Dict[str, str]) -> Dict[str, str]:
    """lowercased static display name -> original ('miji' -> 'Miji')."""
    out: Dict[str, str] = {}
    for raw in defs.values():
        name = strip_renpy_tags(raw)
        if name and not _VAR_RE.search(name):
            out.setdefault(name.lower(), name)
    return out


def _resolve_var(var: str, defs: Dict[str, str], static: Dict[str, str],
                 name_vars: Optional[Dict[str, str]]) -> Optional[str]:
    """A display variable resolving to a KNOWN character's name, else None.

    Three shapes it must handle:
      [Name]        -> a static display name directly;
      [n]           -> a character TAG (`define n = Character("Nora")`) —
                       Ren'Py interpolates the Character object's name;
      [hero_name]   -> a rename variable whose default is the name
                       (`default hero_name = "Nora"`).
    """
    hit = static.get(var.lower())
    if hit:
        return hit
    raw = defs.get(var)
    if raw:
        name = strip_renpy_tags(raw)
        if name and not _VAR_RE.search(name):
            return static.get(name.lower())
    value = (name_vars or {}).get(var)
    if value:
        return static.get(value.lower())
    return None


def find_mc_tags(defs: Dict[str, str],
                 name_vars: Optional[Dict[str, str]] = None) -> Set[str]:
    """
    Tags whose display name is dynamic AND does not alias a known character.
    These are the protagonist's tags — the ones the declared gender applies
    to. (The hero's rename default — `default player_name = "Vlad"` — never
    matches a cast display name, so the hero always stays MC.)
    """
    static = _static_names(defs)
    mc: Set[str] = set()
    for tag, raw in defs.items():
        name = strip_renpy_tags(raw)
        variables = _VAR_RE.findall(name)
        if not variables:
            continue
        # "[Miji]" / "[mi]" = a known character's inner voice, not the hero.
        if any(_resolve_var(v, defs, static, name_vars) for v in variables):
            continue
        mc.add(tag)
    return mc


def _speaker_name(raw: str, defs: Dict[str, str], static: Dict[str, str],
                  name_vars: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Display name usable in the prompt ('The speaker is X'), else None."""
    name = strip_renpy_tags(raw)
    if not name:
        return None
    m = _VAR_RE.fullmatch(name)
    if m:  # pure alias "[Miji]" / "[mi]" -> the known character's name
        return _resolve_var(m.group(1), defs, static, name_vars)
    if _VAR_RE.search(name):  # any other dynamic name is unusable
        return None
    # "Eliza, Thinking" -> "Eliza" when Eliza is a known character.
    head = name.split(",")[0].strip()
    if head != name and head.lower() in static:
        name = static[head.lower()]
    if not re.search(r"[A-Za-z]", name):  # "???", "..." carry no information
        return None
    return name


def infer_cast_genders(defs: Dict[str, str], units: Iterable[Any]) -> Dict[str, str]:
    """
    Infer cast genders from the game text itself: normalized display name ->
    "woman"/"man" (ambiguous/unmentioned names are absent). A name votes when
    it co-occurs with gendered pronouns in the same sentence of ANY spoken
    line; a gendered title glued to the name ("Aunt Sarah", "Mr. Smith") is a
    strong signal (x3). `units` = DialogueUnit-likes (only `.what` is read).

    A bare first name in the prompt does not reliably yield agreement; the
    instruct model needs this explicit gender, after which it performs like the
    declared main-character path.
    """
    static = _static_names(defs)
    names = sorted(set(static.values()), key=len, reverse=True)
    if not names:
        return {}
    name_re = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")
    f_title = {n: re.compile(rf"\b{_F_TITLE}\s+{re.escape(n)}\b") for n in names}
    m_title = {n: re.compile(rf"\b{_M_TITLE}\s+{re.escape(n)}\b") for n in names}

    votes: Dict[str, Counter] = defaultdict(Counter)
    for u in units:
        what = getattr(u, "what", None)
        if not what:
            continue
        text = _TEXT_MARKUP.sub(" ", what)
        for sent in _SENT_SPLIT.split(text):
            found = set(name_re.findall(sent))
            if not found:
                continue
            nf = len(_F_PRON.findall(sent))
            nm = len(_M_PRON.findall(sent))
            for n in found:
                votes[n]["f"] += nf
                votes[n]["m"] += nm
                if f_title[n].search(sent):
                    votes[n]["f_title"] += 1
                if m_title[n].search(sent):
                    votes[n]["m_title"] += 1

    out: Dict[str, str] = {}
    for n, v in votes.items():
        f = v["f"] + 3 * v["f_title"]
        m = v["m"] + 3 * v["m_title"]
        if f + m >= _MIN_VOTES and f >= _RATIO * m:
            out[n] = "woman"
        elif f + m >= _MIN_VOTES and m >= _RATIO * f:
            out[n] = "man"
    return out


def build_speaker_contexts(
    defs: Dict[str, str], mc_gender: Optional[str] = None,
    cast_genders: Optional[Dict[str, str]] = None,
    name_vars: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Map character tag -> per-line context {"speaker", "gender"} for the
    instruct translator (see LlamaCppTranslator.translate_batch(contexts=...)).
    The main character gets the player-DECLARED gender; the cast gets the
    INFERRED one (cast_genders, keyed by normalized display name — aliases like
    "Name, Thinking" or rename-vars "[n]" inherit through their base name).
    Tags with nothing useful to say are omitted — their lines get no context.
    """
    gender = normalize_mc_gender(mc_gender)
    mc_tags = find_mc_tags(defs, name_vars)
    static = _static_names(defs)
    out: Dict[str, Dict[str, Any]] = {}
    for tag, raw in defs.items():
        s = _speaker_name(raw, defs, static, name_vars)
        if tag in mc_tags:
            g = gender
        else:
            g = (cast_genders or {}).get(s) if s else None
        if s or g:
            out[tag] = {"speaker": s, "gender": g}
    return out


# --- conservative ADDRESSEE-gender resolution (the "tu es belle/beau" lever) -----
# Returns the LISTENER's gender ("man"/"woman") ONLY when the line itself makes it
# unambiguous — never a blind "assume the main character" guess. Most second-person
# lines carry no signal, and guessing them would masculinise lines aimed at women.
# Three safe signals:
#   1. an [mc]/[player...] reference  -> the declared MC gender
#   2. a gendered vocative in TRAILING direct-address position (", girl?" / "sir!")
#      -> that gender. Trailing-only on purpose: it dodges the interjection trap
#      ("Oh man, ...") and predicate/possessive uses ("a good man", "my daddy").
#   3. a trailing name vocative (", Penelope?") -> the inferred cast gender for it.
_ADDR_MC_RE = re.compile(r"\[(?:mc|player|player_name|playername)\b[^\]]*\]", re.I)
# RELIABLY-gendered address terms ONLY. Neutral endearments (babe, honey, sweetie,
# sweetheart, darling, baby, gorgeous) and unisex slang (dude) are DELIBERATELY
# excluded: a woman may call a male hero "babe", so classing them feminine would
# break his agreement — the exact regression the zero-regression gate must avoid.
_FEM_VOC_WORDS = r"girl|girls|sis|sister|lady|ladies|miss|ma'?am|princess|queen"
_MASC_VOC_WORDS = r"boy|boys|bro|brother|man|sir|gentleman|mister|prince"
_TRAIL = r"[\s.!?'\"”’)]*$"
_NAME_VOC_RE = re.compile(r",\s*([A-Z][a-z]+)\b" + _TRAIL)


def _trailing_vocative(text: str, words: str) -> bool:
    """A gendered noun in the trailing direct-address slot: a comma, then the word
    (optionally 'my ...'), then only punctuation to the end of the line."""
    return bool(re.search(rf",\s*(?:my\s+)?(?:{words})\b" + _TRAIL, text, re.I))


def resolve_addressee_gender(
    text: str, mc_gender: Optional[str],
    cast_genders: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Listener gender from the line, or None when not unambiguous. Conservative
    by design (zero-regression gate): fires only on an [mc] reference, a trailing
    gendered vocative, or a trailing name vocative resolvable in the inferred cast
    genders. Everything else returns None (no directive)."""
    if not text:
        return None
    if _ADDR_MC_RE.search(text):
        return normalize_mc_gender(mc_gender)
    if _trailing_vocative(text, _FEM_VOC_WORDS):
        return "woman"
    if _trailing_vocative(text, _MASC_VOC_WORDS):
        return "man"
    if cast_genders:
        m = _NAME_VOC_RE.search(text)
        if m:
            g = cast_genders.get(m.group(1))
            if g in ("man", "woman"):
                return g
    return None
