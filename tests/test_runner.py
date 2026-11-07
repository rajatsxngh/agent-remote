import time

from ps5_agent_handler.runner import AppSwitch, JumpToAttention, MenuStep, Paste, RunAction, Runner
from ps5_agent_handler.status import SessionStatus


class FakePlatform:
    def __init__(self, click_result=None, foreground=True, statuses=(), effort=("changed", "High"), screen=("A", "done")):
        self.screen = screen
        self.next_step_result = None
        self.question_result = None
        self.other_picked = False
        self.calls = []
        self.click_result = click_result
        self.foreground = foreground
        self.statuses = list(statuses)
        self.effort = effort

    def session_statuses(self, window):
        return list(self.statuses)

    def screen_light(self, window):
        return self.screen

    def open_session(self, window, status):
        self.calls.append(("open", status.name))

    def step_menu(self, window, menu, step):
        self.calls.append((menu, step))
        return self.effort

    def question_step(self, window, op):
        self.calls.append(("question", op))
        return self.question_result

    def focus_question_other(self, window):
        return False

    def question_other_picked(self, window):
        return self.other_picked

    def prompt_text(self, window):
        return "a draft I was writing"

    def prompt_caret_to_end(self, window):
        self.calls.append("caret_to_end")

    def move_dictation_to_other(self, window, before):
        self.calls.append(("move_to_other", before))
        return "moved", "use the staging database"

    def answer_next_step(self, window, answer):
        self.calls.append(("next_step", answer))
        return self.next_step_result

    def step_effort(self, window, step):
        self.calls.append(("effort", step))
        return self.effort

    def thread_init(self):
        self.calls.append("init")

    def prepare(self):
        self.calls.append("prepare")

    def idle(self):
        pass

    def find_claude_window(self):
        return 42

    def bring_to_front(self, window):
        self.calls.append("front")
        return self.foreground

    def is_foreground(self, window):
        return self.foreground

    def send_chord(self, chord):
        self.calls.append(("chord", str(chord)))

    def chord_down(self, chord):
        self.calls.append(("down", str(chord)))

    def chord_up(self, chord):
        self.calls.append(("up", str(chord)))

    def release_all(self):
        self.calls.append("release_all")

    def click_button(self, window, labels):
        self.calls.append(("click", labels))
        return self.click_result

    def focus_prompt(self, window):
        self.calls.append("focus_prompt")
        return True

    def paste_text(self, window, text, send):
        self.calls.append(("paste", text, send))
        return send

    def set_clipboard(self, text):
        self.calls.append(("clipboard", text))

    def scroll_chat(self, window, notches):
        self.calls.append(("scroll", notches))
        return True


class FakeFeedback:
    def __init__(self):
        self.events = []
        self.agent_states = []

    def request(self, event):
        self.events.append(event)

    def agents(self, state):
        self.agent_states.append(state)


def run_jobs(config, platform, *jobs, wait=0.3, dry_run=False):
    feedback = FakeFeedback()
    runner = Runner(platform, config, feedback, dry_run=dry_run)
    runner.start()
    for job in jobs:
        runner.submit(job)
    time.sleep(wait)
    runner.stop()
    return feedback


def actions_only(calls):
    return [c for c in calls if c not in ("init", "prepare", "release_all")]


def test_keys_bring_claude_forward_then_press(config):
    platform = FakePlatform()
    run_jobs(config, platform, RunAction(config.bindings["cross"]))
    assert actions_only(platform.calls) == ["front", ("chord", "enter")]


def test_click_says_so_when_no_button_is_found(config):
    platform = FakePlatform(click_result=None)
    feedback = run_jobs(config, platform, RunAction(config.bindings["triangle"]))
    assert ("click", ("Allow once", "Allow")) in platform.calls
    assert feedback.events == ["nothing"]


def test_triangle_answers_the_next_step_box_when_there_is_no_prompt():
    from ps5_agent_handler.config import parse_config

    config = parse_config(
        {
            "buttons": {"triangle": "approve"},
            "actions": {"approve": {"click": ["Allow once"], "next_step": "yes"}},
        }
    )
    platform = FakePlatform(click_result=None)
    platform.next_step_result = "Yes, set it up"
    feedback = run_jobs(config, platform, RunAction(config.bindings["triangle"]))
    assert ("next_step", "yes") in platform.calls
    assert "nothing" not in feedback.events


def test_a_permission_prompt_still_comes_first(config):
    from ps5_agent_handler.config import parse_config

    config = parse_config({"buttons": {"square": {"click": ["Deny"], "next_step": "no"}}})
    platform = FakePlatform(click_result="Deny")
    run_jobs(config, platform, RunAction(config.bindings["square"]))
    assert ("click", ("Deny",)) in platform.calls
    assert not [c for c in platform.calls if c[0] == "next_step"]


def _dpad_down():
    from ps5_agent_handler.config import parse_config

    return parse_config(
        {"buttons": {"dpad_down": {"keys": "ctrl+shift+]", "question": "option_next"}}}
    )


def test_with_a_question_card_open_the_dpad_moves_between_answers():
    config = _dpad_down()
    platform = FakePlatform()
    platform.question_result = "Option: 'Quick task'."
    run_jobs(config, platform, RunAction(config.bindings["dpad_down"]))
    assert ("question", "option_next") in platform.calls
    assert ("chord", "ctrl+shift+]") not in platform.calls  # no session switch


def test_without_a_question_card_the_dpad_switches_sessions():
    config = _dpad_down()
    platform = FakePlatform()  # question_step returns None: no card open
    run_jobs(config, platform, RunAction(config.bindings["dpad_down"]))
    assert ("chord", "ctrl+shift+]") in platform.calls


def _circle():
    from ps5_agent_handler.config import parse_config

    return parse_config(
        {"buttons": {"circle": {"click": ["Stop dictation", "Press and hold to record"], "focus_prompt": True}}}
    )


class MicPlatform(FakePlatform):
    """Claude's mic: the first press starts dictation, the second stops it."""

    def click_button(self, window, labels):
        self.calls.append(("click", labels))
        self.recording = not getattr(self, "recording", False)
        return "Press and hold to record" if self.recording else "Stop dictation"


def test_dictation_started_with_other_picked_lands_in_the_other_box():
    config = _circle()
    platform = MicPlatform()
    platform.other_picked = True
    circle = RunAction(config.bindings["circle"])
    run_jobs(config, platform, circle, circle)
    # The cursor went to the end first, and the draft is passed along so only new words move.
    assert "caret_to_end" in platform.calls
    assert ("move_to_other", "a draft I was writing") in platform.calls


def test_normal_dictation_is_left_alone():
    config = _circle()
    platform = MicPlatform()  # no question card / Other not picked
    circle = RunAction(config.bindings["circle"])
    run_jobs(config, platform, circle, circle)
    assert not [c for c in platform.calls if c[0] == "move_to_other"]


def test_app_switch_holds_alt_then_lets_go(config):
    platform = FakePlatform()
    run_jobs(config, platform, AppSwitch(1), AppSwitch(1), wait=0.6)
    assert actions_only(platform.calls) == [
        ("down", "alt"),
        ("chord", "tab"),
        ("chord", "tab"),
        ("up", "alt"),
    ]


def test_other_actions_let_go_of_alt_first(config):
    platform = FakePlatform()
    run_jobs(config, platform, AppSwitch(-1), RunAction(config.bindings["cross"]))
    assert actions_only(platform.calls) == [
        ("down", "alt"),
        ("chord", "shift+tab"),
        ("up", "alt"),
        "front",
        ("chord", "enter"),
    ]


def test_focus_prompt_comes_before_the_click(config):
    from ps5_agent_handler.config import parse_config

    dictate = parse_config(
        {"buttons": {"circle": {"click": ["Stop dictation", "Dictate"], "focus_prompt": True}}}
    ).bindings["circle"]
    platform = FakePlatform(click_result="Dictate")
    run_jobs(config, platform, RunAction(dictate))
    assert actions_only(platform.calls) == [
        "front",
        "focus_prompt",
        ("click", ("Stop dictation", "Dictate")),
    ]


def test_paste_goes_into_the_prompt_box(config):
    platform = FakePlatform()
    run_jobs(config, platform, Paste("run the tests", True))
    assert actions_only(platform.calls) == ["front", "focus_prompt", ("paste", "run the tests", True)]


def test_nothing_is_pressed_if_claude_wont_come_forward(config):
    platform = FakePlatform(foreground=False)
    feedback = run_jobs(config, platform, RunAction(config.bindings["cross"]))
    assert ("chord", "enter") not in platform.calls
    assert feedback.events == ["error"]


def test_jump_opens_the_session_that_needs_you(config):
    statuses = [SessionStatus("Busy", "running"), SessionStatus("Waiting on approval", "attention")]
    platform = FakePlatform(statuses=statuses, screen=("Busy", "working"))
    feedback = run_jobs(config, platform, JumpToAttention())
    assert ("open", "Waiting on approval") in platform.calls
    assert feedback.agent_states[0] == "working"  # the light follows the session on screen


def test_jump_with_nothing_waiting(config):
    platform = FakePlatform(statuses=[SessionStatus("Busy", "running")])
    feedback = run_jobs(config, platform, JumpToAttention())
    assert not [c for c in platform.calls if c[0] == "open"]
    assert "nothing" in feedback.events


def test_effort_steps(config):
    platform = FakePlatform(effort=("same", "Max"))
    feedback = run_jobs(config, platform, MenuStep("effort", 1))
    assert ("effort", 1) in platform.calls
    assert "nothing" in feedback.events  # already at the top


def test_effort_waits_for_an_empty_prompt_box(config):
    platform = FakePlatform(effort=("busy", ""))
    feedback = run_jobs(config, platform, MenuStep("effort", -1))
    assert "nothing" in feedback.events and "tick" not in feedback.events


def test_model_steps(config):
    platform = FakePlatform(effort=("changed", "Sonnet 5.5"))
    feedback = run_jobs(config, platform, MenuStep("model", -1))
    assert ("model", -1) in platform.calls
    assert "tick" in feedback.events


def test_dry_run_touches_nothing(config):
    platform = FakePlatform()
    run_jobs(
        config,
        platform,
        RunAction(config.bindings["cross"]),
        AppSwitch(1),
        Paste("hi", True),
        dry_run=True,
    )
    assert platform.calls == []
