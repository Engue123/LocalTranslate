"""Per-language quality metadata (measured tiers) surfaced in the UI."""
from core.languages import quality_tier, language_note, QUALITY_TIER


def test_tiers_are_measured_not_guessed():
    # European = validated (judgeable); CJK = community (measured-viable, nuance open)
    assert quality_tier("fr") == "validated"
    assert quality_tier("ru") == "validated"
    assert quality_tier("ja") == "community"
    assert quality_tier("ko") == "community"
    assert quality_tier("xx") == "unknown"


def test_every_frontend_target_has_a_tier():
    # the 9 target languages of the UI selector (English is the source, not a target)
    for code in ("fr", "es", "it", "de", "pt", "ru", "ja", "zh", "ko"):
        assert code in QUALITY_TIER


def test_notes_are_honest_and_mention_fonts():
    assert "verified" in language_note("fr")
    ja = language_note("ja")
    assert "feedback welcome" in ja and "swappable" in ja      # honest, not overpromised
    assert language_note("fr").startswith("Font auto-adapted")  # fonts always handled
