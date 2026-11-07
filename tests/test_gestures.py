import pytest

from ps5_agent_handler.gestures import StickFlick, SwipeTracker, TriggerButton


def test_a_flick_presses_once_and_releases_back_in_the_middle():
    stick = StickFlick("lstick")
    assert stick.update(y=-0.3) == []  # a nudge does nothing
    assert stick.update(y=-0.8) == [("lstick_up", True)]
    assert stick.update(y=-1.0) == []  # holding it there doesn't press again
    assert stick.update(y=-0.5) == []  # still past the release point
    assert stick.update(y=-0.1) == [("lstick_up", False)]
    assert stick.update(y=0.9) == [("lstick_down", True)]


def test_a_diagonal_only_counts_the_stronger_direction():
    stick = StickFlick("lstick")
    assert stick.update(x=0.7, y=0.7) == []  # dead even: neither
    assert stick.update(x=0.3, y=0.9) == [("lstick_down", True)]
    assert stick.update(x=0.9, y=0.2) == [("lstick_down", False), ("lstick_right", True)]


def test_trigger_has_a_release_gap():
    trigger = TriggerButton()
    assert trigger.update(0.49, 0.5) is None
    assert trigger.update(0.60, 0.5) is True
    assert trigger.update(0.45, 0.5) is None  # still inside the gap
    assert trigger.update(0.30, 0.5) is False


def test_small_wobble_is_not_a_swipe():
    swipe = SwipeTracker()
    swipe.down(0.5)
    assert swipe.move(0.52) == 0.0
    assert swipe.move(0.47) == 0.0


def test_swipe_reports_movement_once_it_starts():
    swipe = SwipeTracker()
    swipe.down(0.5)
    assert swipe.move(0.45) == pytest.approx(-0.05)
    assert swipe.move(0.40) == pytest.approx(-0.05)
    swipe.up()
    assert swipe.move(0.20) == 0.0
