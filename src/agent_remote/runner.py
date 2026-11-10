"""Runs actions one at a time on their own thread, so reading the controller never waits."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

from .config import Action, Config
from .keys import Chord
from .menus import MENU_LABELS
from .status import WORDS, ScreenTracker, StatusTracker, light_for_all, pick_jump_target

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunAction:
    action: Action


@dataclass(frozen=True)
class AppSwitch:
    step: int  # +1 forward (Alt+Tab), -1 back (Alt+Shift+Tab)


@dataclass(frozen=True)
class Scroll:
    notches: int  # positive scrolls down, toward the newest messages


@dataclass(frozen=True)
class Paste:
    text: str
    send: bool


@dataclass(frozen=True)
class Hold:
    chords: tuple[Chord, ...]
    down: bool


@dataclass(frozen=True)
class ReleaseAll:
    pass


@dataclass(frozen=True)
class JumpToAttention:
    pass


@dataclass(frozen=True)
class MenuStep:
    menu: str  # "effort" or "model"
    step: int  # +1 the next item down the menu (more effort), -1 the one above


_STOP = object()
_ALT = Chord((), "alt")
STATUS_EVERY_S = 1.0  # how often Claude's window is read for the light bar


def describe(job) -> str:
    if isinstance(job, RunAction):
        return f"{job.action.name} ({job.action.describe()})"
    if isinstance(job, AppSwitch):
        return "switch app " + ("forward" if job.step > 0 else "back")
    if isinstance(job, Scroll):
        return f"scroll {'down' if job.notches > 0 else 'up'} {abs(job.notches)}"
    if isinstance(job, Paste):
        return f"paste {job.text!r}" + (" and send it" if job.send else "")
    if isinstance(job, Hold):
        keys = " then ".join(str(chord) for chord in job.chords)
        return ("hold " if job.down else "let go of ") + keys
    if isinstance(job, JumpToAttention):
        return "open the session that needs you"
    if isinstance(job, MenuStep):
        if job.menu == "effort":
            return "turn effort " + ("up" if job.step > 0 else "down")
        return ("next" if job.step > 0 else "previous") + " " + job.menu
    return type(job).__name__


class Runner(threading.Thread):
    def __init__(self, platform, config: Config, feedback, dry_run: bool = False) -> None:
        super().__init__(name="actions", daemon=True)
        self.platform = platform
        self.config = config
        self.feedback = feedback
        self.dry_run = dry_run
        self._queue: queue.Queue = queue.Queue()
        self._alt_held = False
        self._switch_deadline: float | None = None
        self._held: list[Chord] = []
        self._tracker = StatusTracker()
        self._screen = ScreenTracker()
        self._other_dictation: str | None = None  # prompt box text when dictation for "Other" began
        self._next_status = 0.0
        self._last_jump: str | None = None

    def set_config(self, config: Config) -> None:
        self.config = config

    def submit(self, job) -> None:
        self._queue.put(job)

    def stop(self, timeout: float = 3.0) -> None:
        self._queue.put(_STOP)
        self.join(timeout)

    def run(self) -> None:
        if not self.dry_run:
            try:
                self.platform.thread_init()
                self.platform.prepare()
            except Exception:  # keys can still work without UI Automation
                log.exception("Couldn't fully set up; on-screen buttons may not work")
        while True:
            wait = 0.25
            if self._switch_deadline is not None:
                wait = max(0.0, min(wait, self._switch_deadline - time.monotonic()))
            try:
                job = self._queue.get(timeout=wait)
            except queue.Empty:
                job = None
            if self._switch_deadline is not None and time.monotonic() >= self._switch_deadline:
                self._finish_switch()
            if job is _STOP:
                break
            if job is not None:
                try:
                    self._do(job)
                except Exception:  # one failed action shouldn't stop the controller
                    log.exception("Action failed: %s", describe(job))
                    self.feedback.request("error")
            if not self.dry_run:
                try:
                    self.platform.idle()
                except Exception:
                    log.exception("Idle housekeeping failed")
                self._poll_status()
        self._release_everything()

    # -- session statuses (light bar, rumble) --

    def _poll_status(self) -> None:
        now = time.monotonic()
        if now < self._next_status or not self.config.feedback.agent_status:
            return
        self._next_status = now + STATUS_EVERY_S
        try:
            window = self.platform.find_claude_window()
            if not window:
                self.feedback.agents("idle")
                return
            if self.config.feedback.light_follows == "screen":
                title, light = self.platform.screen_light(window)
            else:
                statuses = self.platform.session_statuses(window)
        except Exception as exc:
            log.debug("Couldn't read session statuses: %s", exc)
            return
        if self.config.feedback.light_follows == "screen":
            self.feedback.agents(light)
            for alert in self._screen.update(title, light):
                log.info("'%s' %s.", title, "needs you" if alert == "attention" else "is done")
                self.feedback.request("alert_" + alert)
            return
        _summary, alerts = self._tracker.update(statuses)
        self.feedback.agents(light_for_all(statuses))
        for state, title in alerts:
            log.info("Session '%s' %s.", title, WORDS[state])
            self.feedback.request("alert_" + state)

    def _do(self, job) -> None:
        if self.dry_run:
            if not isinstance(job, ReleaseAll):
                log.info("[dry run] would %s", describe(job))
            return
        if isinstance(job, AppSwitch):
            self._switch(job.step)
            return
        self._finish_switch()  # anything else lets go of Alt first
        if isinstance(job, ReleaseAll):
            self._release_everything()
        elif isinstance(job, RunAction):
            self._run_action(job.action)
        elif isinstance(job, Scroll):
            self._scroll(job.notches)
        elif isinstance(job, Paste):
            self._paste(job.text, job.send)
        elif isinstance(job, Hold):
            self._hold(job.chords, job.down)
        elif isinstance(job, JumpToAttention):
            self._jump()
        elif isinstance(job, MenuStep):
            self._menu_step(job.menu, job.step)

    # -- app switching: hold Alt while L1/R1 taps move through the switcher --

    def _switch(self, step: int) -> None:
        if not self._alt_held:
            self.platform.chord_down(_ALT)
            self._alt_held = True
        self.platform.send_chord(Chord(("shift",), "tab") if step < 0 else Chord((), "tab"))
        self._switch_deadline = time.monotonic() + self.config.settings.app_switch_delay

    def _finish_switch(self) -> None:
        self._switch_deadline = None
        if self._alt_held:
            self._alt_held = False
            self.platform.chord_up(_ALT)

    # -- Claude actions --

    def _claude(self) -> int | None:
        window = self.platform.find_claude_window()
        if not window:
            log.warning("Claude desktop isn't open.")
            self.feedback.request("error")
            return None
        if self.config.settings.bring_claude_to_front:
            if not self.platform.bring_to_front(window):
                log.warning("Couldn't bring Claude to the front, so nothing was pressed.")
                self.feedback.request("error")
                return None
        elif not self.platform.is_foreground(window):
            log.info("Claude isn't in front (bring_claude_to_front = false), so nothing was pressed.")
            return None
        return window

    def _run_action(self, action: Action) -> None:
        window = self._claude()
        if window is None:
            return
        if action.question:
            done = self.platform.question_step(window, action.question)
            if done is not None:  # a question card is open, so the button works on it
                log.info(done)
                return
        claude_mic = action.kind == "click" and "Stop dictation" in action.labels
        other_mode = claude_mic and self._other_dictation is None and self.platform.question_other_picked(window)
        prompt_before = ""
        if other_mode:
            # Dictation adds where the cursor is; put it at the end so the new words
            # are easy to tell apart from anything already in the box.
            self.platform.prompt_caret_to_end(window)
            prompt_before = self.platform.prompt_text(window)
        if action.focus_prompt and not self.platform.focus_prompt(window):
            log.info("Couldn't find Claude's prompt box.")
        if action.kind == "keys":
            self._send_keys(window, action.chords)
            return
        pressed = self.platform.click_button(window, action.labels)
        if claude_mic and pressed:
            self._after_claude_mic(window, pressed, other_mode, prompt_before)
        if not pressed and action.next_step:
            pressed = self.platform.answer_next_step(window, action.next_step)
            if pressed:
                log.info("Pressed '%s' on the card Claude is waiting on.", pressed)
                return
        if pressed:
            log.info("Pressed '%s'.", pressed)
        elif action.fallback:
            self._send_keys(window, action.fallback)
        else:
            looked_for = ", ".join(f"'{label}'" for label in action.labels)
            log.info("No %s button on screen (looked for %s).", action.name, looked_for)
            self.feedback.request("nothing")

    def _after_claude_mic(self, window: int, pressed: str, other_mode: bool, prompt_before: str) -> None:
        """Claude's dictation always writes into its prompt box. When it was started
        with "Other" picked on a question card, move the words there once it stops."""
        if pressed == "Stop dictation":
            if self._other_dictation is None:
                return
            before, self._other_dictation = self._other_dictation, None
            outcome, text = self.platform.move_dictation_to_other(window, before)
            if outcome == "moved":
                log.info("Put your words in the Other box: %s", text)
            elif outcome == "changed":
                log.info("The prompt box changed in an unexpected way, so nothing was moved; your words are there.")
            elif outcome == "no_box":
                log.warning("Couldn't find the Other box, so your words are in the prompt box (and on the clipboard).")
            else:
                log.info("Didn't get any words from the dictation.")
        elif other_mode:
            self._other_dictation = prompt_before  # started for the Other box

    def _send_keys(self, window: int, chords: tuple[Chord, ...]) -> None:
        for index, chord in enumerate(chords):
            if index:
                time.sleep(0.05)
            if not self.platform.is_foreground(window):
                log.warning("Claude lost focus, so the remaining keys weren't sent.")
                return
            self.platform.send_chord(chord)

    def _scroll(self, notches: int) -> None:
        window = self._claude()
        if window is not None and not self.platform.scroll_chat(window, notches):
            log.debug("Couldn't find the conversation to scroll.")

    def _paste(self, text: str, send: bool) -> None:
        window = self._claude()
        if window is None:
            self.platform.set_clipboard(text)
            log.info("Your words are on the clipboard instead.")
            return
        if not self.platform.focus_prompt(window):
            self.platform.set_clipboard(text)
            log.warning("Couldn't find Claude's prompt box. Your words are on the clipboard to paste.")
            self.feedback.request("error")
            return
        sent = self.platform.paste_text(window, text, send)
        log.info("Sent." if sent else "Pasted into the prompt box.")

    def _jump(self) -> None:
        window = self._claude()
        if window is None:
            return
        target = pick_jump_target(self.platform.session_statuses(window), self._last_jump)
        if target is None:
            log.info("No session needs you right now.")
            self.feedback.request("nothing")
            return
        self.platform.open_session(window, target)
        self._last_jump = target.title
        log.info("Opened '%s' (%s).", target.title, WORDS[target.state])

    def _menu_step(self, menu: str, step: int) -> None:
        window = self._claude()
        if window is None:
            return
        what = MENU_LABELS.get(menu, menu)
        if menu == "effort":
            outcome, label = self.platform.step_effort(window, step)
        else:
            outcome, label = self.platform.step_menu(window, menu, step)
        if outcome == "changed":
            log.info("%s: %s", what, label)
            self.feedback.request("tick")
        elif outcome == "same":
            log.info("%s is already at %s (the end of the list).", what, label)
            self.feedback.request("nothing")
        elif outcome == "busy":
            log.info("Your prompt box has text in it. Send or clear it first, then change the effort.")
            self.feedback.request("nothing")
        elif outcome == "unknown" and menu == "effort":
            log.warning("Couldn't read the current effort (Claude's model button says '%s').", label)
            self.feedback.request("error")
        elif outcome == "unknown":
            log.warning(
                "Couldn't tell which %s is picked (%s). The menu is open: pick one with the D-pad and Cross.",
                what.lower(),
                label,
            )
        else:
            log.warning("Couldn't change the %s. Is a Code tab session showing?", what.lower())
            self.feedback.request("error")

    def _hold(self, chords: tuple[Chord, ...], down: bool) -> None:
        if not down:
            while self._held:
                self.platform.chord_up(self._held.pop())
            return
        window = self._claude()
        if window is None:
            return
        self.platform.focus_prompt(window)
        for chord in chords:
            self.platform.chord_down(chord)
            self._held.append(chord)

    def _release_everything(self) -> None:
        if self.dry_run:
            return
        try:
            self._finish_switch()
            while self._held:
                self.platform.chord_up(self._held.pop())
            self.platform.release_all()
        except Exception:
            log.exception("Couldn't release held keys")
