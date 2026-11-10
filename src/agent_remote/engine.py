"""Turns controller events into actions, following config.toml."""

from __future__ import annotations

import logging

from .config import Action, Config
from .runner import AppSwitch, JumpToAttention, MenuStep, ReleaseAll, RunAction, Scroll

log = logging.getLogger(__name__)

REPEAT_DELAY = 0.40  # seconds before a held button starts repeating
REPEAT_EVERY = 0.12
SWIPE_PER_NOTCH = 0.06  # touchpad heights per scroll step
STICK_NOTCHES_PER_SEC = 14.0  # scroll steps per second with the stick all the way over
SCROLL_FLUSH_EVERY = 0.05
BUTTON_SCROLL_NOTCHES = 3
_SCROLL_BUTTONS = {"scroll_up": -BUTTON_SCROLL_NOTCHES, "scroll_down": BUTTON_SCROLL_NOTCHES}
_MENU_STEPS = {
    "effort_down": MenuStep("effort", -1),
    "effort_up": MenuStep("effort", 1),
    "model_previous": MenuStep("model", -1),
    "model_next": MenuStep("model", 1),
}


class Engine:
    def __init__(self, config: Config, runner, voice, feedback) -> None:
        self.config = config
        self.runner = runner
        self.voice = voice
        self.feedback = feedback
        self.paused = False
        self._repeat: dict[str, list] = {}
        self._talk_button: str | None = None
        self._toggle_on = False
        self._scroll_carry = 0.0
        self._last_flush = 0.0
        self._stick_y = 0.0
        self._last_tick: float | None = None

    def set_config(self, config: Config) -> None:
        self.config = config
        self._repeat.clear()

    def handle(self, event: tuple, now: float) -> None:
        kind = event[0]
        if kind == "button":
            self.on_button(event[1], event[2], now)
        elif kind == "swipe":
            self.on_swipe(event[1])
        elif kind == "stick":
            self._stick_y = event[1]
        elif kind == "connected":
            self.feedback.request("connected")
        elif kind == "disconnected":
            self.on_disconnected()

    def on_button(self, name: str, pressed: bool, now: float) -> None:
        if not pressed:
            self._repeat.pop(name, None)
        action = self.config.bindings.get(name)
        if pressed and self.config.settings.log_buttons:
            log.info("%-10s -> %s", name, action.describe() if action else "(not mapped)")
        if action is None:
            return
        builtin = action.name if action.kind == "builtin" else None
        if builtin == "pause":
            if pressed:
                self.toggle_pause()
            return
        if builtin == "toggle_talk":
            if pressed and not self.paused:
                if self._toggle_on:
                    self._toggle_on = False
                    self.voice.stop()
                elif self._talk_button is None:
                    self._toggle_on = True
                    self.voice.start()
            return
        if builtin == "push_to_talk":
            if pressed and self._toggle_on:
                return  # already talking with the toggle button
            if pressed and not self.paused:
                self._talk_button = name
                self.voice.start()
            elif not pressed and self._talk_button == name:
                self._talk_button = None
                self.voice.stop()
            return
        if not pressed or self.paused:
            return
        job = self._job_for(action)
        if job is None:
            return
        self.runner.submit(job)
        if action.repeat or builtin in _SCROLL_BUTTONS:
            self._repeat[name] = [now + REPEAT_DELAY, job]

    def _job_for(self, action: Action):
        if action.kind != "builtin":
            return RunAction(action)
        if action.name == "previous_app":
            return AppSwitch(-1)
        if action.name == "next_app":
            return AppSwitch(1)
        if action.name in _SCROLL_BUTTONS:
            return Scroll(_SCROLL_BUTTONS[action.name])
        if action.name == "jump_to_attention":
            return JumpToAttention()
        return _MENU_STEPS.get(action.name)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self._stop_talking()
            self._repeat.clear()
            self._scroll_carry = 0.0
            self.runner.submit(ReleaseAll())
            log.info("Paused. Press PS again to resume.")
            self.feedback.request("paused")
        else:
            log.info("Resumed.")
            self.feedback.request("resumed")

    def on_swipe(self, dy: float) -> None:
        if self.paused or not self._scrolls("touchpad_swipe"):
            return
        # The page follows your finger: swipe up to see newer messages.
        notches = -dy / SWIPE_PER_NOTCH
        self._scroll_carry += notches * self._direction() * self.config.settings.scroll_speed

    def _stop_talking(self) -> None:
        if self._talk_button is not None or self._toggle_on:
            self._talk_button = None
            self._toggle_on = False
            self.voice.cancel()

    def on_disconnected(self) -> None:
        self._stop_talking()
        self._repeat.clear()
        self._stick_y = 0.0
        self._scroll_carry = 0.0
        self.runner.submit(ReleaseAll())

    def tick(self, now: float) -> None:
        dt = 0.0 if self._last_tick is None else min(0.1, now - self._last_tick)
        self._last_tick = now
        for entry in self._repeat.values():
            if now >= entry[0]:
                entry[0] = now + REPEAT_EVERY
                self.runner.submit(entry[1])
        settings = self.config.settings
        if not self.paused and self._scrolls("right_stick"):
            tilt = abs(self._stick_y)
            if tilt > settings.stick_deadzone:
                strength = (tilt - settings.stick_deadzone) / (1.0 - settings.stick_deadzone)
                sign = 1.0 if self._stick_y > 0 else -1.0
                self._scroll_carry += (
                    sign * strength * STICK_NOTCHES_PER_SEC * dt
                    * settings.scroll_speed * self._direction()
                )
        if now - self._last_flush >= SCROLL_FLUSH_EVERY:
            self._last_flush = now
            whole = int(self._scroll_carry)
            if whole:
                self._scroll_carry -= whole
                self.runner.submit(Scroll(whole))

    def _scrolls(self, gesture: str) -> bool:
        action = self.config.bindings.get(gesture)
        return action is not None and action.kind == "builtin" and action.name == "scroll"

    def _direction(self) -> float:
        return -1.0 if self.config.settings.invert_scroll else 1.0
