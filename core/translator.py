import re
from abc import ABC, abstractmethod
from typing import List, Optional, Callable
from pathlib import Path

_MAX_TERMS = 8  # cap terminology entries per line to keep the prompt lean

class BaseTranslator(ABC):
    """Abstract baseline class for local translation backends."""
    
    @abstractmethod
    def translate_batch(self, texts: List[str], style_hint: Optional[str] = None) -> List[str]:
        """Translates a batch of texts and returns the translated strings in order."""
        pass

class MockTranslator(BaseTranslator):
    """A mock translator for tests and local debugging (no ML model required)."""

    def __init__(self, prefix: str = "[FR] ", style_hint: Optional[str] = None,
                 instruct: bool = False):
        self.prefix = prefix
        self.style_hint = style_hint
        self.instruct = instruct  # engine sends per-line contexts when True
        self.glossary = {}   # accepted for engine symmetry; unused by the mock
        self.last_prompt = None
        self.context_calls = []   # log of (texts, contexts) for wiring tests

    def translate_batch(
        self,
        texts: List[str],
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        style_hint: Optional[str] = None,
        seed: Optional[int] = None,
        contexts: Optional[List[dict]] = None,
    ) -> List[str]:
        self.context_calls.append((list(texts), contexts))
        current_style = style_hint or self.style_hint
        base_prompt = "Translate the following visual novel text accurately. Preserve the tone, character voice, and formatting. Output only the translated text without explanation."
        style_hint_text = f" Translate in the following style: {current_style}." if current_style else ""
        
        # Store last prompt for verification
        self.last_prompt = f"{base_prompt}{style_hint_text}\nTranslate from {source_lang or 'en'} to {target_lang or 'fr'}:\n{texts[0] if texts else ''}\n\nTranslation:"
        
        results = [f"{self.prefix}{t}" for t in texts]
        if progress_callback:
            for i in range(len(texts)):
                progress_callback((i + 1) / len(texts), f"Translated {i+1}/{len(texts)}")
        return results

# HY-MT expects FULL language names (not ISO codes) in its prompt. Subset of the
# 36 languages it supports; unknown codes fall back to the code itself.
LANGUAGE_NAMES = {
    "en": "English", "fr": "French", "es": "Spanish", "it": "Italian",
    "de": "German", "pt": "Portuguese", "ru": "Russian", "ja": "Japanese",
    "zh": "Chinese", "ko": "Korean", "ar": "Arabic", "tr": "Turkish",
    "th": "Thai", "vi": "Vietnamese", "pl": "Polish", "nl": "Dutch",
    "cs": "Czech", "id": "Indonesian", "ms": "Malay", "uk": "Ukrainian",
    "hi": "Hindi", "fa": "Persian", "he": "Hebrew",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get((code or "").lower(), code)


# Explicit-line detector: instruct models tend to drift CLINICAL on crude sexual
# vocabulary ("cock"->"pénis", "cum"->"éjaculer"), softening the source. When a line
# is explicit we add a crude-register directive to keep the vernacular faithful.
_EXPLICIT_RE = re.compile(
    r"\b(fuck\w*|cock|dick|cum\w*|pussy|cunt|tits?|slut|whore|horny|blowjob|"
    r"asshole|jerk\s*off|hand\s*job|deepthroat|clit\w*)\b", re.IGNORECASE)

# Per-target-language crude register examples (like _GENDER_EXAMPLES): the
# generic "be crude, not clinical" directive fires for any target; the example
# string is added only where we have curated vernacular. Keep examples crude on
# purpose — a clinical example would defeat the directive.
_CRUDE_EXAMPLES = {
    "fr": ("bite/queue (cock), chatte (pussy), cul (ass), seins/nichons (tits), "
           "baiser/niquer (to fuck), jouir (to cum), foutre/sperme (cum), "
           "branler (to jerk), salope (slut), pute (whore)"),
    "es": ("polla (cock), coño (pussy), culo (ass), tetas (tits), follar (to "
           "fuck), correrse (to cum), puta (whore)"),
    "it": ("cazzo (cock), figa (pussy), culo (ass), tette (tits), scopare (to "
           "fuck), venire (to cum), troia (slut)"),
}
# Clinical/euphemistic terms the directive tells the model to AVOID, per language.
_CRUDE_AVOID = {
    "fr": "pénis, vagin, éjaculer, faire l'amour, partie intime, rapport sexuel",
    "es": "pene, vagina, eyacular, hacer el amor",
    "it": "pene, vagina, eiaculare, fare l'amore",
}


def _instruct_output_ok(out: str, source: str) -> bool:
    """A sane instruct reply: non-empty, no echo of our own prompt, and not a
    runaway answer many times longer than the source line."""
    if not out or not out.strip():
        return False
    if any(m in out for m in _INSTRUCT_ECHO_MARKERS):
        return False
    return len(out) <= 3 * len(source) + 80


# A concrete example per target language makes the gender directive RELIABLE
# (a small model obeys an example far better than an abstract rule). Only
# languages whose predicate adjectives/participles inflect for the subject's
# gender need it; others (en, de, ja, …) simply get no example. Tuple = (feminine, masculine).
_GENDER_EXAMPLES = {
    "fr": ("je suis contente, prête, allée", "je suis content, prêt, allé"),
    "es": ("estoy cansada, lista", "estoy cansado, listo"),
    "it": ("sono stanca, pronta", "sono stanco, pronto"),
    "pt": ("estou cansada, pronta", "estou cansado, pronto"),
    "ca": ("estic cansada, preparada", "estic cansat, preparat"),
    "ro": ("sunt obosită, pregătită", "sunt obosit, pregătit"),
}

# Same idea for the ADDRESSEE (the "you"/2nd person), used only when the line tells
# us the listener's gender (resolve_addressee_gender). 2nd-person predicate forms; the
# directive plus this example agrees the listener without touching the speaker's own
# person. Tuple = (feminine, masculine).
_ADDRESSEE_EXAMPLES = {
    "fr": ("tu es prête, sûre, belle", "tu es prêt, sûr, beau"),
    "es": ("estás cansada, lista", "estás cansado, listo"),
    "it": ("sei stanca, pronta", "sei stanco, pronto"),
    "pt": ("estás cansada, pronta", "estás cansado, pronto"),
    "ca": ("estàs cansada, preparada", "estàs cansat, preparat"),
    "ro": ("ești obosită, pregătită", "ești obosit, pregătit"),
}

# One-shot anchor: on ULTRA-SHORT lines ("Ready?", "Okay.") an instruct model may
# continue the system prompt's bullet list instead of translating (echoing it). A
# single worked example eliminates that. (source_line, target_line) pairs, chosen to
# ALSO demonstrate informal 2nd person (tu/du/ты) — a few-shot exemplar is an
# effective register-control lever.
_ONESHOT_EXAMPLES = {
    "fr": ("Do you need help?", "Tu as besoin d'aide ?"),
    "es": ("Do you need help?", "¿Necesitas ayuda?"),
    "it": ("Do you need help?", "Hai bisogno di aiuto?"),
    "de": ("Do you need help?", "Brauchst du Hilfe?"),
    "pt": ("Do you need help?", "Precisas de ajuda?"),
    "nl": ("Do you need help?", "Heb je hulp nodig?"),
    "pl": ("Do you need help?", "Potrzebujesz pomocy?"),
    "cs": ("Do you need help?", "Potřebuješ pomoc?"),
    "ro": ("Do you need help?", "Ai nevoie de ajutor?"),
    "ca": ("Do you need help?", "Necessites ajuda?"),
    "ru": ("Do you need help?", "Тебе нужна помощь?"),
    "uk": ("Do you need help?", "Тобі потрібна допомога?"),
    "tr": ("Do you need help?", "Yardıma ihtiyacın var mı?"),
    "en": ("Hello there.", "Hi."),
}

# Informal 2nd person per language (formality control). Used only when the register
# is casual/informal; languages without a T-V split simply get no directive.
_INFORMAL_2P = {
    "fr": "'tu' and its forms (toi, ton, ta, tes) — never the formal 'vous'",
    "es": "'tú' — never the formal 'usted'",
    "it": "'tu' — never the formal 'Lei'",
    "de": "'du' — never the formal 'Sie'",
    "pt": "the informal 'tu/você' — never 'o senhor/a senhora'",
    "ru": "'ты' — never the formal 'вы'",
    "uk": "'ти' — never the formal 'ви'",
}

# Echo detection (defence in depth): a regurgitated reply contains stable
# fragments of OUR OWN prompt — markers no legitimate VN line will contain.
_INSTRUCT_ECHO_MARKERS = (
    "FAITHFULNESS", "PRESERVE EXACTLY", "REGISTER & GENDER",
    "word-for-word", "Translate meaning",
)


def build_instruct_system_prompt(
    source_name: str,
    target_name: str,
    target_code: Optional[str] = None,
    speaker: Optional[str] = None,
    gender: Optional[str] = None,
    register: Optional[str] = None,
    glossary_terms: Optional[List[str]] = None,
    masked_tokens: bool = False,
    explicit: bool = False,
    addressee_gender: Optional[str] = None,
) -> str:
    """System prompt for an instruction-following model (the Quality tier).

    Fully parameterised by source/target language — the tool translates from the
    game's ORIGINAL language into the SELECTED language, never a hard-coded
    EN->FR. The REGISTER & GENDER lines are emitted only when known and are
    phrased generically, so they are a harmless no-op for target languages with
    no grammatical gender or no tu/vous-style register.

    `explicit=True` adds a crude-register directive (with per-language examples)
    that steers the model away from clinical/euphemistic vocabulary — fired only
    on sexually explicit source lines (see _EXPLICIT_RE in the translator).
    """
    p = [
        f"You are an elite game localizer translating a Ren'Py visual novel from "
        f"{source_name} into {target_name}, for an adult audience.",
        f"Translate the single line of game text the user sends. Output ONLY the "
        f"{target_name} translation — no quotes, no notes, no commentary, nothing else.",
        "",
        "FAITHFULNESS",
        "- Translate meaning, tone and voice idiomatically, never word-for-word.",
        # Literary/artistic framing: an aligned instruct model softens or refuses
        # explicit content unless the work is framed as deliberate adult fiction.
        # Stated up front + the no-refuse/no-disclaimer directive measurably cuts
        # euphemising on abliterated AND aligned instruct models.
        "- The source is a published work of adult literary fiction — an erotic "
        "visual novel. Its profanity, crude slang and explicit sexual content are "
        "deliberate artistic elements of the original work, not gratuitous.",
        "- Reproduce them in the translation directly and fully: never tone down, "
        "censor, euphemise, omit, refuse, moralise, or add any disclaimer or warning. "
        "A faithful translation preserves exactly the register and explicitness of "
        "the source line.",
        # Completeness: instruct models sometimes leave a sentence-initial source
        # word or phrase untranslated; this directive tells them to translate every word.
        f"- Write the reply ENTIRELY in {target_name}: translate every word, leave no "
        f"{source_name} word untranslated (proper names and the {{markup}}/[variables] "
        "below are the only exceptions).",
    ]
    if explicit:
        # Crude-register steer (per-language examples like _GENDER_EXAMPLES). The
        # generic directive fires for any target; examples/avoid-list when curated.
        line = ("- This line is sexually explicit: render it in direct, crude, "
                "vernacular language — never clinical or euphemistic wording.")
        code = (target_code or "").lower()
        ex = _CRUDE_EXAMPLES.get(code)
        if ex:
            line += f" In {target_name}, use words such as: {ex}."
        avoid = _CRUDE_AVOID.get(code)
        if avoid:
            line += f" Avoid clinical terms like: {avoid}."
        p.append(line)
    rg = []
    if register:
        rg.append(f"- Register: {register}.")
        if "informal" in register.lower() or "casual" in register.lower():
            tv = _INFORMAL_2P.get((target_code or "").lower())
            if tv:
                # SURGICAL: "you" -> informal form, but DON'T change grammatical
                # person. A blunt "use tu throughout" can make a model flip the
                # speaker's "I" into "tu" (a meaning inversion); this wording keeps
                # "I"->"je" and turns only "you" informal.
                rg.append(f"- Informal register: translate second-person address "
                          f"(\"you\") with {tv}. Do NOT change grammatical person — the "
                          f"speaker's own \"I\"/\"we\" stay first person; only \"you\" "
                          f"becomes informal.")
    if gender:
        who = f"{speaker}, " if speaker else ""
        g = gender.strip().lower()
        form = ("feminine" if g in ("woman", "female", "f")
                else "masculine" if g in ("man", "male", "m") else None)
        if form:
            # Explicit ("use feminine forms") + a concrete per-language example is
            # what makes it reliable, while staying language-agnostic via the table.
            line = (f"- The speaker is {who}a {gender}. In {target_name}, use {form} "
                    f"grammatical forms for every word that agrees with the speaker "
                    f"(adjectives, past participles, etc.).")
            ex = _GENDER_EXAMPLES.get((target_code or "").lower())
            if ex:
                line += f' For example: "{ex[0] if form == "feminine" else ex[1]}".'
            rg.append(line)
        else:
            rg.append(f"- The speaker is {who}{gender}.")
    elif speaker:
        # Name alone still helps: an instruct model infers the gender of a
        # clearly gendered name ("Eliza") and keeps the voice consistent.
        rg.append(f"- The speaker is {speaker}.")
    if addressee_gender:
        # ADDRESSEE agreement ("tu es belle/beau"). Emitted ONLY when the line itself
        # tells us the listener's gender (resolve_addressee_gender); never a blind guess.
        # SURGICAL like the tutoiement lever: agree the 2nd person only, do NOT touch
        # the speaker's own person or agreement (no je<->tu inversion).
        ag = addressee_gender.strip().lower()
        aform = ("feminine" if ag in ("woman", "female", "f")
                 else "masculine" if ag in ("man", "male", "m") else None)
        if aform:
            line = (f'- The person being addressed as "you" is a {addressee_gender}. For '
                    "adjectives and past participles that agree with the person addressed, "
                    f"use {aform} forms.")
            ex = _ADDRESSEE_EXAMPLES.get((target_code or "").lower())
            if ex:
                line += f' For example: "{ex[0] if aform == "feminine" else ex[1]}".'
            rg.append(line)
            rg.append('- Do NOT change grammatical person: the speaker\'s own "I"/"we" keep '
                      "first person and their own gender agreement; only the second person "
                      '"you" takes these forms.')
    if rg:
        p += ["", "REGISTER & GENDER"] + rg
    pres = ["- Copy verbatim, never translate: text inside {curly braces} (engine tags) "
            "and inside [square brackets] (variables)."]
    if masked_tokens:
        # The pipeline masks markup as __TAG_n__ before the call; without this line
        # some models 'clean' or substitute the token (a leading masked token can be
        # replaced by unrelated text from the prompt).
        pres.append("- The text contains placeholder tokens like __TAG_0__: copy "
                    "each token exactly as written, unchanged, at the matching place.")
    if glossary_terms:
        pres.append("- Keep these names/terms exactly as written: "
                    + ", ".join(glossary_terms) + ".")
    p += ["", "PRESERVE EXACTLY"] + pres
    # Final directive + one-shot example: anchors 'reply = the translation only'
    # (kills the short-line echo).
    p += ["", f"Now reply with the {target_name} translation of the user's line, "
              "and nothing else."]
    shot = _ONESHOT_EXAMPLES.get((target_code or "").lower())
    if shot:
        p += ["", f"Example — user sends: {shot[0]}  You reply: {shot[1]}"]
    return "\n".join(p)


class LlamaCppTranslator(BaseTranslator):
    """Llama.cpp backend for Tencent HY-MT1.5 (GGUF).

    Uses the model's *official* prompt template and recommended sampling
    (temperature 0.7, top_p 0.6, top_k 20, repetition_penalty 1.05). A fixed
    seed makes the sampled output reproducible across runs.

    Note: HY-MT is a dedicated translation model — it does NOT follow free-form
    style/system instructions, so `style_hint` is accepted for API symmetry but
    not injected into the prompt (it would be ignored or degrade output).
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        source_lang: str = "en",
        target_lang: str = "fr",
        n_gpu_layers: int = -1,
        style_hint: Optional[str] = None,
        temperature: float = 0.7,
        seed: int = 42,
        glossary: Optional[dict] = None,
        informal: bool = True,
        instruct: bool = False,
        top_p: float = 0.6,
        chat_format: Optional[str] = None,
    ):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.style_hint = style_hint
        self.temperature = temperature
        self.seed = seed
        # Instruct mode (Quality tier): use the chat template + a VN system prompt
        # instead of the pure-MT template. The registry sets this per model.
        self.instruct = instruct
        self.top_p = top_p
        # Some merges ship NO embedded chat template (NemoMix = Mistral-Nemo Base
        # merge -> llama-cpp falls back to llama-2, whose <<SYS>> tokens are
        # off-distribution for Nemo). The registry pins the correct format here
        # (e.g. "mistral-instruct") so the system prompt is delivered natively.
        self.chat_format = chat_format
        # Terminology lever (HY-MT native): locks recurring terms (e.g. character
        # names) to a fixed translation. Injected selectively, per line.
        self.glossary = glossary or {}
        # Register nudge: for French, hint "you -> tu" so casual VN dialogue
        # leans towards informal address (tutoiement).
        self.informal = informal
        self.last_prompt = None

        if not model_path:
            model_path = self._default_model_path()

        self.model_path = Path(model_path)

        from llama_cpp import Llama

        # n_ctx=4096 and full GPU offload (non-negotiable invariants). The fixed
        # seed makes temperature-based sampling reproducible run to run.
        llama_kwargs = dict(
            model_path=str(self.model_path),
            n_gpu_layers=n_gpu_layers,
            n_ctx=4096,
            seed=seed,
            verbose=False,
        )
        if chat_format:
            llama_kwargs["chat_format"] = chat_format
        self.llm = Llama(**llama_kwargs)

    def _default_model_path(self) -> str:
        # Discover any GGUF in models/, preferring higher-fidelity quants
        # (Q8 > Q6 > Q5 > Q4 …) so dropping in a better quant just works.
        dirs = [Path.cwd() / "models", Path(__file__).parent.parent / "models"]
        ggufs = []
        for d in dirs:
            if d.is_dir():
                ggufs.extend(sorted(d.glob("*.gguf")))
        if not ggufs:
            raise FileNotFoundError("No GGUF model found. Place a model in ./models/")

        order = ["q8", "q6", "q5", "q4", "q3", "q2"]
        def rank(p: Path) -> int:
            n = p.name.lower()
            return next((i for i, q in enumerate(order) if q in n), len(order))

        ggufs.sort(key=rank)
        return str(ggufs[0])

    def _terminology_for(self, text: str, tgt_code: str):
        """Select the terminology pairs relevant to THIS line (selective injection)."""
        terms = []
        # Register nudge for French: only when a 2nd-person "you" is present.
        if self.informal and (tgt_code or "").lower() in ("fr", "french"):
            if re.search(r"\byou\b", text, re.IGNORECASE):
                terms.append(("you", "tu"))
        # Glossary terms that actually appear in the line.
        for src, tgt in self.glossary.items():
            if src and re.search(rf"\b{re.escape(src)}\b", text):
                terms.append((src, tgt))
            if len(terms) >= _MAX_TERMS:
                break
        return terms

    def _compose_prompt(self, text: str, tgt_code: str, tgt_name: str) -> str:
        base = (f"Translate the following segment into {tgt_name}, "
                f"without additional explanation.\n\n{text}")
        terms = self._terminology_for(text, tgt_code)
        if not terms:
            return base
        block = "Refer to the following translations:\n" + \
                "\n".join(f"{s} -> {t}" for s, t in terms) + "\n\n"
        return block + base
        
    def translate_batch(
        self,
        texts: List[str],
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        style_hint: Optional[str] = None,
        seed: Optional[int] = None,
        contexts: Optional[List[dict]] = None,
    ) -> List[str]:
        src = source_lang or self.source_lang
        tgt = target_lang or self.target_lang
        tgt_name = language_name(tgt)
        call_seed = seed if seed is not None else self.seed

        if self.instruct:
            return self._translate_batch_instruct(
                texts, src, tgt, contexts, call_seed, progress_callback)

        results: List[str] = []
        total = len(texts)

        for i, text in enumerate(texts):
            if not text or not text.strip():
                results.append(text)
                continue

            # Official HY-MT template + selective terminology (register + glossary).
            prompt = self._compose_prompt(text, tgt, tgt_name)
            self.last_prompt = prompt

            output = self.llm(
                prompt,
                max_tokens=512,
                stop=["\n\n"],
                temperature=self.temperature,
                top_p=0.6,
                top_k=20,
                repeat_penalty=1.05,
                seed=call_seed,
            )

            result = output["choices"][0]["text"].strip()
            result = result.strip('"').strip("'")
            results.append(result)

            if progress_callback:
                progress_callback(min(1.0, (i + 1) / total), f"Translated {i+1}/{total}")

        return results

    def _translate_batch_instruct(self, texts, src, tgt, contexts, seed, progress_callback):
        """Quality-tier path: chat template + the VN system prompt, with optional
        per-line context (speaker / gender / register) supplied by the engine."""
        src_name, tgt_name = language_name(src), language_name(tgt)
        default_register = "casual / informal" if self.informal else None
        gloss = [k for k in self.glossary.keys() if k]
        results: List[str] = []
        total = len(texts)

        for i, text in enumerate(texts):
            if not text or not text.strip():
                results.append(text)
                continue
            ctx = (contexts[i] if contexts and i < len(contexts) else None) or {}
            terms = [t for t in gloss if re.search(rf"\b{re.escape(t)}\b", text)][:_MAX_TERMS]
            system = build_instruct_system_prompt(
                src_name, tgt_name, target_code=tgt,
                speaker=ctx.get("speaker"),
                gender=ctx.get("gender"),
                register=ctx.get("register", default_register),
                glossary_terms=terms or None,
                masked_tokens="__TAG_" in text,
                explicit=bool(_EXPLICIT_RE.search(text)),
                addressee_gender=ctx.get("addressee_gender"),
            )
            self.last_prompt = system + "\n\n[user]\n" + text

            # Echo guard (L5 spirit): a regurgitated/runaway reply is retried
            # once with a fresh seed, then falls back to the SOURCE text —
            # never ship garbage into a patch.
            result = None
            for attempt in range(2):
                output = self.llm.create_chat_completion(
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": text}],
                    temperature=self.temperature,
                    top_p=self.top_p,
                    max_tokens=512,
                    seed=seed + attempt,
                )
                candidate = (output["choices"][0]["message"]["content"]
                             .strip().strip('"').strip("'"))
                if _instruct_output_ok(candidate, text):
                    result = candidate
                    break
            results.append(result if result is not None else text)
            if progress_callback:
                progress_callback(min(1.0, (i + 1) / total), f"Translated {i+1}/{total}")
        return results
