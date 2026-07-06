import pytest
from pathlib import Path

from core.detector import detect, LanguageDetector
from core.translator import MockTranslator, LlamaCppTranslator


def test_language_detection():
    """Detects dominant language on sample texts."""
    texts_en = [
        "Welcome to the first episode.",
        "You enter the room.",
        "Yes, it is.",
        "No, go back."
    ]
    texts_fr = [
        "Bienvenue dans le premier épisode.",
        "Vous entrez dans la pièce.",
        "Oui, c'est ça.",
        "Non, retournez en arrière."
    ]
    
    assert detect(texts_en) == "en"
    assert detect(texts_fr) == "fr"
    
    # Check LanguageDetector class helper
    assert LanguageDetector.detect(texts_en) == "en"


def test_style_hint_in_prompt():
    """Verifies that style hint is present in the final prompt of MockTranslator."""
    translator = MockTranslator(style_hint="pirate slang")
    texts = ["Hello my friend."]
    
    # Run batch translation
    translator.translate_batch(texts)
    
    assert translator.last_prompt is not None
    assert "Translate the following visual novel text accurately." in translator.last_prompt
    assert "Translate in the following style: pirate slang." in translator.last_prompt

    # Test override in translate_batch
    translator.translate_batch(texts, style_hint="formal French")
    assert "Translate in the following style: formal French." in translator.last_prompt


def test_llama_translation():
    """Verify translation works correctly using the local Llama.cpp model."""
    # Find local model path
    model_path = Path.cwd() / "models" / "hy-mt1.5-1.8b-q4_k_m.gguf"
    assert model_path.exists(), "Model GGUF file is required for tests"
    
    # Load translator
    translator = LlamaCppTranslator(
        model_path=str(model_path),
        source_lang="en",
        target_lang="fr",
        n_gpu_layers=-1
    )
    
    # Test simple translation
    texts = ["Welcome to the first episode.", "Language"]
    
    called_progress = []
    def progress_callback(progress, message):
        called_progress.append((progress, message))
        
    translated = translator.translate_batch(texts, progress_callback=progress_callback)
    
    assert len(translated) == 2
    assert len(translated[0]) > 0
    assert len(translated[1]) > 0
    
    # Progress callback should be called twice
    assert len(called_progress) == 2
    assert called_progress[0][0] == 0.5
    assert called_progress[1][0] == 1.0
