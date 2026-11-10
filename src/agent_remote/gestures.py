"""Small helpers that turn analog controller input into clean events."""

from __future__ import annotations


class TriggerButton:
    """Turns an analog trigger (0.0 to 1.0) into press / release, with a little gap so it doesn't flicker."""

    RELEASE_GAP = 0.15

    def __init__(self) -> None:
        self.down = False

    def update(self, value: float, threshold: float) -> bool | None:
        """Return True on press, False on release, or None when nothing changed."""
        if not self.down and value >= threshold:
            self.down = True
            return True
        if self.down and value < max(0.0, threshold - self.RELEASE_GAP):
            self.down = False
            return False
        return None


class StickFlick:
    """Turns a stick into four buttons: push past PRESS in a direction to press it,
    come back inside RELEASE to let go. A diagonal push only counts for the
    direction pushed more, so one flick never presses two directions."""

    PRESS = 0.6
    RELEASE = 0.35
    DOMINANCE = 1.2  # the main direction must be this much stronger than the other one

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.x = 0.0
        self.y = 0.0
        self._held: str | None = None

    def update(self, x: float | None = None, y: float | None = None) -> list[tuple[str, bool]]:
        """Feed the latest axis values (-1..1, y down positive); returns (button, pressed) changes."""
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        changes: list[tuple[str, bool]] = []
        if self._held is not None:
            along = abs(self.y) if self._held in ("up", "down") else abs(self.x)
            if along < self.RELEASE:
                changes.append((f"{self.prefix}_{self._held}", False))
                self._held = None
        if self._held is None:
            direction = None
            if abs(self.y) >= self.PRESS and abs(self.y) >= abs(self.x) * self.DOMINANCE:
                direction = "down" if self.y > 0 else "up"
            elif abs(self.x) >= self.PRESS and abs(self.x) >= abs(self.y) * self.DOMINANCE:
                direction = "right" if self.x > 0 else "left"
            if direction is not None:
                self._held = direction
                changes.append((f"{self.prefix}_{direction}", True))
        return changes

    def reset(self) -> None:
        self.x = self.y = 0.0
        self._held = None


class SwipeTracker:
    """Follows one finger on the touchpad and reports vertical movement once it's clearly a swipe."""

    START_DISTANCE = 0.04  # in touchpad heights; ignores the wobble of a tap or a click

    def __init__(self) -> None:
        self._anchor: float | None = None
        self._last = 0.0
        self._swiping = False

    def down(self, y: float) -> None:
        self._anchor = y
        self._last = y
        self._swiping = False

    def move(self, y: float) -> float:
        """Return how far the finger moved down (+) or up (-) since the last call."""
        if self._anchor is None:
            return 0.0
        if not self._swiping:
            if abs(y - self._anchor) < self.START_DISTANCE:
                return 0.0
            self._swiping = True
        delta = y - self._last
        self._last = y
        return delta

    def up(self) -> None:
        self._anchor = None
        self._swiping = False
