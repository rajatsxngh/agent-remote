"""Keep track of what each Claude session is doing, as shown in the sidebar."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Most urgent first. "unread" means a session finished while you weren't looking.
PRIORITY = ("attention", "error", "unread", "running")
JUMP_ORDER = ("attention", "error", "unread")  # what the jump button takes you to
WORDS = {"attention": "needs you", "error": "hit an error", "unread": "finished", "running": "running"}
# Some rows' names end with the status itself ("My task Running").
STATUS_SUFFIXES = (
    " Awaiting input",
    " Awaiting answer",
    " Using the browser",
    " Unread response",
    " Needs attention",
    " Running",
    " Unread",
    " Error",
)


@dataclass(frozen=True)
class SessionStatus:
    name: str
    state: str  # one of PRIORITY
    ref: Any = field(default=None, compare=False)  # the platform's handle for opening it

    @property
    def title(self) -> str:
        """The session's title, without the "Started by Claude from ..." line or a status word."""
        title = self.name.split(" Started by ")[0].strip()
        for suffix in STATUS_SUFFIXES:
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
        return title or self.name


def most_urgent(states) -> str:
    states = set(states)
    order = PRIORITY + ("working", "done")
    return next((state for state in order if state in states), "idle")


class StatusTracker:
    """Turns repeated sidebar readings into a summary color and "something changed" alerts."""

    def __init__(self) -> None:
        self._last: dict[str, str] | None = None

    def update(self, statuses: list[SessionStatus]) -> tuple[str, list[tuple[str, str]]]:
        current: dict[str, str] = {}
        for status in statuses:
            before = current.get(status.title)
            if before is None or PRIORITY.index(status.state) < PRIORITY.index(before):
                current[status.title] = status.state
        alerts: list[tuple[str, str]] = []
        if self._last is not None:  # the first reading is the baseline, so no alerts for it
            for title, state in current.items():
                if state != self._last.get(title) and state in JUMP_ORDER:
                    alerts.append((state, title))
        self._last = current
        return most_urgent(current.values()), alerts


# The light bar's three states: what a session is doing, as you'd say it.
LIGHT_FOR_STATE = {"attention": "attention", "error": "attention", "running": "working", "unread": "done"}


def light_for_all(statuses: list[SessionStatus]) -> str:
    """One light for every session: red if any needs you, yellow if any is working, else blue."""
    state = most_urgent(LIGHT_FOR_STATE[s.state] for s in statuses if s.state in LIGHT_FOR_STATE)
    if state in ("attention", "working"):
        return state
    return "done"


def light_for_screen(
    row_states: list[str], approval_showing: bool, stop_showing: bool, question_showing: bool = False
) -> str:
    """The light for the session you're looking at, from its sidebar status and what's on screen.

    question_showing is Claude's next-step box asking you something ("Should I...?"
    with answer buttons). It only counts once Claude has stopped working.
    """
    if approval_showing or any(LIGHT_FOR_STATE.get(s) == "attention" for s in row_states):
        return "attention"
    if stop_showing or "running" in row_states:
        return "working"
    if question_showing:
        return "attention"
    return "done"


class ScreenTracker:
    """Notices when the session on screen starts needing you or finishes (not when you switch)."""

    def __init__(self) -> None:
        self._title: str | None = None
        self._light: str | None = None

    def update(self, title: str | None, light: str) -> list[str]:
        alerts = []
        if title is not None and title == self._title and light != self._light:
            if light == "attention":
                alerts.append("attention")
            elif light == "done" and self._light == "working":
                alerts.append("done")
        self._title, self._light = title, light
        return alerts


def pick_jump_target(statuses: list[SessionStatus], last_title: str | None) -> SessionStatus | None:
    """The session the jump button should open: the most urgent, cycling on repeated presses."""
    targets = [s for s in statuses if s.state in JUMP_ORDER]
    if not targets:
        return None
    targets.sort(key=lambda s: JUMP_ORDER.index(s.state))  # stable: keeps sidebar order within a state
    titles = [s.title for s in targets]
    if last_title in titles and len(targets) > 1:
        return targets[(titles.index(last_title) + 1) % len(targets)]
    return targets[0]
