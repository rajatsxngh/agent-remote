import pytest

from ps5_agent_handler.keys import Chord, KeyParseError, parse_chord, parse_keys


def test_modifiers_are_aliased_and_ordered():
    assert parse_chord("Shift+Control+[") == Chord(("ctrl", "shift"), "[")


def test_named_function_and_punctuation_keys():
    assert parse_chord("esc") == Chord((), "escape")
    assert parse_chord("shift+F10") == Chord(("shift",), "f10")
    assert parse_chord("ctrl+`") == Chord(("ctrl",), "`")
    assert parse_chord("ctrl+\\") == Chord(("ctrl",), "\\")


def test_a_list_is_pressed_in_order():
    assert parse_keys(["ctrl+k", "enter"]) == (Chord(("ctrl",), "k"), Chord((), "enter"))


def test_prints_back_the_same_way():
    assert str(parse_chord("ctrl+shift+]")) == "ctrl+shift+]"


@pytest.mark.parametrize("bad", ["", "ctrl+", "ctrl++", "hyper+a", "ctrl+banana", "ctrl+é"])
def test_bad_shortcuts_are_rejected(bad):
    with pytest.raises(KeyParseError):
        parse_chord(bad)
