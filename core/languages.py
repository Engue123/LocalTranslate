"""
Per-language UI metadata: the translation-QUALITY expectation with the default
model (NemoMix). Kept separate from the FONT layer on purpose — the two are
decoupled:

  * Fonts (core/fonts.py) make ANY language DISPLAY. Universal, deterministic.
  * Quality here is about the MODEL only, and the model is swappable (an open-source
    user can drop in a GGUF better suited to their language — see the repo docs).

The tier says how much the default model's output can be vouched for. European
targets are judgeable and solid; CJK produces the correct script and faithful
output, but its native nuance cannot be judged here, so we invite feedback rather
than overpromise.
"""
from typing import Dict

# code -> tier. Only the frontend's target languages need an entry.
QUALITY_TIER: Dict[str, str] = {
    "fr": "validated", "es": "validated", "it": "validated",
    "de": "validated", "pt": "validated", "ru": "validated",
    "ja": "community", "zh": "community", "ko": "community",
}


def quality_tier(code: str) -> str:
    return QUALITY_TIER.get((code or "").lower(), "unknown")


def language_note(code: str) -> str:
    """One-line note for the UI under the language selector: fonts are always
    adapted; then the honest translation-quality expectation for this language."""
    tier = quality_tier(code)
    if tier == "validated":
        return "Font auto-adapted · translation quality verified for this language."
    if tier == "community":
        return ("Font auto-adapted · correct script & faithful output, native nuance "
                "unverified — feedback welcome, and the model is swappable.")
    return "Font auto-adapted for the target language."
