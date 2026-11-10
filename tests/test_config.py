from pathlib import Path

import pytest

from agent_remote.config import ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parents[1]


def test_the_shipped_config_loads():
    config = load_config(ROOT / "config.toml")
    assert "cross" in config.bindings


def test_left_stick_directions_are_buttons():
    config = parse_config({"buttons": {"lstick_up": "pause", "lstick_right": {"keys": "tab"}}})
    assert config.bindings["lstick_up"].name == "pause"
    assert str(config.bindings["lstick_right"].chords[0]) == "tab"


def test_test_config_maps_what_we_expect(config):
    assert str(config.bindings["cross"].chords[0]) == "enter"
    assert config.bindings["r2"].name == "push_to_talk"
    assert config.bindings["triangle"].labels == ("Allow once", "Allow")
    assert config.bindings["dpad_right"].repeat is True
    assert config.settings.app_switch_delay == 0.2


def test_inline_action_on_a_button():
    config = parse_config({"buttons": {"l2": {"keys": "ctrl+enter"}}})
    assert str(config.bindings["l2"].chords[0]) == "ctrl+enter"


def test_none_leaves_a_button_unmapped():
    config = parse_config({"buttons": {"l2": "none"}})
    assert "l2" not in config.bindings


def test_click_with_fallback_keys():
    config = parse_config({"buttons": {"triangle": {"click": "Allow", "fallback_keys": "enter"}}})
    action = config.bindings["triangle"]
    assert action.labels == ("Allow",) and str(action.fallback[0]) == "enter"


@pytest.mark.parametrize(
    "data, message",
    [
        ({"nonsense": {}}, "unknown section"),
        ({"buttons": {"x_button": "send"}}, "isn't a controller button"),
        ({"buttons": {"cross": "launch_rockets"}}, "isn't an action"),
        ({"buttons": {"touchpad_swipe": "pause"}}, "can only be"),
        ({"buttons": {"cross": "scroll"}}, "scroll_up"),
        ({"actions": {"pause": {"keys": "p"}}}, "built-in"),
        ({"actions": {"a": {"keys": "ctrl+n", "click": ["x"]}}}, "either"),
        ({"actions": {"a": {"keys": "ctrl+banana"}}}, "unknown key"),
        ({"settings": {"trigger_treshold": 0.5}}, "unknown setting"),
        ({"settings": {"log_buttons": "yes"}}, "true or false"),
        ({"settings": {"trigger_threshold": 2}}, "between"),
        ({"voice": {"engine": "siri"}}, "engine"),
        ({"actions": {"a": {"click": ["x"], "next_step": "maybe"}}}, "next_step"),
        ({"actions": {"a": {"keys": "tab", "question": "dance"}}}, "question must be"),
    ],
)
def test_mistakes_are_explained(data, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(data)
