import re
import uuid
from typing import Tuple, List, Dict
from concurrent.futures import ThreadPoolExecutor

# Regex to match Ren'Py tags like {b}, {/b}, {color=#fff}, {w=1.0}, etc.
TAG_RE = re.compile(r'\{[^\}]+\}')

# Regex to match Ren'Py interpolation placeholders like [player], [points], [player.name]
# Avoid matching doubled brackets like [[ which represent a literal [
PLACEHOLDER_RE = re.compile(r'(?<!\[)\[[^\[\]]+\]')

def markup_items(text: str) -> List[str]:
    """All Ren'Py markup occurrences in `text`: {tags} and [interpolations]."""
    return TAG_RE.findall(text) + PLACEHOLDER_RE.findall(text)


def markup_intact(original: str, translated: str) -> bool:
    """True iff every {tag}/[placeholder] of `original` survives, in full count."""
    for item in set(markup_items(original)):
        if translated.count(item) < original.count(item):
            return False
    return True


def _translate_once(translator, masked: str, seed: int, kwargs: dict) -> str:
    """Translate a single masked string, passing `seed` when the backend supports it."""
    try:
        return translator.translate_batch([masked], seed=seed, **kwargs)[0]
    except TypeError:
        return translator.translate_batch([masked], **kwargs)[0]


def translate_preserving_tags(translator, text: str, base_seed: int = 42,
                              retries: int = 2, fallback=None, **kwargs):
    """
    Translate `text` while GUARANTEEING Ren'Py markup safety.

    Strategy: mask {tags}/[vars] -> translate -> unmask -> validate. If any markup
    was lost, retry with a fresh seed (best effort). If it STILL cannot be made
    safe and a `fallback` translator is given, try that one: on markup-heavy lines
    an instruction model can drop masked tokens where a pure-MT model copies them
    faithfully, so the fallback rescues many of them. Only if that also fails do we
    return the ORIGINAL text (valid Ren'Py) rather than emit broken markup.

    Returns (result, ok). ok is False when we had to fall back to the original.
    """
    masked, meta = TagProtector.mask(text)

    # Nothing translatable: a line that is only markup + punctuation
    # ("[Vlad]....", "{i}…{/i}?") is its own translation. Skipping the model
    # call is faster AND safer — such lines otherwise make some models improvise
    # instead of copying the token.
    residue = re.sub(r"__TAG_\d+__", "", masked)
    if not re.search(r"[^\W\d_]", residue):   # no unicode letter left
        return text, True

    if not meta:
        out = _translate_once(translator, masked, base_seed, kwargs)
        return (out if out is not None else text), True

    for attempt in range(retries + 1):
        out = _translate_once(translator, masked, base_seed + attempt, kwargs)
        if out is None:
            continue
        restored, _ = TagProtector.unmask(out, meta)
        if markup_intact(text, restored):
            return restored, True

    # Primary model couldn't preserve the markup. Try the fallback translator (a
    # pure-MT model copies masked tokens more faithfully) before giving up.
    if fallback is not None:
        return translate_preserving_tags(fallback, text, base_seed, retries, **kwargs)

    return text, False  # safe fallback: keep the original, never break Ren'Py


class TagProtector:
    """Masks and unmasks Ren'Py tags and placeholders to protect them during translation."""
    
    @staticmethod
    def mask(text: str) -> Tuple[str, List[str]]:
        """
        Replaces tags and placeholders with protected tokens like __TAG_0__.
        Returns the masked text and the list of original tags/placeholders in order.
        """
        protected_items = []
        
        def replace_tag(match) -> str:
            item = match.group(0)
            token = f"__TAG_{len(protected_items)}__"
            protected_items.append(item)
            return token

        # Mask placeholders first (longer or distinct), then formatting tags
        masked_text = PLACEHOLDER_RE.sub(replace_tag, text)
        masked_text = TAG_RE.sub(replace_tag, masked_text)
        
        return masked_text, protected_items

    @staticmethod
    def unmask(masked_text: str, original_items: List[str]) -> Tuple[str, List[str]]:
        """
        Restores original tags and placeholders in the translated text.
        Allows for minor spacing/casing variations in the tokens created by MT models.
        Returns the unmasked text and a list of warnings/errors.
        """
        warnings = []
        unmasked = masked_text
        
        for idx, item in enumerate(original_items):
            # Try to match the token with optional spaces, case-insensitive,
            # and 1-3 underscores on each side: some models thin __TAG_0__ down
            # to _TAG_0_ — still recoverable.
            token_pattern = re.compile(
                rf'_{{1,3}}\s*TAG\s*_\s*{idx}\s*_{{1,3}}',
                re.IGNORECASE
            )
            
            # Check if token exists in the text
            match = token_pattern.search(unmasked)
            if match:
                # Replace the first occurrence of the matched token
                unmasked = token_pattern.sub(item, unmasked, count=1)
            else:
                # Token was lost or modified beyond recognition
                warnings.append(f"Lost tag/placeholder '{item}' (Token: __TAG_{idx}__) in translation.")
                
        # Clean up any leftover token-like strings that might have been mangled
        # e.g., if the model duplicated a token
        leftover_pattern = re.compile(r'_{1,3}\s*TAG\s*_\s*\d+\s*_{1,3}', re.IGNORECASE)
        unmasked = leftover_pattern.sub("", unmasked)
        
        return unmasked, warnings

def protect(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Replaces [vars] and {markup} with unique tokens of the form __VAR_xxxxxxxx__.
    Returns the protected text and a dictionary mapping tokens to their original values.
    """
    mapping = {}
    
    def replace_match(match) -> str:
        original = match.group(0)
        while True:
            token = f"__VAR_{uuid.uuid4().hex[:8]}__"
            if token not in mapping:
                break
        mapping[token] = original
        return token

    # Mask placeholders first, then formatting tags
    protected_text = PLACEHOLDER_RE.sub(replace_match, text)
    protected_text = TAG_RE.sub(replace_match, protected_text)
    
    return protected_text, mapping

def restore(text: str, mapping: Dict[str, str]) -> str:
    """
    Restores original [vars] and {markup} from the token mapping.
    Allows for minor spacing/casing variations in the tokens.
    """
    restored = text
    for token, original in mapping.items():
        hex_part = token.replace("__VAR_", "").replace("__", "")
        pattern = re.compile(rf'__\s*VAR\s*_\s*{hex_part}\s*__', re.IGNORECASE)
        restored = pattern.sub(original, restored)
    return restored

def protect_batch(texts: List[str]) -> List[Tuple[str, Dict[str, str]]]:
    """Protects a batch of texts in parallel using ThreadPoolExecutor."""
    with ThreadPoolExecutor() as executor:
        return list(executor.map(protect, texts))

def restore_batch(texts: List[str], mappings: List[Dict[str, str]]) -> List[str]:
    """Restores a batch of texts in parallel using ThreadPoolExecutor."""
    with ThreadPoolExecutor() as executor:
        return list(executor.map(lambda p: restore(p[0], p[1]), zip(texts, mappings)))

class Safeguard:
    """Safeguard helper for text protection."""
    def mask(self, text: str) -> Tuple[str, Dict[str, str]]:
        return protect(text)
        
    def unmask(self, text: str, mapping: Dict[str, str]) -> str:
        return restore(text, mapping)
