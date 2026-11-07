from ps5_agent_handler.matching import FoundButton, label_matches, pick_button


def button(name, top=0, offscreen=False, enabled=True, left=600):
    return FoundButton(name, left, top, left + 100, top + 40, offscreen, enabled)


def test_labels_match_whole_words_only():
    assert label_matches("Allow once", "allow once")
    assert label_matches("Allow  once  Enter", "Allow once")
    assert label_matches("Deny, and tell Claude what to do", "Deny")
    assert not label_matches("Notifications", "No")
    assert not label_matches("Allowance report", "Allow")


def test_first_label_wins_then_the_lowest_visible_button():
    buttons = [
        button("Allow", top=100),
        button("Allow once", top=50),
        button("Allow once", top=900, offscreen=True),
        button("Allow once", top=300),
    ]
    assert pick_button(buttons, ["Allow once", "Allow"]).top == 300


def test_sidebar_and_disabled_buttons_are_skipped():
    sidebar = [(0, 0, 576, 1344)]
    buttons = [button("Deny", top=500, left=10), button("Deny", top=200, enabled=False)]
    assert pick_button(buttons, ["Deny"], sidebar) is None


def test_offscreen_is_used_when_nothing_is_visible():
    assert pick_button([button("Deny", offscreen=True)], ["Deny"]).name == "Deny"
