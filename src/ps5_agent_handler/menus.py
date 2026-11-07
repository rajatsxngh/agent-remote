"""Step through one of Claude's menus (effort, model): which item to pick next.

Platform code opens the menu and reads its items; this decides which one to press.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MENU_LABELS = {"effort": "Effort", "model": "Model"}
# Effort level names, least to most, used to tell which way the effort menu runs.
EFFORT_WORDS = ("low", "medium", "high", "extra", "xhigh", "max")
# The levels Claude's /effort command takes, least to most, and how Claude shows them.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
EFFORT_NAMES = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high", "max": "Max"}


def effort_from_label(text: str) -> str | None:
    """The effort level in Claude's model button, which reads like "Model: Opus 5.5 Medium"."""
    words = text.casefold().replace("-", " ").split()
    if _contains(words, ["extra", "high"]) or "xhigh" in words:
        return "xhigh"
    for level in ("max", "high", "medium", "low"):
        if level in words:
            return level
    return None


def effort_candidates(current: str, step: int) -> list[str]:
    """Levels to try, nearest first. Not every model has every level, so the
    caller tries them in turn until one takes."""
    index = EFFORT_LEVELS.index(current)
    if step > 0:
        return list(EFFORT_LEVELS[index + 1 :])
    return list(reversed(EFFORT_LEVELS[:index]))


@dataclass
class MenuItem:
    name: str
    checked: bool = False
    checkable: bool = False  # a pickable option (radio / checkbox), not a plain command
    ref: Any = None


def effort_rank(name: str) -> int | None:
    words = name.casefold().replace("-", " ").split()
    for rank in range(len(EFFORT_WORDS) - 1, -1, -1):  # "extra high" counts as extra, not high
        if EFFORT_WORDS[rank] in words:
            return rank
    return None


def _contains(words: list[str], part: list[str]) -> bool:
    return any(words[i : i + len(part)] == part for i in range(len(words) - len(part) + 1))


def current_index(items: list[MenuItem], hint: str) -> int | None:
    """The picked item: the checked one, or else the one Claude's model button mentions.

    hint is that button's text, such as "Model: Opus 5.5 Medium". An item matches
    when its first words appear in it; the item matching the most words wins.
    """
    checked = [i for i, item in enumerate(items) if item.checked]
    if len(checked) == 1:
        return checked[0]
    hint_words = hint.casefold().replace("model:", " ").split()
    best, best_score, tied = None, 0, False
    for index, item in enumerate(items):
        words = item.name.casefold().split()
        score = next((n for n in range(len(words), 0, -1) if _contains(hint_words, words[:n])), 0)
        if score > best_score:
            best, best_score, tied = index, score, False
        elif score and score == best_score:
            tied = True
    return None if tied or best_score == 0 else best


def choose(items: list[MenuItem], step: int, hint: str, menu: str) -> tuple[str, MenuItem | None, str]:
    """Returns (outcome, item to press, label).

    outcome is "changed", "same" (already at the end; pressing the item just
    closes the menu) or "unknown" (can't tell which is picked; press nothing).
    """
    pickable = [item for item in items if item.checkable]
    choices = pickable if len(pickable) >= 2 else list(items)
    if not choices:
        return "unknown", None, ""
    current = current_index(choices, hint)
    if current is None:
        return "unknown", None, ", ".join(item.name for item in choices)
    if menu == "effort":
        first, last = effort_rank(choices[0].name), effort_rank(choices[-1].name)
        if first is not None and last is not None and first > last:
            step = -step  # the menu lists the highest effort first
    target = max(0, min(len(choices) - 1, current + step))
    outcome = "changed" if target != current else "same"
    return outcome, choices[target], choices[target].name
