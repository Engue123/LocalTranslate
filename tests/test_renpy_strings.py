"""Tests for the textual strings-channel scanner (_() markers + screen text)."""
from core.renpy_ast.strings import scan_marked_strings

SAMPLE = '''
label start:
    e "Dialogue is not a marker."

screen prefs():
    text "Screen Title"
    textbutton _("Click me") action Return()
    label "Plain Label"
    tooltip "Hover help"

init python:
    define config.name = _("My Game")
    $ x = gettext_("must not match")
    $ y = __("Double underscore")
    renpy.notify(_p("Paragraph string"))
'''


def test_scan_extracts_markers_and_screen_text():
    found = {s.text for s in scan_marked_strings(SAMPLE)}
    # _() / _p() / __() markers
    assert "Click me" in found
    assert "My Game" in found
    assert "Paragraph string" in found
    assert "Double underscore" in found
    # screen text literals
    assert "Screen Title" in found
    assert "Plain Label" in found
    assert "Hover help" in found


def test_scan_ignores_identifier_prefixed_underscore():
    # gettext_("…") is an identifier call, not a translation marker.
    assert "must not match" not in {s.text for s in scan_marked_strings(SAMPLE)}


def test_scan_dialogue_is_not_captured():
    # Plain say dialogue must not be picked up by the strings scanner.
    assert "Dialogue is not a marker." not in {s.text for s in scan_marked_strings(SAMPLE)}


def test_scan_dedupes():
    text = 'text "Repeat"\ntext "Repeat"\ntextbutton _("Repeat")'
    assert [s.text for s in scan_marked_strings(text)] == ["Repeat"]


def test_scan_all_units_are_ui_kind():
    assert all(s.kind == "ui" for s in scan_marked_strings(SAMPLE))
