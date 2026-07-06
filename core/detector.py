from typing import List, Optional
from fast_langdetect import detect as fast_detect

# Mapping standard ISO language codes to NLLB language codes
NLLB_LANG_MAP = {
    "en": "eng_Latn",
    "fr": "fra_Latn",
    "es": "spa_Latn",
    "it": "ita_Latn",
    "de": "deu_Latn",
    "pt": "por_Latn",
    "ru": "rus_Cyrl",
    "ja": "jpn_Jpan",
    "zh": "zho_Hans",
    "ko": "kor_Hang",
}

def detect(texts: List[str], top_n: int = 20) -> Optional[str]:
    """
    Concatenates the first top_n non-empty strings and returns the dominant ISO 639-1 language code.
    Returns None if detection fails or input is empty.
    """
    filtered = [t.strip() for t in texts if t.strip()]
    if not filtered:
        return None
        
    combined = " ".join(filtered[:top_n])
    if not combined.strip():
        return None
        
    try:
        res = fast_detect(combined)
        if res and isinstance(res, list) and len(res) > 0:
            # fast_langdetect returns dict or list of dicts. We handle both.
            if isinstance(res[0], dict):
                lang = res[0].get("lang")
                if lang:
                    return lang.lower()
            elif isinstance(res, dict):
                lang = res.get("lang")
                if lang:
                    return lang.lower()
    except Exception:
        pass
        
    return None

def to_model_code(iso: str) -> str:
    """Maps ISO 639-1 code to model prefix (NLLB code format)."""
    iso = iso.lower()
    return NLLB_LANG_MAP.get(iso, f"{iso}_Latn")

class LanguageDetector:
    @staticmethod
    def detect(texts: List[str], top_n: int = 20) -> Optional[str]:
        return detect(texts, top_n)
