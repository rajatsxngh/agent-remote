"""Decide which on-screen button an action should press, going by its label."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

Rect = tuple[int, int, int, int]  # left, top, right, bottom


@dataclass
class FoundButton:
    name: str
    left: int
    top: int
    right: int
    bottom: int
    offscreen: bool = False
    enabled: bool = True
    accel: str = ""
    ref: Any = None  # the platform's handle for pressing it


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def label_matches(name: str, label: str) -> bool:
    """True when a button's name is the label, or starts with it as whole words."""
    name, label = _norm(name), _norm(label)
    if not label:
        return False
    if name == label:
        return True
    return name.startswith(label) and not name[len(label)].isalnum()


def center_inside(button: FoundButton, rects: Iterable[Rect]) -> bool:
    cx = (button.left + button.right) / 2
    cy = (button.top + button.bottom) / 2
    return any(left <= cx <= right and top <= cy <= bottom for left, top, right, bottom in rects)


def added_text(before: str, after: str) -> tuple[str, bool] | None:
    """What dictation added to a text box: (the added characters, added at the end?).

    None if the box changed in some other way (then nothing should be touched).
    Trailing newlines, which Claude's editor reports at the end, are ignored.
    """
    old, new = before.rstrip("\n"), after.rstrip("\n")
    if new == old:
        return "", True
    if new.startswith(old):
        return new[len(old):], True
    if new.endswith(old):
        return new[: len(new) - len(old)], False
    return None


def pick_button(
    buttons: Sequence[FoundButton], labels: Sequence[str], exclude: Iterable[Rect] = ()
) -> FoundButton | None:
    """The first label wins. Among its matches, prefer visible ones, then the lowest on screen."""
    exclude = list(exclude)
    usable = [b for b in buttons if b.enabled and not center_inside(b, exclude)]
    for label in labels:
        matches = [b for b in usable if label_matches(b.name, label)]
        if matches:
            visible = [b for b in matches if not b.offscreen]
            return max(visible or matches, key=lambda b: b.bottom)
    return None
