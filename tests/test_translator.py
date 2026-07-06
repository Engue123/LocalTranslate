"""Unit tests for the HY-MT prompt format + sampling (L1), fully mocked."""
from unittest.mock import patch

import core.translator as T
from core.translator import language_name, LlamaCppTranslator


def test_language_name_full_names():
    assert language_name("fr") == "French"
    assert language_name("en") == "English"
    assert language_name("it") == "Italian"
    assert language_name("xx") == "xx"  # unknown -> passthrough


class _FakeLlama:
    last = {}

    def __init__(self, **kwargs):
        _FakeLlama.last["init"] = kwargs

    def __call__(self, prompt, **kwargs):
        _FakeLlama.last["prompt"] = prompt
        _FakeLlama.last["call"] = kwargs
        return {"choices": [{"text": "Bonjour le monde."}]}


def test_official_prompt_and_sampling():
    with patch("llama_cpp.Llama", _FakeLlama):
        tr = LlamaCppTranslator(model_path="/fake/model.gguf", target_lang="fr", seed=123)
        out = tr.translate_batch(["Hello world."])

    assert out == ["Bonjour le monde."]

    init = _FakeLlama.last["init"]
    assert init["seed"] == 123            # reproducible sampling
    assert init["n_ctx"] == 4096

    # Official template with the FULL target language name, no en:/fr: framing.
    assert _FakeLlama.last["prompt"] == (
        "Translate the following segment into French, "
        "without additional explanation.\n\nHello world."
    )

    call = _FakeLlama.last["call"]
    assert call["temperature"] == 0.7
    assert call["top_p"] == 0.6
    assert call["top_k"] == 20
    assert call["repeat_penalty"] == 1.05


def test_style_hint_is_not_injected_for_hymt():
    # HY-MT ignores free-form style; it must not pollute the prompt.
    with patch("llama_cpp.Llama", _FakeLlama):
        tr = LlamaCppTranslator(model_path="/fake/model.gguf", target_lang="fr",
                                style_hint="pirate slang")
        tr.translate_batch(["Hello."])
    assert "pirate" not in _FakeLlama.last["prompt"]
    assert "style" not in _FakeLlama.last["prompt"].lower()


def test_temperature_is_configurable():
    with patch("llama_cpp.Llama", _FakeLlama):
        tr = LlamaCppTranslator(model_path="/fake/model.gguf", target_lang="fr",
                                temperature=0.3)
        tr.translate_batch(["Hello."])
    assert _FakeLlama.last["call"]["temperature"] == 0.3


def test_register_you_to_tu_injected_when_you_present():
    with patch("llama_cpp.Llama", _FakeLlama):
        tr = LlamaCppTranslator(model_path="/fake/model.gguf", target_lang="fr")
        tr.translate_batch(["Are you okay?"])
    p = _FakeLlama.last["prompt"]
    assert "you -> tu" in p
    assert p.strip().endswith("Are you okay?")


def test_no_terminology_block_when_nothing_relevant():
    with patch("llama_cpp.Llama", _FakeLlama):
        tr = LlamaCppTranslator(model_path="/fake/model.gguf", target_lang="fr")
        tr.translate_batch(["Hello there."])   # no "you", no glossary term
    assert "Refer to the following" not in _FakeLlama.last["prompt"]


def test_glossary_name_locked_when_present():
    with patch("llama_cpp.Llama", _FakeLlama):
        tr = LlamaCppTranslator(model_path="/fake/model.gguf", target_lang="fr",
                                glossary={"Eileen": "Eileen"})
        tr.translate_batch(["Hello Eileen, nice to meet you."])
    p = _FakeLlama.last["prompt"]
    assert "Eileen -> Eileen" in p
    assert "you -> tu" in p   # both levers can co-occur


def test_informal_false_disables_register_nudge():
    with patch("llama_cpp.Llama", _FakeLlama):
        tr = LlamaCppTranslator(model_path="/fake/model.gguf", target_lang="fr",
                                informal=False)
        tr.translate_batch(["Are you okay?"])
    assert "you -> tu" not in _FakeLlama.last["prompt"]


def test_register_nudge_only_for_french():
    with patch("llama_cpp.Llama", _FakeLlama):
        tr = LlamaCppTranslator(model_path="/fake/model.gguf", target_lang="de")
        tr.translate_batch(["Are you okay?"])
    assert "you -> tu" not in _FakeLlama.last["prompt"]


def test_default_model_path_prefers_higher_quant(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    (models / "hy-mt.Q4_K_M.gguf").write_bytes(b"x")
    (models / "hy-mt.Q6_K.gguf").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    # __new__ avoids loading a real model; we only exercise path discovery.
    tr = LlamaCppTranslator.__new__(LlamaCppTranslator)
    assert tr._default_model_path().endswith("Q6_K.gguf")
