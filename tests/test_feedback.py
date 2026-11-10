from agent_remote.config import FeedbackSettings
from agent_remote.feedback import COLORS, Feedback


class FakePad:
    def __init__(self):
        self.leds = []
        self.rumbles = []

    def set_led(self, rgb):
        self.leds.append(tuple(rgb))

    def rumble(self, low, high, ms):
        self.rumbles.append((low, high, ms))


def make():
    pad = FakePad()
    return Feedback(pad, FeedbackSettings()), pad


def test_yellow_while_working_blue_when_done():
    feedback, pad = make()
    feedback.drain(0.0)
    assert pad.leds[-1] == COLORS["idle"]
    feedback.agents("working")
    feedback.drain(0.1)
    assert pad.leds[-1] == COLORS["working"] == (255, 150, 0)
    feedback.agents("done")
    feedback.drain(0.2)
    assert pad.leds[-1] == COLORS["done"] == (0, 60, 255)


def test_voice_and_pause_take_priority():
    feedback, pad = make()
    feedback.agents("working")
    feedback.request("listening")
    feedback.drain(0.0)
    assert pad.leds[-1] == COLORS["listening"]
    feedback.request("voice_done")
    feedback.drain(0.1)
    assert pad.leds[-1] == COLORS["working"]
    feedback.request("paused")
    feedback.drain(0.2)
    assert pad.leds[-1] == COLORS["paused"]
    feedback.request("resumed")
    feedback.drain(0.3)
    assert pad.leds[-1] == COLORS["working"]


def test_needs_you_is_pulsing_red_and_buzzes():
    feedback, pad = make()
    feedback.agents("attention")
    feedback.request("alert_attention")
    for step in range(20):
        feedback.drain(step * 0.1)
    reds = [led for led in pad.leds if led[1] == 0 and led[2] == 0 and led[0] > 0]
    assert len(set(reds)) > 3  # red, with the brightness changing
    assert pad.rumbles


def test_only_the_buzzes_you_kept():
    pad = FakePad()
    feedback = Feedback(pad, FeedbackSettings(buzz_needs_you=True, buzz_done=False, buzz_nothing=False))
    for event in ("alert_done", "nothing", "alert_attention"):
        feedback.request(event)
    feedback.drain(0.0)
    assert len(pad.rumbles) == 1  # only "needs you"


def test_light_bar_can_be_turned_off():
    pad = FakePad()
    feedback = Feedback(pad, FeedbackSettings(lightbar=False, rumble=False))
    feedback.agents("attention")
    feedback.request("alert_attention")
    feedback.drain(0.0)
    assert pad.leds == [] and pad.rumbles == []
