from ps5_agent_handler.status import (
    ScreenTracker,
    SessionStatus,
    StatusTracker,
    light_for_all,
    light_for_screen,
    most_urgent,
    pick_jump_target,
)


def test_light_for_the_session_on_screen():
    assert light_for_screen([], approval_showing=False, stop_showing=False) == "done"
    assert light_for_screen(["running"], approval_showing=False, stop_showing=False) == "working"
    assert light_for_screen([], approval_showing=False, stop_showing=True) == "working"
    assert light_for_screen(["running"], approval_showing=True, stop_showing=True) == "attention"
    assert light_for_screen(["attention"], approval_showing=False, stop_showing=False) == "attention"
    assert light_for_screen(["error"], approval_showing=False, stop_showing=False) == "attention"


def test_a_next_step_question_turns_it_red_once_claude_has_stopped():
    assert light_for_screen([], approval_showing=False, stop_showing=False, question_showing=True) == "attention"
    # A leftover box doesn't count while Claude is working again.
    assert light_for_screen([], approval_showing=False, stop_showing=True, question_showing=True) == "working"
    assert light_for_screen(["running"], approval_showing=False, stop_showing=False, question_showing=True) == "working"


def test_light_for_all_sessions():
    assert light_for_all([]) == "done"
    assert light_for_all([s("A", "unread")]) == "done"
    assert light_for_all([s("A", "unread"), s("B", "running")]) == "working"
    assert light_for_all([s("A", "attention"), s("B", "running")]) == "attention"


def test_screen_tracker_buzzes_on_changes_but_not_on_switching_sessions():
    tracker = ScreenTracker()
    assert tracker.update("A", "working") == []
    assert tracker.update("A", "attention") == ["attention"]
    assert tracker.update("A", "working") == []
    assert tracker.update("A", "done") == ["done"]
    assert tracker.update("B", "attention") == []  # you switched to B; it was already waiting
    assert tracker.update(None, "idle") == []


def s(name, state):
    return SessionStatus(name, state)


def test_most_urgent_wins():
    assert most_urgent(["running", "unread"]) == "unread"
    assert most_urgent(["running", "attention", "error"]) == "attention"
    assert most_urgent([]) == "idle"


def test_first_reading_is_only_a_baseline():
    tracker = StatusTracker()
    summary, alerts = tracker.update([s("A", "attention"), s("B", "running")])
    assert summary == "attention" and alerts == []


def test_alerts_only_when_a_session_changes():
    tracker = StatusTracker()
    tracker.update([s("A", "running"), s("B", "running")])
    summary, alerts = tracker.update([s("A", "attention"), s("B", "running")])
    assert summary == "attention" and alerts == [("attention", "A")]
    _, alerts = tracker.update([s("A", "attention"), s("B", "running")])
    assert alerts == []  # no repeat while nothing changes
    summary, alerts = tracker.update([s("B", "unread")])
    assert summary == "unread" and alerts == [("unread", "B")]
    summary, alerts = tracker.update([])
    assert summary == "idle" and alerts == []


def test_started_by_line_is_dropped_from_titles():
    status = s("Build M5: scenario generator v1 Started by Claude from Document vision", "running")
    assert status.title == "Build M5: scenario generator v1"


def test_status_words_are_dropped_so_a_session_keeps_its_title():
    assert s("Document vision and purpose Running", "running").title == "Document vision and purpose"
    assert s("Document vision and purpose Needs attention", "attention").title == "Document vision and purpose"
    assert s("Fix login Awaiting input", "attention").title == "Fix login"
    assert s("Fix login Unread response", "unread").title == "Fix login"
    tracker = StatusTracker()
    tracker.update([s("Plan Running", "running")])
    summary, alerts = tracker.update([s("Plan Needs attention", "attention")])
    assert alerts == [("attention", "Plan")]


def test_jump_goes_to_the_most_urgent_then_cycles():
    statuses = [s("Done", "unread"), s("Waiting", "attention"), s("Busy", "running"), s("Also waiting", "attention")]
    first = pick_jump_target(statuses, None)
    assert first.name == "Waiting"
    second = pick_jump_target(statuses, first.title)
    assert second.name == "Also waiting"
    third = pick_jump_target(statuses, second.title)
    assert third.name == "Done"
    assert pick_jump_target(statuses, third.title).name == "Waiting"


def test_nothing_to_jump_to():
    assert pick_jump_target([s("Busy", "running")], None) is None
