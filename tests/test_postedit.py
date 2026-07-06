"""Tests for the deterministic French elision/euphony post-editor."""
import pytest

from core.postedit import fix_french_elision as fx

APO = "’"


# --- it fixes the real error patterns -------------------------------------

@pytest.mark.parametrize("bad, good", [
    ("Tu te endors à côté d'elle", f"Tu t{APO}endors à côté d'elle"),
    ("vous la envoyez", f"vous l{APO}envoyez"),
    ("je ne ai rien", f"je n{APO}ai rien"),         # ne -> n', je stays (before n')
    ("que il vienne", f"qu{APO}il vienne"),
    ("de eau fraîche", f"d{APO}eau fraîche"),
    ("je aime ça", f"j{APO}aime ça"),
    ("Je ai vu", f"J{APO}ai vu"),                   # capital preserved
    ("le arbre", f"l{APO}arbre"),
])
def test_elision_fixed(bad, good):
    assert fx(bad) == good


@pytest.mark.parametrize("bad, good", [
    ("ma assistante", "mon assistante"),
    ("ta école", "ton école"),
    ("sa idée", "son idée"),
    ("Ma amie", "Mon amie"),                        # capital preserved
])
def test_possessive_euphony_fixed(bad, good):
    assert fx(bad) == good


def test_si_il_contraction():
    assert fx("si il vient") == f"s{APO}il vient"
    assert fx("si ils viennent") == f"s{APO}ils viennent"
    assert fx("si elle vient") == "si elle vient"   # NOT before elle


# --- it never breaks correct or special text (safety) ----------------------

@pytest.mark.parametrize("ok", [
    "J'ai déjà mangé.",                              # already contracted
    "Ne t'inquiète pas.",                            # te already elided before t'
    "le héros du film",                              # aspirated h: no elision
    "la haine monte",                                # aspirated h
    "ma haine",                                      # aspirated h: ma stays
    "le onze de France",                             # "onze": no elision
    "Mais oui, bien sûr",                            # "oui": no elision
    "que l'on sache",                                # already fine (l'on)
    "comme il l'a dit",                              # "me" inside "comme" untouched
    "Mets [mcname] dans la boîte",                   # placeholder untouched
    "le {i}mot{/i} juste",                           # tag: no elision before {
    "de [item] et de la magie",                      # placeholder; "de la" not vowel
    "Puis-je en avoir un ?",                          # inversion: je stays (enclitic)
    "Dois-je en parler ?",                            # inversion
    "Ai-je un espoir ?",                              # inversion
    "Donne-le à Marie.",                              # imperative enclitic: le stays
    "Prends-le ici.",                                 # imperative enclitic
])
def test_safe_no_false_positive(ok):
    assert fx(ok) == ok


def test_idempotent():
    once = fx("Tu te endors et je aime ma amie")
    assert fx(once) == once


def test_empty_and_plain():
    assert fx("") == ""
    assert fx("Bonjour") == "Bonjour"
