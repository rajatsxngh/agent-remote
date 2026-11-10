from agent_remote.keys import parse_chord
from agent_remote.platform.mac_keys import flags_for, keycode_for, mac_chord


def mac(text):
    return mac_chord(parse_chord(text))


def test_claude_shortcuts_use_cmd_on_the_mac():
    assert mac("ctrl+n") == (("cmd",), "n")
    assert mac("ctrl+shift+[") == (("shift", "cmd"), "[")
    assert mac("ctrl+shift+e") == (("shift", "cmd"), "e")
    assert mac("ctrl+enter") == (("cmd",), "enter")
    assert mac("ctrl+v") == (("cmd",), "v")


def test_shortcuts_that_stay_ctrl():
    assert mac("ctrl+tab") == (("ctrl",), "tab")
    assert mac("ctrl+`") == (("ctrl",), "`")
    assert mac("ctrl+o") == (("ctrl",), "o")


def test_ctrl_alt_becomes_cmd_ctrl():
    assert mac("ctrl+alt+w") == (("ctrl", "cmd"), "w")


def test_text_box_moves():
    assert mac("ctrl+end") == (("cmd",), "down")
    assert mac("ctrl+home") == (("cmd",), "up")


def test_app_switcher_holds_cmd():
    assert mac("alt") == ((), "cmd")
    assert mac("escape") == ((), "escape")


def test_codes_and_flags():
    assert keycode_for("v") == 9 and keycode_for("enter") == 36 and keycode_for("[") == 33
    assert flags_for(("shift", "cmd")) == 0x120000
