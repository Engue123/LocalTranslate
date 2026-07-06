"""
Tests for the Quality-tier instruct mode: the multilingual VN system prompt and
the chat-completion routing. No real model is loaded (the system-prompt builder
is a pure function; routing is exercised with a fake llm).
"""
from core.translator import build_instruct_system_prompt, LlamaCppTranslator


# --- the system prompt is multilingual, NOT hard-coded EN->FR ---------------

def test_system_prompt_is_language_parameterised():
    p = build_instruct_system_prompt("Russian", "Spanish")
    assert "from Russian into Spanish" in p
    assert "ONLY the Spanish translation" in p
    assert "English" not in p and "French" not in p   # never hard-coded


def test_system_prompt_gender_register_conditional():
    bare = build_instruct_system_prompt("English", "Japanese")
    assert "REGISTER & GENDER" not in bare            # unknown -> omitted (no-op langs)
    full = build_instruct_system_prompt(
        "English", "French", speaker="Eliza", gender="woman", register="casual / informal")
    assert "REGISTER & GENDER" in full
    assert "Register: casual / informal." in full
    assert "Eliza, a woman" in full
    assert "use feminine grammatical forms" in full   # explicit
    assert "In French" in full                         # target-aware, not hard-coded


def test_system_prompt_gender_example_per_language():
    fr = build_instruct_system_prompt("English", "French", target_code="fr",
                                      gender="woman")
    assert 'For example: "je suis contente' in fr      # concrete FR feminine example
    es = build_instruct_system_prompt("English", "Spanish", target_code="es",
                                      gender="man")
    assert "estoy cansado" in es                        # concrete ES masculine example
    # a language without an example entry: directive present, no example
    ja = build_instruct_system_prompt("English", "Japanese", target_code="ja",
                                      gender="woman")
    assert "For example" not in ja


def test_system_prompt_speaker_without_gender():
    """Name alone is still emitted: an instruct model infers gender from a
    clearly gendered name (the cast path of the declarative-MC design)."""
    p = build_instruct_system_prompt("English", "French", speaker="Eliza")
    assert "- The speaker is Eliza." in p
    assert "REGISTER & GENDER" in p


def test_system_prompt_preserves_tags_and_glossary():
    p = build_instruct_system_prompt("English", "French", glossary_terms=["Eliza", "[mc]"])
    assert "{curly braces}" in p and "[square brackets]" in p
    assert "Eliza, [mc]" in p


def test_system_prompt_uncensored_clause():
    """Literary/artistic framing + an explicit no-refuse/no-disclaimer directive —
    measurably cuts euphemising/refusals on aligned instruct models (the user's
    'limit censorship via the system prompt' requirement)."""
    p = build_instruct_system_prompt("English", "French").lower()
    assert "adult literary fiction" in p          # the artistic framing
    assert "never tone down" in p
    assert "refuse" in p and "disclaimer" in p     # no-refuse / no-warning


def test_system_prompt_final_directive_and_oneshot():
    """Short lines made EuroLLM echo the prompt's bullet list; the final
    directive + a one-shot example killed it (measured 4/30 -> 0/30 in
    scratch/ab_prompt_fewshot.py). The example now ALSO demonstrates the
    informal 2nd person (a free register-control lever)."""
    fr = build_instruct_system_prompt("English", "French", target_code="fr")
    assert fr.endswith("Example — user sends: Do you need help?  You reply: Tu as besoin d'aide ?")
    assert "Now reply with the French translation of the user's line" in fr
    es = build_instruct_system_prompt("English", "Spanish", target_code="es")
    assert "You reply: ¿Necesitas ayuda?" in es
    # target language without an example entry: directive still present
    th = build_instruct_system_prompt("English", "Thai", target_code="th")
    assert "Now reply with the Thai translation" in th
    assert "Example —" not in th


def test_system_prompt_tutoiement_directive():
    """Informal register -> an explicit T-V directive with the target-language
    forms (formality control; the EuroLLM vous-leaning gap, 36/48). Fires only
    when the register is casual/informal and the language has a T-V split."""
    fr = build_instruct_system_prompt("English", "French", target_code="fr",
                                      register="casual / informal")
    assert "Informal register" in fr
    assert "'tu' and its forms" in fr and "never the formal 'vous'" in fr
    # MUST preserve grammatical person (the blunt wording flipped EuroLLM's I->tu)
    assert "Do NOT change grammatical person" in fr
    # formal register -> no tutoiement push
    formal = build_instruct_system_prompt("English", "French", target_code="fr",
                                          register="formal")
    assert "Informal register" not in formal
    # a language with no T-V split in the table -> no directive even if informal
    ja = build_instruct_system_prompt("English", "Japanese", target_code="ja",
                                      register="casual / informal")
    assert "Informal register" not in ja


def test_system_prompt_addressee_directive():
    """When the line names the listener, the ADDRESSEE block makes 2nd-person
    agreement follow that gender ("tu es belle/beau") with a per-language example,
    while keeping the speaker's own person (measured 50%->85%, no je<->tu flip)."""
    f = build_instruct_system_prompt("English", "French", target_code="fr",
                                     addressee_gender="woman")
    assert 'person being addressed as "you" is a woman' in f
    assert "use feminine forms" in f
    assert "tu es prête, sûre, belle" in f            # per-language 2nd-person example
    assert "Do NOT change grammatical person" in f    # surgical: no person flip
    m = build_instruct_system_prompt("English", "French", target_code="fr",
                                     addressee_gender="man")
    assert "use masculine forms" in m and "tu es prêt, sûr, beau" in m
    # no addressee gender -> no directive
    assert "person being addressed" not in build_instruct_system_prompt(
        "English", "French", target_code="fr")
    # genderless target -> directive fires but no (French) example leaks in
    ja = build_instruct_system_prompt("English", "Japanese", target_code="ja",
                                      addressee_gender="woman")
    assert "use feminine forms" in ja and "tu es" not in ja


def test_system_prompt_completeness_directive():
    """Always tells the model to leave NO source-language word untranslated
    (EuroLLM left 'Gimme that big fat cock' verbatim, measured)."""
    p = build_instruct_system_prompt("English", "French")
    assert "ENTIRELY in French" in p and "leave no English word untranslated" in p


# --- instruct routing (fake llm, no real model) ----------------------------

class _FakeLLM:
    def __init__(self):
        self.calls = []

    def create_chat_completion(self, messages, **kw):
        self.calls.append((messages, kw))
        return {"choices": [{"message": {"content": "TR:" + messages[-1]["content"]}}]}


def _instruct_translator(glossary=None, informal=True):
    tr = LlamaCppTranslator.__new__(LlamaCppTranslator)
    tr.source_lang, tr.target_lang = "en", "fr"
    tr.style_hint = None
    tr.temperature, tr.top_p, tr.seed = 0.3, 0.9, 42
    tr.glossary = glossary or {}
    tr.informal = informal
    tr.instruct = True
    tr.last_prompt = None
    tr.llm = _FakeLLM()
    return tr


def test_instruct_routing_builds_chat_with_context():
    tr = _instruct_translator(glossary={"Eliza": "Eliza"})
    out = tr.translate_batch(
        ["I'm ready, Eliza."],
        contexts=[{"speaker": "Eliza", "gender": "woman", "register": "casual"}],
    )
    assert out == ["TR:I'm ready, Eliza."]
    messages, kw = tr.llm.calls[0]
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    system = messages[0]["content"]
    assert "woman" in system and "Register: casual." in system
    assert "Eliza" in system                          # glossary term present in the line
    assert kw["temperature"] == 0.3 and kw["top_p"] == 0.9


def test_instruct_default_register_from_informal_flag():
    tr = _instruct_translator(informal=True)
    tr.translate_batch(["Hello there."])
    system = tr.llm.calls[0][0][0]["content"]
    assert "casual / informal" in system


def test_instruct_blank_text_passthrough():
    tr = _instruct_translator()
    assert tr.translate_batch(["", "  "]) == ["", "  "]
    assert tr.llm.calls == []                          # blanks never hit the model


# --- echo guard: retry once, then fall back to the source text --------------

class _EchoThenGoodLLM:
    """Regurgitates the prompt on the first call, answers properly after."""
    ECHO = "FAITHFULNESS\n- Translate meaning, tone and voice idiomatically."

    def __init__(self, good="Prête."):
        self.calls = []
        self._good = good

    def create_chat_completion(self, messages, **kw):
        self.calls.append((messages, kw))
        content = self.ECHO if len(self.calls) == 1 else self._good
        return {"choices": [{"message": {"content": content}}]}


class _AlwaysEchoLLM(_EchoThenGoodLLM):
    def create_chat_completion(self, messages, **kw):
        self.calls.append((messages, kw))
        return {"choices": [{"message": {"content": self.ECHO}}]}


def test_echo_guard_retries_with_fresh_seed():
    tr = _instruct_translator()
    tr.llm = _EchoThenGoodLLM()
    out = tr.translate_batch(["Ready?"], seed=42)
    assert out == ["Prête."]
    assert len(tr.llm.calls) == 2
    assert tr.llm.calls[0][1]["seed"] == 42 and tr.llm.calls[1][1]["seed"] == 43


def test_echo_guard_falls_back_to_source():
    """Never ship a regurgitated prompt into a patch — keep the original line."""
    tr = _instruct_translator()
    tr.llm = _AlwaysEchoLLM()
    out = tr.translate_batch(["Ready?"])
    assert out == ["Ready?"]                            # L5 spirit: safe fallback
    assert len(tr.llm.calls) == 2                       # exactly one retry


def test_echo_guard_rejects_runaway_length():
    """A 'translation' many times longer than a short source line is garbage
    even without literal echo markers."""
    from core.translator import _instruct_output_ok
    assert _instruct_output_ok("Prête.", "Ready?") is True
    assert _instruct_output_ok("x" * 500, "Ready?") is False
    assert _instruct_output_ok("", "Ready?") is False


def test_system_prompt_crude_register_directive():
    """explicit=True adds a crude-register steer with per-language examples +
    an avoid-list; off by default. Multilingual: generic directive even with
    no example table entry."""
    off = build_instruct_system_prompt("English", "French", target_code="fr")
    assert "sexually explicit" not in off

    fr = build_instruct_system_prompt("English", "French", target_code="fr",
                                      explicit=True)
    assert "sexually explicit" in fr and "crude" in fr
    assert "bite/queue (cock)" in fr                  # FR crude examples
    assert "Avoid clinical terms" in fr and "pénis" in fr
    # a target language without a curated table still gets the generic directive
    ja = build_instruct_system_prompt("English", "Japanese", target_code="ja",
                                      explicit=True)
    assert "sexually explicit" in ja
    assert "use words such as" not in ja              # no example table -> no examples


def test_instruct_fires_crude_directive_only_on_explicit_lines():
    tr = _instruct_translator()
    tr.translate_batch(["Give me that cock.", "Good morning, neighbour."])
    sys_explicit = tr.llm.calls[0][0][0]["content"]
    sys_plain = tr.llm.calls[1][0][0]["content"]
    assert "sexually explicit" in sys_explicit
    assert "sexually explicit" not in sys_plain


def test_system_prompt_masked_tokens_directive():
    """When the pipeline masks markup (__TAG_n__), the prompt must say so —
    measured: without it EuroLLM substitutes the token (even with the
    SPEAKER's name from this very prompt)."""
    p = build_instruct_system_prompt("English", "French", masked_tokens=True)
    assert "__TAG_0__" in p and "unchanged" in p
    assert "__TAG_0__" not in build_instruct_system_prompt("English", "French")


def test_instruct_adds_token_directive_only_for_masked_text():
    tr = _instruct_translator()
    tr.translate_batch(["__TAG_0__ gets up.", "Plain line."])
    sys_masked = tr.llm.calls[0][0][0]["content"]
    sys_plain = tr.llm.calls[1][0][0]["content"]
    assert "placeholder tokens" in sys_masked
    assert "placeholder tokens" not in sys_plain
