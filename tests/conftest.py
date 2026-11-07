import tomllib

import pytest

from ps5_agent_handler.config import parse_config

# A fixed button map, so the tests don't depend on your edits to config.toml.
TEST_TOML = """
[buttons]
cross = "send"
circle = "stop"
triangle = "approve"
dpad_right = "focus_next"
r2 = "push_to_talk"
square = "toggle_talk"
create = "jump_to_attention"
l3 = "effort_down"
r3 = "effort_up"
options = "model_next"
l1 = "previous_app"
r1 = "next_app"
ps = "pause"
l2 = "scroll_down"
touchpad_swipe = "scroll"
right_stick = "scroll"

[actions]
send = { keys = "enter" }
stop = { keys = "escape" }
focus_next = { keys = "tab", repeat = true }
approve = { click = ["Allow once", "Allow"] }

[settings]
app_switch_delay = 0.2
log_buttons = false
"""


@pytest.fixture
def config():
    return parse_config(tomllib.loads(TEST_TOML))
