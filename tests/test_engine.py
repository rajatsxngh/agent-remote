from ps5_agent_handler.engine import REPEAT_DELAY, REPEAT_EVERY, Engine
from ps5_agent_handler.runner import AppSwitch, JumpToAttention, MenuStep, ReleaseAll, RunAction, Scroll


class FakeRunner:
    def __init__(self):
        self.jobs = []

    def submit(self, job):
        self.jobs.append(job)


class FakeVoice:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def cancel(self):
        self.calls.append("cancel")


class FakeFeedback:
    def __init__(self):
        self.events = []

    def request(self, event):
        self.events.append(event)


def make(config):
    runner, voice, feedback = FakeRunner(), FakeVoice(), FakeFeedback()
    return Engine(config, runner, voice, feedback), runner, voice, feedback


def press(engine, name, now=0.0):
    engine.on_button(name, True, now)
    engine.on_button(name, False, now + 0.05)


def test_cross_sends_enter(config):
    engine, runner, _, _ = make(config)
    press(engine, "cross")
    assert len(runner.jobs) == 1
    assert isinstance(runner.jobs[0], RunAction)
    assert str(runner.jobs[0].action.chords[0]) == "enter"


def test_unmapped_button_does_nothing(config):
    engine, runner, _, _ = make(config)
    press(engine, "mute")
    assert runner.jobs == []


def test_toggle_talk_starts_then_stops(config):
    engine, runner, voice, _ = make(config)
    press(engine, "square", 0.0)
    assert voice.calls == ["start"]
    press(engine, "square", 2.0)
    assert voice.calls == ["start", "stop"]
    assert runner.jobs == []


def test_toggle_talk_is_cancelled_by_pause(config):
    engine, _, voice, _ = make(config)
    press(engine, "square", 0.0)
    press(engine, "ps", 1.0)
    press(engine, "square", 2.0)  # paused: ignored
    assert voice.calls == ["start", "cancel"]


def test_pause_blocks_everything_but_ps(config):
    engine, runner, voice, feedback = make(config)
    press(engine, "ps")
    assert engine.paused and feedback.events[-1] == "paused"
    assert isinstance(runner.jobs[-1], ReleaseAll)
    runner.jobs.clear()
    press(engine, "cross")
    press(engine, "r2")
    assert runner.jobs == [] and voice.calls == []
    press(engine, "ps")
    assert not engine.paused and feedback.events[-1] == "resumed"
    press(engine, "cross")
    assert len(runner.jobs) == 1


def test_r2_is_push_to_talk(config):
    engine, _, voice, _ = make(config)
    engine.on_button("r2", True, 0.0)
    engine.on_button("r2", False, 1.0)
    assert voice.calls == ["start", "stop"]


def test_pausing_while_talking_cancels(config):
    engine, _, voice, _ = make(config)
    engine.on_button("r2", True, 0.0)
    press(engine, "ps", 0.5)
    engine.on_button("r2", False, 1.0)
    assert voice.calls == ["start", "cancel"]


def test_jump_and_effort_buttons(config):
    engine, runner, _, _ = make(config)
    press(engine, "create")
    press(engine, "l3")
    press(engine, "r3")
    press(engine, "options")
    assert runner.jobs == [JumpToAttention(), MenuStep("effort", -1), MenuStep("effort", 1), MenuStep("model", 1)]


def test_shoulders_switch_apps(config):
    engine, runner, _, _ = make(config)
    press(engine, "r1")
    press(engine, "l1")
    assert runner.jobs == [AppSwitch(1), AppSwitch(-1)]


def test_held_button_repeats_until_released(config):
    engine, runner, _, _ = make(config)
    engine.on_button("dpad_right", True, 0.0)
    engine.tick(REPEAT_DELAY - 0.01)
    assert len(runner.jobs) == 1
    engine.tick(REPEAT_DELAY + 0.01)
    assert len(runner.jobs) == 2
    engine.tick(REPEAT_DELAY + 0.01 + REPEAT_EVERY + 0.001)
    assert len(runner.jobs) == 3
    engine.on_button("dpad_right", False, 1.0)
    engine.tick(5.0)
    assert len(runner.jobs) == 3


def test_scroll_button_repeats_too(config):
    engine, runner, _, _ = make(config)
    engine.on_button("l2", True, 0.0)
    engine.tick(REPEAT_DELAY + 0.01)
    assert runner.jobs == [Scroll(3), Scroll(3)]


def test_swipe_up_scrolls_toward_newer_messages(config):
    engine, runner, _, _ = make(config)
    engine.on_swipe(-0.13)  # finger moved up a little over two steps
    engine.tick(1.0)
    assert runner.jobs == [Scroll(2)]


def test_stick_scrolls_while_held(config):
    engine, runner, _, _ = make(config)
    engine.handle(("stick", 1.0), 0.0)
    now = 0.0
    engine.tick(now)
    for _ in range(20):
        now += 0.05
        engine.tick(now)
    total = sum(job.notches for job in runner.jobs if isinstance(job, Scroll))
    assert 12 <= total <= 15  # about 14 steps per second at full tilt


def test_disconnect_cancels_voice_and_lets_go_of_keys(config):
    engine, runner, voice, _ = make(config)
    engine.on_button("r2", True, 0.0)
    engine.handle(("disconnected", "pad"), 0.5)
    assert voice.calls == ["start", "cancel"]
    assert isinstance(runner.jobs[-1], ReleaseAll)
