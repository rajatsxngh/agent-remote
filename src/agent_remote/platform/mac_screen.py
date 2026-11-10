"""Understand Claude's window on the Mac from a snapshot of its accessibility tree.

No Mac-only imports: mac.py reads the tree into Node objects, and everything here
works on those, so it can be tested anywhere with a hand-made tree.

On the Mac, Chromium (and so Claude) exposes roles such as AXButton / AXGroup /
AXStaticText, names through AXTitle / AXDescription / AXValue, CSS classes through
AXDOMClassList, element ids through AXDOMIdentifier, and aria-current through
AXARIACurrent.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..matching import FoundButton, center_inside
from ..menus import MenuItem
from ..status import SessionStatus
from .common import (
    ANSWER_CLASS,
    CONVERSATION,
    IGNORED_STATUS_TEXT,
    MAIN_PANE,
    NEXT_STEP_BOX,
    POPUPS_ID,
    QUESTION_CARD_CLASS,
    SESSION_STATUS_TEXT,
    SIDEBAR,
)

BUTTON_ROLES = {"AXButton", "AXCheckBox", "AXPopUpButton", "AXMenuButton", "AXToggle"}
TEXT_BOX_ROLES = {"AXTextArea", "AXTextField"}
GROUP_ROLES = {"AXGroup", "AXList", "AXSection"}


@dataclass(eq=False)
class Node:
    ref: Any = None
    role: str = ""
    name: str = ""
    classes: tuple[str, ...] = ()
    dom_id: str = ""
    focused: bool = False
    enabled: bool = True
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom (screen points)
    shortcut: str = ""
    current: str = ""
    value: Any = None
    mark: str = ""  # a menu item's check mark, if it has one
    parent: Node | None = None
    children: list[Node] = field(default_factory=list)

    def add(self, child: Node) -> Node:
        child.parent = self
        self.children.append(child)
        return child

    @property
    def checked(self) -> bool:
        return self.value in (1, True, "1") or bool(self.mark)

    def has_class(self, prefix: str) -> bool:
        return any(c.startswith(prefix) for c in self.classes)


def walk(node: Node) -> Iterator[Node]:
    """Every node below `node` (not `node` itself), in screen-reader order."""
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def clean(text: str) -> str:
    return " ".join((text or "").split())


class ClaudeScreen:
    """What's on Claude's window right now, read from one snapshot."""

    def __init__(self, root: Node) -> None:
        self.root = root
        self.nodes = list(walk(root))
        windows = [n for n in self.nodes if n.role == "AXWindow"]
        self.window_rect = windows[0].rect if windows else root.rect

    # -- where things are --

    def groups_named(self, name: str) -> list[Node]:
        return [n for n in self.nodes if n.role in GROUP_ROLES and clean(n.name) == name]

    def main_roots(self) -> list[Node]:
        return self.groups_named(MAIN_PANE) + [n for n in self.nodes if n.dom_id == POPUPS_ID]

    def sidebar(self) -> Node | None:
        bars = self.groups_named(SIDEBAR)
        return max(bars, key=lambda n: n.rect[3] - n.rect[1]) if bars else None

    def has_web_tree(self) -> bool:
        return bool(self.groups_named(MAIN_PANE))

    def offscreen(self, node: Node) -> bool:
        left, top, right, bottom = node.rect
        _wl, w_top, _wr, w_bottom = self.window_rect
        return right <= left or bottom <= top or bottom < w_top or top > w_bottom

    # -- buttons --

    def buttons(self) -> list[FoundButton]:
        """Buttons in Claude's main pane and its popups (never the sidebar)."""
        roots = self.main_roots()
        exclude: list[tuple[int, int, int, int]] = []
        if not roots:
            roots = [self.root]
            exclude = [(n.rect[0], -(10**7), n.rect[2], 10**7) for n in self.groups_named(SIDEBAR)]
        found = []
        for root in roots:
            for node in walk(root):
                if node.role not in BUTTON_ROLES:
                    continue
                left, top, right, bottom = node.rect
                found.append(
                    FoundButton(
                        name=clean(node.name),
                        left=left,
                        top=top,
                        right=right,
                        bottom=bottom,
                        offscreen=self.offscreen(node),
                        enabled=node.enabled,
                        accel=node.shortcut,
                        ref=node,
                    )
                )
        return [b for b in found if not center_inside(b, exclude)]

    def prompt(self) -> Node | None:
        """Claude's prompt box: the text box named "Prompt", else any ProseMirror editor."""
        boxes = [n for n in self.nodes if n.role in TEXT_BOX_ROLES]
        named = [n for n in boxes if clean(n.name) == "Prompt"]
        editors = named or [n for n in boxes if n.has_class("ProseMirror")]
        if not editors:
            return None
        focused = [n for n in editors if n.focused]
        visible = [n for n in editors if not self.offscreen(n)]
        return (focused or visible or editors)[0]

    # -- sessions --

    def session_statuses(self) -> list[SessionStatus]:
        """Each sidebar session that's running, needs you, failed or finished unread."""
        sidebar = self.sidebar()
        if sidebar is None:
            return []
        found: list[SessionStatus] = []
        seen: set[tuple[int, str]] = set()
        for node in walk(sidebar):
            state = SESSION_STATUS_TEXT.get(clean(node.name))
            if state is None:
                continue
            row = node.parent
            for _ in range(7):  # the status sits a few levels inside the session's row
                if row is None:
                    break
                if row.role in BUTTON_ROLES:
                    key = (id(row), state)
                    if key not in seen:
                        seen.add(key)
                        found.append(SessionStatus(clean(row.name), state, row))
                    break
                row = row.parent
        return found

    def unknown_status_labels(self) -> list[str]:
        sidebar = self.sidebar()
        if sidebar is None:
            return []
        return [
            clean(n.name)
            for n in walk(sidebar)
            if n.role == "AXApplicationStatus" and clean(n.name)
            and clean(n.name) not in SESSION_STATUS_TEXT and clean(n.name) not in IGNORED_STATUS_TEXT
        ]

    def current_session_title(self) -> str | None:
        """The session open in Claude: the sidebar row marked as the current page."""
        sidebar = self.sidebar()
        if sidebar is None:
            return None
        for node in walk(sidebar):
            if node.role in BUTTON_ROLES and node.current == "page":
                return SessionStatus(clean(node.name), "running").title
        return None

    # -- cards, boxes and menus --

    def question_card(self) -> Node | None:
        """The question card on screen (the lowest, if there are several)."""
        cards = [n for n in self.nodes if n.has_class(QUESTION_CARD_CLASS) and not self.offscreen(n)]
        return max(cards, key=lambda n: n.rect[1]) if cards else None

    def question_answers(self) -> list[dict]:
        card = self.question_card()
        if card is None:
            return []
        answers = []
        for node in walk(card):
            is_answer = node.role in BUTTON_ROLES and (
                node.has_class(ANSWER_CLASS)
                or (node.parent is not None and node.parent.has_class(ANSWER_CLASS))
                or node.role == "AXCheckBox"
            )
            if not is_answer or not node.enabled or clean(node.name) in ("Skip", "Submit"):
                continue
            block = node
            while block.parent is not None and block.parent is not card:
                block = block.parent
            answers.append(
                {
                    "element": node,
                    "kind": "option",
                    "name": clean(node.name)[:60],
                    "checked": node.checked,
                    "focused": node.focused,
                    "top": node.rect[1],
                    "group": id(block),
                }
            )
        return answers

    def card_buttons(self, card: Node) -> list[Node]:
        return [n for n in walk(card) if n.role in BUTTON_ROLES and n.enabled]

    def next_step_boxes(self) -> list[Node]:
        return self.groups_named(NEXT_STEP_BOX)

    def menu_items(self) -> list[MenuItem]:
        items = []
        for menu in (n for n in self.nodes if n.role == "AXMenu"):
            for node in walk(menu):
                if node.role != "AXMenuItem" or not clean(node.name):
                    continue
                checkable = bool(node.mark) or node.value in (0, 1, True, False)
                items.append(MenuItem(clean(node.name), node.checked, checkable, node))
        return items

    def conversation_point(self) -> tuple[int, int] | None:
        panes = self.groups_named(MAIN_PANE)
        if not panes:
            return None
        left, top, right, bottom = panes[0].rect
        chats = [n for n in self.nodes if clean(n.name) == CONVERSATION]
        if chats:
            c_left, c_top, c_right, c_bottom = chats[0].rect
            left, right = max(left, c_left), min(right, c_right)
            top, bottom = max(top, c_top), min(bottom, c_bottom)
        if right - left < 20 or bottom - top < 20:
            return None
        return (left + right) // 2, top + (bottom - top) * 2 // 5
