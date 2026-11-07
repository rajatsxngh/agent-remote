from ps5_agent_handler.menus import (
    MenuItem,
    choose,
    current_index,
    effort_candidates,
    effort_from_label,
    effort_rank,
)


def test_effort_is_read_from_the_model_button():
    assert effort_from_label("Model: Opus 5.5 Medium") == "medium"
    assert effort_from_label("Model: Opus 5.5 Extra high") == "xhigh"
    assert effort_from_label("Model: Opus 5.5 Max 3.5x or more usage") == "max"
    assert effort_from_label("Model: Haiku 5.5") is None


def test_effort_candidates_go_outward_from_the_current_level():
    assert effort_candidates("medium", 1) == ["high", "xhigh", "max"]
    assert effort_candidates("medium", -1) == ["low"]
    assert effort_candidates("max", 1) == []
    assert effort_candidates("high", -1) == ["medium", "low"]


def test_the_real_model_menu():
    # What Claude's model menu showed when probed: radio items plus a few commands.
    items = [
        radio("Opus 5.5", True),
        radio("Fable 5.1"),
        radio("Sonnet 5.5"),
        radio("Haiku 5.5"),
        MenuItem("Effort Medium"),
        MenuItem("More models"),
        MenuItem("Enable fast mode Requires usage credits Buy credit"),
    ]
    assert choose(items, 1, "Model: Opus 5.5 Medium", "model")[::2] == ("changed", "Fable 5.1")
    assert choose(items, -1, "Model: Opus 5.5 Medium", "model")[::2] == ("same", "Opus 5.5")


def radio(name, checked=False):
    return MenuItem(name, checked=checked, checkable=True)


EFFORT = [radio("Low"), radio("Medium", True), radio("High"), radio("Max")]


def test_effort_steps_up_and_down():
    assert choose(EFFORT, 1, "", "effort")[::2] == ("changed", "High")
    assert choose(EFFORT, -1, "", "effort")[::2] == ("changed", "Low")


def test_effort_stops_at_the_ends():
    top = [radio("Low"), radio("Medium"), radio("High"), radio("Max", True)]
    outcome, item, label = choose(top, 1, "", "effort")
    assert outcome == "same" and label == "Max" and item is top[3]  # pressing it just closes the menu


def test_effort_menu_listed_highest_first_still_goes_the_right_way():
    reversed_menu = [radio("Max"), radio("High", True), radio("Medium"), radio("Low")]
    assert choose(reversed_menu, 1, "", "effort")[2] == "Max"


def test_model_menu_ignores_plain_commands():
    items = [radio("Opus 5.5"), radio("Sonnet 5.5", True), radio("Haiku 5.5"), MenuItem("More models")]
    assert choose(items, 1, "", "model")[2] == "Haiku 5.5"
    assert choose(items, -1, "", "model")[2] == "Opus 5.5"


def test_current_from_the_model_button_when_nothing_is_checked():
    items = [MenuItem("Opus 5.5 Most capable"), MenuItem("Sonnet 5.5 Fast and smart")]
    assert current_index(items, "Model: Opus 5.5 Medium") == 0
    effort = [MenuItem("Low"), MenuItem("High"), MenuItem("Extra high"), MenuItem("Max")]
    assert current_index(effort, "Model: Opus 5.5 Extra high") == 2  # more words beat "High"


def test_unknown_when_it_cant_tell():
    items = [MenuItem("Alpha"), MenuItem("Beta")]
    outcome, item, label = choose(items, 1, "Model: Something else", "model")
    assert outcome == "unknown" and item is None and label == "Alpha, Beta"


def test_effort_rank():
    assert [effort_rank(n) for n in ("Low", "Medium", "High", "Extra high", "Max", "Opus")] == [0, 1, 2, 3, 5, None]
