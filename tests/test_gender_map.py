"""
Tests for declarative MC gender support (core/renpy_ast/gender.py).

The protagonist's display name is DYNAMIC ([mc], [player_name]) so no gender
can be inferred from the game text; the user declares it (UI/CLI). This covers
the deterministic plumbing: which tags ARE the MC, per-speaker prompt contexts,
and the tag->name collector. The def patterns mirror the real validation games
(DivineHeel / GuiltyPleasure / Nephilim), measured in scratch/probe_gender_map.py.

No model is loaded (mock-only invariant); AST nodes are built in memory with
the same pattern as test_renpy_ast.py.
"""
import types

from types import SimpleNamespace

from core.renpy_ast.loader import ast_classes
from core.renpy_ast.gender import (
    collect_character_defs,
    find_mc_tags,
    build_speaker_contexts,
    normalize_mc_gender,
    strip_renpy_tags,
    infer_cast_genders,
    resolve_addressee_gender,
)


def _lines(*texts):
    """Spoken lines for the inference scanner (only .what is read)."""
    return [SimpleNamespace(what=t) for t in texts]

# Real-game def patterns (from the probe's measurements).
DIVINEHEEL_DEFS = {
    "Vlad": "{color=#1b45a3}[player_name]{/color}",   # the hero (renamed by player)
    "Vladth": "[player_name]",                        # hero, thinking
    "Rosa": "{color=#ab2746}Rosa{/color}",
    "Miji": "{color=#2442b1}Miji{/color}",
    "Mijith": "[Miji]",                               # Miji's inner voice — NOT the MC
}
NEPHILIM_DEFS = {
    "mc": "[mc]",
    "mct": "[mc], Thinking",
    "el": "Eliza",
    "elt": "Eliza, Thinking",
    "uk": "???",
}


# -- in-memory AST helpers (same pattern as test_renpy_ast.py) ---------------

def make_define(varname, source_code):
    classes = ast_classes()
    Define = classes["Define"]
    d = Define.__new__(Define)
    PyCode = classes.get("PyCode")
    if PyCode is not None:
        code = PyCode.__new__(PyCode)
        code.source = source_code
    else:
        code = types.SimpleNamespace(source=source_code)
    d.code = code
    d.varname = varname
    d.store = "store"
    d.linenumber = 0
    return d


def make_label(name, block):
    Label = ast_classes()["Label"]
    lab = Label.__new__(Label)
    lab.__dict__["name"] = name
    lab.block = block
    lab.parameters = None
    lab.hide = False
    lab.linenumber = 0
    return lab


# -- normalize / strip --------------------------------------------------------

def test_normalize_mc_gender():
    assert normalize_mc_gender("m") == "man"
    assert normalize_mc_gender("Man") == "man"
    assert normalize_mc_gender("HOMME") == "man"
    assert normalize_mc_gender("f") == "woman"
    assert normalize_mc_gender("femme") == "woman"
    assert normalize_mc_gender("female") == "woman"
    assert normalize_mc_gender(None) is None
    assert normalize_mc_gender("") is None
    assert normalize_mc_gender("unspecified") is None


def test_strip_renpy_tags():
    assert strip_renpy_tags("{color=#ab2746}Rosa{/color}") == "Rosa"
    assert strip_renpy_tags("  Eliza ") == "Eliza"
    assert strip_renpy_tags("[mc]") == "[mc]"          # vars are kept, not tags
    assert strip_renpy_tags(None) == ""


# -- collect_character_defs (AST) ---------------------------------------------

def test_collect_character_defs_keeps_varname():
    stmts = [
        make_define("e", 'Character("Eileen")'),
        make_define("x", "5"),                                   # not a Character
        make_label("start", [make_define("v", 'DynamicCharacter ("Vex")')]),
    ]
    defs = collect_character_defs(stmts)
    assert defs == {"e": "Eileen", "v": "Vex"}


# -- find_mc_tags ---------------------------------------------------------------

def test_find_mc_tags_divineheel_pattern():
    """Dynamic-name tags are the MC — but '[Miji]' aliases the known character
    Miji (her inner voice) and must NOT receive the declared gender."""
    assert find_mc_tags(DIVINEHEEL_DEFS) == {"Vlad", "Vladth"}


def test_find_mc_tags_nephilim_pattern():
    """'[mc], Thinking' is still the MC (the variable resolves to no character)."""
    assert find_mc_tags(NEPHILIM_DEFS) == {"mc", "mct"}


def test_find_mc_tags_all_static_is_empty():
    assert find_mc_tags({"e": "Eileen", "uk": "???"}) == set()
    assert find_mc_tags({}) == set()


# -- build_speaker_contexts -----------------------------------------------------

def test_contexts_declared_man_divineheel():
    ctx = build_speaker_contexts(DIVINEHEEL_DEFS, mc_gender="m")
    assert ctx["Vlad"] == {"speaker": None, "gender": "man"}     # dynamic name
    assert ctx["Vladth"] == {"speaker": None, "gender": "man"}
    assert ctx["Rosa"] == {"speaker": "Rosa", "gender": None}    # cast: name only
    assert ctx["Mijith"] == {"speaker": "Miji", "gender": None}  # alias, not MC


def test_contexts_thinking_suffix_maps_to_base_character():
    ctx = build_speaker_contexts(NEPHILIM_DEFS, mc_gender="woman")
    assert ctx["elt"] == {"speaker": "Eliza", "gender": None}
    assert ctx["mct"] == {"speaker": None, "gender": "woman"}
    assert "uk" not in ctx                       # "???" carries no information


def test_contexts_unknown_comma_name_kept_whole():
    """A comma name whose head is NOT a known character stays as-is."""
    ctx = build_speaker_contexts({"x": "Smith, John"})
    assert ctx["x"]["speaker"] == "Smith, John"


def test_contexts_without_declared_gender_omits_mc():
    """No declaration -> MC tags have nothing to contribute (today's behaviour)."""
    ctx = build_speaker_contexts(DIVINEHEEL_DEFS, mc_gender=None)
    assert "Vlad" not in ctx and "Vladth" not in ctx
    assert ctx["Rosa"] == {"speaker": "Rosa", "gender": None}


def test_ast_to_contexts_end_to_end():
    """collect_character_defs -> build_speaker_contexts on an in-memory AST."""
    stmts = [
        make_define("mc", 'Character("[mc]")'),
        make_define("el", 'Character ("Eliza", who_color="#ca61ed")'),
    ]
    ctx = build_speaker_contexts(collect_character_defs(stmts), mc_gender="f")
    assert ctx["mc"] == {"speaker": None, "gender": "woman"}
    assert ctx["el"] == {"speaker": "Eliza", "gender": None}


# -- cast-gender inference (pronoun scan) ---------------------------------------

def test_infer_cast_genders_from_pronoun_cooccurrence():
    """A name voting with she/her in the same sentence resolves to woman;
    pronouns in sentences NOT mentioning the name never count."""
    defs = {"el": "Eliza", "da": "Dalen"}
    units = _lines(
        "Eliza said she would come, and she did.",        # 2 F votes
        "I saw Eliza yesterday; her dress was red.",      # 1 F vote
        "Eliza smiled because she knew.",                 # 1 F vote -> total 4
        "She left early.",                                # no name -> no vote
        "Dalen grabbed his sword. He ran.",               # 1 M (He ran: no name)
        "Dalen said he was ready, his hands steady.",     # 2 M votes
        "He is tall.",                                    # no name -> no vote
        "Dalen, he won.",                                 # 1 M vote -> total 4
    )
    g = infer_cast_genders(defs, units)
    assert g == {"Eliza": "woman", "Dalen": "man"}


def test_infer_below_threshold_or_ambiguous_stays_unknown():
    defs = {"x": "Flynn", "y": "Maisy"}
    units = _lines(
        "Flynn said he was fine; she told Flynn her side too.",  # 1M+2F mixed
        "Flynn, he left. Flynn and his dog. She gave Flynn her word.",  # noisy
        "Maisy smiled, she waved.",                       # only 1 F vote (<4)
    )
    g = infer_cast_genders(defs, units)
    assert "Flynn" not in g               # ambiguous ratio -> no verdict
    assert "Maisy" not in g               # below the vote threshold


def test_infer_gendered_title_is_a_strong_signal():
    """'Aunt Sarah' counts x3: one title mention + one pronoun reaches a verdict."""
    defs = {"s": "Sarah"}
    units = _lines("Aunt Sarah waved as she arrived.")    # 3 (title) + 1 (she)
    assert infer_cast_genders(defs, units) == {"Sarah": "woman"}


def test_infer_ignores_markup_and_dynamic_names():
    defs = {"r": "{color=#ab}Rosa{/color}", "mc": "[mc]"}
    units = _lines("{i}Rosa{/i} said she was here, she and [mc].",
                   "Rosa told me she won. Rosa knew she could.")
    g = infer_cast_genders(defs, units)
    assert g == {"Rosa": "woman"}         # color tags normalized; [mc] unscannable


def test_contexts_cast_gets_inferred_gender_mc_keeps_declared():
    defs = dict(NEPHILIM_DEFS)            # mc/mct dynamic, el/elt = Eliza, uk = ???
    cast = {"Eliza": "woman"}
    ctx = build_speaker_contexts(defs, mc_gender="m", cast_genders=cast)
    assert ctx["el"] == {"speaker": "Eliza", "gender": "woman"}
    assert ctx["elt"] == {"speaker": "Eliza", "gender": "woman"}   # alias inherits
    assert ctx["mc"] == {"speaker": None, "gender": "man"}         # declared wins
    assert "uk" not in ctx                                         # still nothing


def test_cast_thinking_tag_interpolating_a_character_tag_is_not_mc():
    """DivineHeel pattern (measured): `define mi = Character("{color}Miji…")`
    and the thinking tag's display is '[mi]' — Ren'Py interpolates the
    CHARACTER TAG. Must resolve as Miji's alias (inherit her inferred gender),
    never as MC. The hero's own rename default ("Vlad") matches no cast
    display name, so he stays MC."""
    defs = {
        "Vlad": "{color=#1b45a3}[player_name]{/color}",
        "mi": "{color=#2442b1}Miji{/color}",
        "mith": "[mi]",                       # interpolates the tag `mi`
    }
    name_vars = {"player_name": "Vlad"}       # default player_name = "Vlad"
    assert find_mc_tags(defs, name_vars) == {"Vlad"}

    ctx = build_speaker_contexts(defs, mc_gender="m",
                                 cast_genders={"Miji": "woman"}, name_vars=name_vars)
    assert ctx["mith"] == {"speaker": "Miji", "gender": "woman"}  # inherits, not M
    assert ctx["Vlad"] == {"speaker": None, "gender": "man"}


def test_rename_variable_with_literal_default_resolves_too():
    """Other shape: `default jas_name = "Jasmina"` + display '[jas_name]'."""
    defs = {"ja": "Jasmina", "jath": "[jas_name]"}
    name_vars = {"jas_name": "Jasmina"}
    assert find_mc_tags(defs, name_vars) == set()
    ctx = build_speaker_contexts(defs, cast_genders={"Jasmina": "woman"},
                                 name_vars=name_vars)
    assert ctx["jath"] == {"speaker": "Jasmina", "gender": "woman"}


# -- engine wiring (mock translator, real extractor/generator) -----------------
# Define/PyCode nodes don't survive a pickle round-trip (gotcha §5.3), so the
# demo game is a fake .rpyc on disk + load_ast monkeypatched to in-memory stmts
# (same pattern as test_renpy_ast.py::test_collect_glossary_from_jobs).


def make_say(who, what):
    Say = ast_classes()["Say"]
    s = Say.__new__(Say)
    s.who = who
    s.what = what
    s.with_ = None
    s.interact = True
    s.attributes = None
    s.temporary_attributes = None
    s.arguments = None
    s.identifier = None
    s.explicit_identifier = False
    s.linenumber = 0
    return s


def _demo_game(tmp_path, monkeypatch):
    """A tiny game: the MC and Eliza both say "Ready." (the dedup-split case)."""
    stmts = [
        make_define("mc", 'Character("[mc]")'),
        make_define("el", 'Character("Eliza")'),
        make_label("start", [
            make_say("mc", "Ready."),
            make_say("el", "Ready."),
            make_say("el", "Hi!"),
            make_say(None, "Dust everywhere."),
        ]),
    ]
    monkeypatch.setattr("core.renpy_ast.load_ast",
                        lambda src, try_harder=False: stmts)
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "script.rpyc").write_bytes(b"fake-rpyc")  # content unused (patched)
    return tmp_path / "src", tmp_path / "out"


# -- addressee gender ("tu es belle") -------------------------------------------

def test_resolve_addressee_gender():
    """Conservative listener-gender resolution: fires only on an [mc] ref, a
    trailing gendered vocative, or a trailing name vocative in the cast; abstains
    (None) on no-signal lines AND on the interjection/possessive traps that would
    otherwise masculinise lines aimed at women."""
    cast = {"Penelope": "woman", "Keating": "man"}
    R = resolve_addressee_gender
    assert R("Hey [mc], you ready?", "m", cast) == "man"        # [mc] -> declared MC
    assert R("Look at you, [mc]!", "f", cast) == "woman"
    assert R("You're drunk, girl.", "m", cast) == "woman"       # trailing fem vocative
    assert R("Are you sure, princess?", "m", cast) == "woman"
    assert R("You're a good man, sir.", "f", cast) == "man"     # trailing masc vocative
    assert R("Are you sure, Penelope?", "m", cast) == "woman"   # name -> inferred cast
    # traps -> None (never a blind guess)
    assert R("Are you ready?", "m", cast) is None               # no signal
    assert R("Oh man, you're so strong", "f", cast) is None     # interjection, not address
    assert R("Are you afraid of my daddy?", "f", cast) is None  # possessive, not address
    assert R("Are you done, Mr. Playboy?", "m", cast) is None   # name not in cast
    assert R("Ready, [mc]?", None, cast) is None                # MC gender unspecified


def test_engine_routes_addressee_gender(tmp_path, monkeypatch):
    """A line naming its listener carries an addressee_gender to the translator
    (a trailing [mc] ref -> the declared MC gender); a no-signal line carries none."""
    from core.engine import TranslationEngine
    from core.translator import MockTranslator

    stmts = [
        make_define("el", 'Character("Eliza")'),
        make_label("start", [
            make_say("el", "Are you ready, [mc]?"),   # addresses the MC -> man
            make_say("el", "Are you ready?"),          # no signal -> no addressee
        ]),
    ]
    monkeypatch.setattr("core.renpy_ast.load_ast",
                        lambda src, try_harder=False: stmts)
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "script.rpyc").write_bytes(b"fake-rpyc")

    tr = MockTranslator(instruct=True)
    res = TranslationEngine(source_dir=tmp_path / "src", output_dir=tmp_path / "out",
                            target_lang="fr", translator=tr, mc_gender="m").run(
        progress_callback=lambda p, m: None)
    assert not res.errors

    # (the translator receives the MASKED text, so key by the resolved addressee
    # instead): exactly one line names its listener -> addressee_gender = the MC's.
    addr = [(contexts or [{}])[0].get("addressee_gender")
            for _texts, contexts in tr.context_calls]
    assert addr.count("man") == 1            # the "..., [mc]?" line
    assert addr.count(None) == len(addr) - 1  # every other line: no addressee directive


def test_engine_routes_contexts_to_instruct_translator(tmp_path, monkeypatch):
    """Instruct mode: per-line {speaker, gender} reaches the translator, and the
    same text said by differently-gendered speakers is translated SEPARATELY."""
    from core.engine import TranslationEngine
    from core.translator import MockTranslator

    src, out = _demo_game(tmp_path, monkeypatch)
    tr = MockTranslator(instruct=True)
    res = TranslationEngine(source_dir=src, output_dir=out, target_lang="fr",
                            translator=tr, mc_gender="m").run(
        progress_callback=lambda p, m: None)
    assert not res.errors

    calls = {}
    for texts, contexts in tr.context_calls:
        ctx = (contexts or [{}])[0] or {}
        calls.setdefault(texts[0], []).append((ctx.get("speaker"), ctx.get("gender")))

    # "Ready." split in two: the MC (declared man, dynamic name) and Eliza (name only).
    assert set(calls["Ready."]) == {(None, "man"), ("Eliza", None)}
    assert len(calls["Ready."]) == 2
    assert calls["Hi!"] == [("Eliza", None)]
    assert calls["Dust everywhere."] == [(None, None)]   # narrator: no context

    # Both "Ready." units got their translation assigned.
    ready = [u for u in res.units if u.original_text == "Ready."]
    assert len(ready) == 2
    assert all(u.translated_text == "[FR] Ready." for u in ready)


def test_engine_infers_cast_gender_end_to_end(tmp_path, monkeypatch):
    """The narrator mentions Eliza with pronouns -> her lines reach the
    translator with an EXPLICIT inferred gender (the A/B showed a bare name
    yields no agreement: 2/16; explicit gender performs like the MC's 7/7)."""
    from core.engine import TranslationEngine
    from core.translator import MockTranslator

    stmts = [
        make_define("el", 'Character("Eliza")'),
        make_label("start", [
            make_say(None, "Eliza said she was ready, and she smiled."),
            make_say(None, "Eliza knew she would win, she always did."),
            make_say("el", "Ready."),
        ]),
    ]
    monkeypatch.setattr("core.renpy_ast.load_ast",
                        lambda src, try_harder=False: stmts)
    game = tmp_path / "src" / "game"
    game.mkdir(parents=True)
    (game / "script.rpyc").write_bytes(b"fake-rpyc")

    tr = MockTranslator(instruct=True)
    res = TranslationEngine(source_dir=tmp_path / "src", output_dir=tmp_path / "out",
                            target_lang="fr", translator=tr).run(
        progress_callback=lambda p, m: None)
    assert not res.errors

    ctxs = {texts[0]: (contexts or [None])[0] for texts, contexts in tr.context_calls}
    assert ctxs["Ready."] == {"speaker": "Eliza", "gender": "woman"}
    assert ctxs["Eliza said she was ready, and she smiled."] is None  # narrator


def test_engine_mt_mode_keeps_text_dedup_and_warns(tmp_path, monkeypatch):
    """Pure-MT mode: no contexts are sent, dedup stays by text alone, and a
    declared-but-unusable MC gender is surfaced honestly in the log."""
    from core.engine import TranslationEngine
    from core.translator import MockTranslator

    src, out = _demo_game(tmp_path, monkeypatch)
    tr = MockTranslator()                                # instruct=False
    msgs = []
    res = TranslationEngine(source_dir=src, output_dir=out, target_lang="fr",
                            translator=tr, mc_gender="m").run(
        progress_callback=lambda p, m: msgs.append(m))
    assert not res.errors

    texts = [t[0] for t, _ in tr.context_calls]
    assert texts.count("Ready.") == 1                    # dedup unchanged
    assert all(c is None for _, c in tr.context_calls)   # never any context
    assert any("pure-MT" in m for m in msgs)             # honest warning


def test_ui_mc_gender_choices_align_with_engine():
    """The GUI selector's values map onto what the engine/prompt understand
    (and the safe default is 'no directive at all')."""
    from ui.app import MC_GENDER_CHOICES

    assert MC_GENDER_CHOICES["Unspecified"] is None
    assert normalize_mc_gender(MC_GENDER_CHOICES["Man"]) == "man"
    assert normalize_mc_gender(MC_GENDER_CHOICES["Woman"]) == "woman"
