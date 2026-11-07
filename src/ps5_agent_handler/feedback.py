"""Light bar colors and rumbles, so you can tell what's happening without looking.

The light bar shows, in order of importance: paused, then your voice (listening /
transcribing), then Claude: red = needs you, yellow = working, blue = done.
"""

from __future__ import annotations

import math
import queue
import time

COLORS = {
    "attention": (255, 0, 0),  # red, pulsing: the session needs you
    "working": (255, 150, 0),  # yellow: the session is working
    "done": (0, 60, 255),  # blue: the session finished its task
    "idle": (60, 60, 60),  # dim white: no session is open
    "listening": (255, 0, 140),  # pink: Whisper is recording your voice
    "thinking": (140, 0, 255),  # purple: Whisper is turning speech into text
    "paused": (12, 12, 12),  # nearly off: the controller is paused
    "off": (0, 0, 64),  # dim blue, close to the controller's own default
}
AGENT_STATES = ("idle", "working", "attention", "done")
PULSE_PERIOD_S = 1.4
PULSE_UPDATE_S = 0.06

# event: rumble as (low motor, high motor, milliseconds)
RUMBLES = {
    "connected": (0, 18000, 120),
    "paused": (26000, 0, 160),
    "resumed": (0, 22000, 90),
    "listening": (0, 22000, 70),
    "voice_done": (0, 14000, 50),
    "voice_empty": (30000, 0, 160),
    "nothing": (16000, 0, 80),
    "error": (40000, 40000, 220),
    "tick": (0, 12000, 40),
    "alert_attention": (45000, 20000, 300),
    "alert_done": (0, 30000, 150),
    "alert_unread": (0, 30000, 150),
    "alert_error": (50000, 0, 400),
}
_VOICE_ON = {"listening": "listening", "transcribing": "thinking"}
_VOICE_OFF = {"voice_done", "voice_empty", "error"}


class Feedback:
    """Any thread may call request() / agents(); the controller thread calls drain() to apply them."""

    def __init__(self, controller, settings) -> None:
        self._controller = controller
        self._settings = settings
        self._queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._paused = False
        self._voice: str | None = None
        self._agents = "idle"
        self._shown: tuple[int, int, int] | None = None
        self._last_led = 0.0

    def set_settings(self, settings) -> None:
        self._settings = settings
        self._shown = None

    def request(self, event: str) -> None:
        self._queue.put(event)

    def agents(self, state: str) -> None:
        self._queue.put("agents:" + state)

    @property
    def color_name(self) -> str:
        if self._paused:
            return "paused"
        return self._voice or self._agents

    def drain(self, now: float | None = None) -> None:
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            self._apply(event)
        self._render(time.monotonic() if now is None else now)

    def goodbye(self) -> None:
        if self._settings.lightbar:
            self._controller.set_led(COLORS["off"])

    def _apply(self, event: str) -> None:
        if event.startswith("agents:"):
            state = event.split(":", 1)[1]
            if state in AGENT_STATES:
                self._agents = state
            return
        if event == "paused":
            self._paused = True
        elif event == "resumed":
            self._paused = False
        elif event == "connected":
            self._shown = None  # a reconnected controller forgets its color
        if event in _VOICE_ON:
            self._voice = _VOICE_ON[event]
        elif event in _VOICE_OFF:
            self._voice = None
        rumble = RUMBLES.get(event)
        if rumble and self._settings.rumble and self._wanted(event):
            self._controller.rumble(*rumble)

    def _wanted(self, event: str) -> bool:
        settings = self._settings
        if event in ("alert_attention", "alert_error"):
            return getattr(settings, "buzz_needs_you", True)
        if event in ("alert_done", "alert_unread"):
            return getattr(settings, "buzz_done", True)
        if event == "nothing":
            return getattr(settings, "buzz_nothing", True)
        return True

    def _render(self, now: float) -> None:
        if not self._settings.lightbar:
            return
        name = self.color_name
        color = COLORS[name]
        if name == "attention":
            if now - self._last_led < PULSE_UPDATE_S:
                return
            strength = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * now / PULSE_PERIOD_S))
            color = tuple(int(c * strength) for c in color)
        if color != self._shown:
            self._controller.set_led(color)
            self._shown = color
            self._last_led = now
