"""macOS: find and focus Claude, press keys, press on-screen buttons, paste, scroll.

The Mac version of windows.py. It reads Claude's accessibility tree through the
macOS Accessibility API (AXUIElement, via pyobjc) and sends keys with Quartz
events. It needs the Accessibility permission for the app (or the Terminal it
runs in): System Settings > Privacy & Security > Accessibility.

Not yet tested on a Mac. The parts that don't need a Mac (key translation and
reading Claude's screen from a tree) have tests that run anywhere.
"""

from __future__ import annotations

import fcntl
import logging
import os
import tempfile
import time

from ..keys import Chord
from ..matching import added_text, center_inside, label_matches, pick_button
from ..menus import EFFORT_NAMES, choose, effort_candidates, effort_from_label
from ..status import SessionStatus, light_for_screen
from .common import (
    APPROVAL_LABELS,
    CONVERSATION,
    DISMISS_LABELS,
    MENU_SHORTCUTS,
    MODEL_WORDS,
    NO_LABELS,
)
from .mac_keys import FLAGS, keycode_for, mac_chord
from .mac_screen import BUTTON_ROLES, TEXT_BOX_ROLES, ClaudeScreen, Node, clean, walk

log = logging.getLogger(__name__)

CLAUDE_BUNDLE_IDS = ("com.anthropic.claudefordesktop",)
MAX_NODES = 6000
NEWEST_MESSAGES = 6  # read only the newest messages of a long conversation
WAKE_GRACE_S = 3.0
CURSOR_PARK_S = 0.7
ATTRIBUTES = [
    "AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXDOMClassList",
    "AXDOMIdentifier", "AXFocused", "AXEnabled", "AXPosition", "AXSize",
    "AXKeyShortcutsValue", "AXARIACurrent", "AXMenuItemMarkChar", "AXChildren",
]


def _plain(value):
    """pyobjc values (NSString, NSNumber) as plain Python values."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    try:
        return int(value) if str(value).lstrip("-").isdigit() else str(value)
    except Exception:
        return str(value)


class _Ax:
    """Reads Claude's accessibility tree into Node snapshots, and acts on its elements."""

    def __init__(self) -> None:
        import ApplicationServices as AS

        self.AS = AS
        self._woken: dict[int, float] = {}

    def app(self, pid: int):
        return self.AS.AXUIElementCreateApplication(pid)

    # Electron apps like Claude only build their accessibility tree once asked to.
    def wake(self, pid: int) -> bool:
        if pid in self._woken:
            return False
        app = self.app(pid)
        for attribute in ("AXManualAccessibility", "AXEnhancedUserInterface"):
            try:
                self.AS.AXUIElementSetAttributeValue(app, attribute, True)
            except Exception as exc:
                log.debug("Couldn't set %s: %s", attribute, exc)
        self._woken[pid] = time.monotonic()
        return True

    def recently_woken(self, pid: int) -> bool:
        return time.monotonic() - self._woken.get(pid, 0.0) < WAKE_GRACE_S

    def _is_error(self, value) -> bool:
        if type(value).__name__ != "AXValueRef":
            return False
        return self.AS.AXValueGetType(value) == self.AS.kAXValueAXErrorType

    def _attributes(self, element) -> dict:
        try:
            error, values = self.AS.AXUIElementCopyMultipleAttributeValues(element, ATTRIBUTES, 0, None)
        except Exception:
            return {}
        if error or values is None:
            return {}
        return {name: value for name, value in zip(ATTRIBUTES, values) if value is not None and not self._is_error(value)}

    def _rect(self, attributes: dict) -> tuple[int, int, int, int]:
        AS = self.AS
        try:
            _ok, point = AS.AXValueGetValue(attributes["AXPosition"], AS.kAXValueCGPointType, None)
            _ok, size = AS.AXValueGetValue(attributes["AXSize"], AS.kAXValueCGSizeType, None)
            return int(point.x), int(point.y), int(point.x + size.width), int(point.y + size.height)
        except Exception:
            return 0, 0, 0, 0

    def _node(self, element, parent: Node | None, budget: list[int], depth: int) -> Node:
        attributes = self._attributes(element)
        role = str(attributes.get("AXRole", "") or "")
        if str(attributes.get("AXSubrole", "") or "") == "AXApplicationStatus":
            role = "AXApplicationStatus"
        value = _plain(attributes.get("AXValue"))
        name = str(attributes.get("AXTitle", "") or "") or str(attributes.get("AXDescription", "") or "")
        if not name and role == "AXStaticText" and isinstance(value, str):
            name = value
        node = Node(
            ref=element,
            role=role,
            name=name,
            classes=tuple(str(c) for c in (attributes.get("AXDOMClassList") or ())),
            dom_id=str(attributes.get("AXDOMIdentifier", "") or ""),
            focused=bool(attributes.get("AXFocused", False)),
            enabled=bool(attributes.get("AXEnabled", True)),
            rect=self._rect(attributes),
            shortcut=str(attributes.get("AXKeyShortcutsValue", "") or ""),
            current=str(attributes.get("AXARIACurrent", "") or ""),
            value=value,
            mark=str(attributes.get("AXMenuItemMarkChar", "") or ""),
        )
        if parent is not None:
            parent.add(node)
        budget[0] -= 1
        children = list(attributes.get("AXChildren") or ())
        if clean(name) == CONVERSATION:
            children = children[-NEWEST_MESSAGES:]
        if depth < 60:
            for child in children:
                if budget[0] <= 0:
                    break
                self._node(child, node, budget, depth + 1)
        return node

    def snapshot(self, pid: int) -> ClaudeScreen:
        return ClaudeScreen(self._node(self.app(pid), None, [MAX_NODES], 0))

    def press(self, node: Node) -> None:
        error = self.AS.AXUIElementPerformAction(node.ref, "AXPress")
        if error:
            raise RuntimeError(f"couldn't press '{node.name}' (accessibility error {error})")

    def focus(self, node: Node) -> None:
        self.AS.AXUIElementSetAttributeValue(node.ref, "AXFocused", True)


class MacPlatform:
    def __init__(self) -> None:
        import AppKit
        import Quartz

        self.AppKit = AppKit
        self.Quartz = Quartz
        self._ax: _Ax | None = None
        self._down: list[Chord] = []
        self._lock_file = None
        self._parked: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._unpark_at = 0.0
        self._unknown_labels: set[str] = set()

    # -- setup --

    def thread_init(self) -> None:
        self._ax = _Ax()

    @property
    def ax(self) -> _Ax:
        if self._ax is None:
            self._ax = _Ax()
        return self._ax

    def prepare(self) -> None:
        pid = self.find_claude_window()
        if pid:
            self.ax.wake(pid)

    def acquire_single_instance(self) -> bool:
        path = os.path.join(tempfile.gettempdir(), "agent-remote.lock")
        self._lock_file = open(path, "w")  # noqa: SIM115 - kept open while the app runs
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def idle(self) -> None:
        if self._parked is not None and time.monotonic() >= self._unpark_at:
            self._unpark()

    # -- the Claude app --

    def _running_app(self, pid: int):
        return self.AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)

    def find_claude_window(self) -> int | None:
        """Claude's process id (the Mac's stand-in for a window handle)."""
        AppKit = self.AppKit
        for bundle_id in CLAUDE_BUNDLE_IDS:
            apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
            if apps:
                return int(apps[0].processIdentifier())
        for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
            if str(app.localizedName() or "") == "Claude" and app.activationPolicy() == 0:
                return int(app.processIdentifier())
        return None

    def is_foreground(self, window: int) -> bool:
        front = self.AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return front is not None and int(front.processIdentifier()) == window

    def bring_to_front(self, window: int) -> bool:
        if self.is_foreground(window):
            return True
        app = self._running_app(window)
        if app is None:
            return False
        app.unhide()
        app.activateWithOptions_(self.AppKit.NSApplicationActivateIgnoringOtherApps)
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            if self.is_foreground(window):
                return True
            time.sleep(0.02)
        return self.is_foreground(window)

    def _screen(self, window: int) -> ClaudeScreen:
        """A fresh snapshot, waiting briefly if Claude's tree was only just switched on."""
        self.ax.wake(window)
        screen = self.ax.snapshot(window)
        while self.ax.recently_woken(window) and not screen.has_web_tree():
            time.sleep(0.25)
            screen = self.ax.snapshot(window)
        return screen

    # -- keys (written the Windows way; mac_chord turns Ctrl into Cmd and so on) --

    def _post(self, code: int, down: bool, flags: int) -> None:
        Q = self.Quartz
        event = Q.CGEventCreateKeyboardEvent(None, code, down)
        Q.CGEventSetFlags(event, flags)
        Q.CGEventPost(Q.kCGHIDEventTap, event)

    def send_chord(self, chord: Chord) -> None:
        mods, key = mac_chord(chord)
        flags = 0
        for mod in mods:
            flags |= FLAGS[mod]
            self._post(keycode_for(mod), True, flags)
        code = keycode_for(key)
        self._post(code, True, flags)
        self._post(code, False, flags)
        for mod in reversed(mods):
            flags &= ~FLAGS[mod]
            self._post(keycode_for(mod), False, flags)

    def chord_down(self, chord: Chord) -> None:
        mods, key = mac_chord(chord)
        flags = 0
        for mod in mods:
            flags |= FLAGS[mod]
            self._post(keycode_for(mod), True, flags)
        if key in FLAGS:
            flags |= FLAGS[key]
        self._post(keycode_for(key), True, flags)
        self._down.append(chord)

    def chord_up(self, chord: Chord) -> None:
        mods, key = mac_chord(chord)
        flags = 0
        self._post(keycode_for(key), False, flags)
        for mod in reversed(mods):
            self._post(keycode_for(mod), False, flags)
        if chord in self._down:
            self._down.remove(chord)

    def release_all(self) -> None:
        while self._down:
            self.chord_up(self._down[-1])
        self._unpark()

    def _select_chars(self, count: int, backwards: bool) -> None:
        shift = FLAGS["shift"]
        arrow = keycode_for("left" if backwards else "right")
        self._post(keycode_for("shift"), True, shift)
        for _ in range(count):
            self._post(arrow, True, shift)
            self._post(arrow, False, shift)
        self._post(keycode_for("shift"), False, 0)

    # -- clipboard and text --

    def _clipboard_text(self) -> str | None:
        AppKit = self.AppKit
        text = AppKit.NSPasteboard.generalPasteboard().stringForType_(AppKit.NSPasteboardTypeString)
        return str(text) if text is not None else None

    def set_clipboard(self, text: str) -> None:
        AppKit = self.AppKit
        board = AppKit.NSPasteboard.generalPasteboard()
        board.clearContents()
        board.setString_forType_(text, AppKit.NSPasteboardTypeString)

    def paste_text(self, window: int, text: str, send: bool) -> bool:
        previous = self._clipboard_text()
        self.set_clipboard(text)
        try:
            if not self.is_foreground(window):
                raise RuntimeError("Claude lost focus before the text could be pasted")
            self.send_chord(Chord(("ctrl",), "end"))  # Cmd+Down: after anything already typed
            self.send_chord(Chord(("ctrl",), "v"))  # Cmd+V
            time.sleep(0.2)
            sent = False
            if send:
                if self.is_foreground(window):
                    self.send_chord(Chord((), "enter"))
                    sent = True
                else:
                    log.warning("Claude lost focus, so the message wasn't sent.")
            time.sleep(0.25)
            return sent
        finally:
            if previous is not None:
                self.set_clipboard(previous)

    def prompt_text(self, window: int) -> str:
        prompt = self._screen(window).prompt()
        value = prompt.value if prompt is not None else ""
        return value if isinstance(value, str) else ""

    def focus_prompt(self, window: int) -> bool:
        prompt = self._screen(window).prompt()
        if prompt is None:
            return False
        self.ax.focus(prompt)
        return True

    def prompt_caret_to_end(self, window: int) -> None:
        if self.focus_prompt(window) and self.is_foreground(window):
            self.send_chord(Chord(("ctrl",), "end"))

    # -- on-screen buttons --

    def click_button(self, window: int, labels: tuple[str, ...]) -> str | None:
        target = pick_button(self._screen(window).buttons(), labels)
        if target is None:
            return None
        self.ax.press(target.ref)
        return target.name

    # -- sessions --

    def session_statuses(self, window: int) -> list[SessionStatus]:
        screen = self._screen(window)
        for label in screen.unknown_status_labels():
            if label not in self._unknown_labels:
                self._unknown_labels.add(label)
                log.info("A session's status says '%s', which this app doesn't know yet.", label)
        return screen.session_statuses()

    def open_session(self, window: int, status: SessionStatus) -> None:
        self.ax.press(status.ref)

    def screen_light(self, window: int) -> tuple[str | None, str]:
        screen = self._screen(window)
        title = screen.current_session_title()
        if title is None:
            return None, "idle"
        row_states = [s.state for s in screen.session_statuses() if s.title == title]
        buttons = [b for b in screen.buttons() if b.enabled]
        names = [b.name for b in buttons if not b.offscreen]
        approval = any(label_matches(name, label) for name in names for label in APPROVAL_LABELS)
        stop = "Stop" in names
        boxes = [n.rect for n in screen.next_step_boxes()]
        question = any(center_inside(b, boxes) for b in buttons) if boxes else False
        question = question or screen.question_card() is not None
        return title, light_for_screen(row_states, approval, stop, question)

    # -- Claude's menus and /effort --

    def step_menu(self, window: int, menu: str, step: int) -> tuple[str, str]:
        screen = self._screen(window)
        if not self.is_foreground(window):
            return "no_menu", ""
        hint = next((b.name for b in screen.buttons() if b.name.startswith("Model:")), "")
        items = []
        for _attempt in range(2):
            self.focus_prompt(window)
            if not self.is_foreground(window):
                return "no_menu", ""
            self.send_chord(MENU_SHORTCUTS[menu])
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                items = self.ax.snapshot(window).menu_items()
                if items:
                    break
                time.sleep(0.05)
            if items:
                break
        if not items:
            return "no_menu", ""
        if menu == "model":
            models = [i for i in items if i.name.split()[0].casefold() in MODEL_WORDS]
            items = models if len(models) >= 2 else items
        outcome, target, label = choose(items, step, hint, menu)
        if target is not None:
            self.ax.press(target.ref)
        return outcome, label

    def step_effort(self, window: int, step: int) -> tuple[str, str]:
        screen = self._screen(window)
        if not self.is_foreground(window):
            return "no_control", ""
        label = next((b.name for b in screen.buttons() if b.name.startswith("Model:")), "")
        current = effort_from_label(label)
        if current is None:
            return "unknown", label
        if self.prompt_text(window).strip():
            return "busy", ""
        for level in effort_candidates(current, step):
            if not self.is_foreground(window) or not self.focus_prompt(window):
                return "no_control", ""
            self.paste_text(window, f"/effort {level}", send=True)
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                now = next((b.name for b in self.ax.snapshot(window).buttons() if b.name.startswith("Model:")), "")
                if effort_from_label(now) == level:
                    return "changed", EFFORT_NAMES[level]
                time.sleep(0.05)
            if self.prompt_text(window).strip() and self.is_foreground(window):
                self.send_chord(Chord(("ctrl",), "a"))
                self.send_chord(Chord((), "backspace"))
        return "same", EFFORT_NAMES[current]

    # -- next-step box and question cards --

    def answer_next_step(self, window: int, answer: str) -> str | None:
        screen = self._screen(window)
        buttons = [b for b in screen.buttons() if b.enabled]
        mains = [b for b in buttons if "enter" in b.accel.casefold()]
        card = screen.question_card()
        if card is not None:
            mains += [b for b in buttons if b.name == "Submit" and b.ref in screen.card_buttons(card)]
        visible = [b for b in mains if not b.offscreen]
        if visible or mains:
            main = max(visible or mains, key=lambda b: b.bottom)
            if answer == "yes":
                self.ax.press(main.ref)
                return main.name
            around: Node | None = main.ref
            for _ in range(6):
                around = around.parent if around is not None else None
                if around is None:
                    break
                candidates = [n for n in walk(around) if n.role in BUTTON_ROLES and n.enabled]
                target = next(
                    (n for label in NO_LABELS for n in candidates if label_matches(clean(n.name), label)), None
                ) or next((n for label in DISMISS_LABELS for n in candidates if clean(n.name) == label), None)
                if target is not None:
                    self.ax.press(target)
                    return clean(target.name)
            return None
        boxes = screen.next_step_boxes()
        if not boxes:
            return None
        box_buttons = [n for n in walk(boxes[-1]) if n.role in BUTTON_ROLES and n.enabled]
        if answer == "yes":
            target = box_buttons[0] if box_buttons else None
        else:
            target = next((n for label in NO_LABELS for n in box_buttons if label_matches(clean(n.name), label)), None)
        if target is None:
            return None
        self.ax.press(target)
        return clean(target.name)

    def _focus_other_box(self, window: int, answer_name: str) -> bool:
        for _attempt in range(6):
            screen = self.ax.snapshot(window)
            other = next((a for a in screen.question_answers() if a["name"] == answer_name), None)
            holder = other["element"].parent if other else None
            for _ in range(2):
                if holder is None:
                    break
                boxes = [n for n in walk(holder) if n.role in TEXT_BOX_ROLES]
                if boxes:
                    self.ax.focus(boxes[0])
                    return True
                holder = holder.parent
            time.sleep(0.08)
        return False

    def question_step(self, window: int, op: str) -> str | None:
        answers = self._screen(window).question_answers()
        if not answers:
            return None
        focused = next((i for i, a in enumerate(answers) if a["focused"]), None)
        checked = next((i for i, a in enumerate(answers) if a["checked"]), None)
        here = focused if focused is not None else checked
        if op == "option_pick":
            if focused is None:
                start = checked if checked is not None else 0
                self.ax.focus(answers[start]["element"])
                return f"Highlighted '{answers[start]['name']}'. Press Cross again to pick it."
            answer = answers[focused]
            self.ax.press(answer["element"])
            if answer["name"].casefold().startswith("other") and self._focus_other_box(window, answer["name"]):
                return "Picked 'Other'. Type or dictate (Circle) your answer, then Triangle sends it."
            return f"{'Un-picked' if answer['checked'] else 'Picked'} '{answer['name']}'."
        if op in ("question_previous", "question_next"):
            groups: list[list[int]] = []
            for i, answer in enumerate(answers):
                if groups and answers[groups[-1][0]]["group"] == answer["group"]:
                    groups[-1].append(i)
                else:
                    groups.append([i])
            current = next((g for g, members in enumerate(groups) if here in members), 0)
            target = max(0, min(len(groups) - 1, current + (1 if op == "question_next" else -1)))
            index = next((i for i in groups[target] if answers[i]["checked"]), groups[target][0])
            self.ax.focus(answers[index]["element"])
            return f"Question {target + 1} of {len(groups)}: '{answers[index]['name']}'."
        step = 1 if op == "option_next" else -1
        if focused is None:
            target = here if here is not None else (0 if step > 0 else len(answers) - 1)
        else:
            target = max(0, min(len(answers) - 1, focused + step))
        self.ax.focus(answers[target]["element"])
        return f"Option: '{answers[target]['name']}'."

    def _picked_other(self, window: int) -> dict | None:
        for answer in self._screen(window).question_answers():
            if answer["checked"] and answer["name"].casefold().startswith("other"):
                return answer
        return None

    def question_other_picked(self, window: int) -> bool:
        return self._picked_other(window) is not None

    def focus_question_other(self, window: int) -> bool:
        other = self._picked_other(window)
        return other is not None and self._focus_other_box(window, other["name"])

    def dictation_state(self, window: int) -> str:
        names = [b.name for b in self._screen(window).buttons()]
        if "Stop dictation" in names:
            return "recording"
        if any(name.startswith("Finishing dictation") for name in names):
            return "finishing"
        return "idle"

    def move_dictation_to_other(self, window: int, before: str) -> tuple[str, str]:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and self.dictation_state(window) != "idle":
            time.sleep(0.15)
        old = before.rstrip("\n")
        settle = time.monotonic() + 2.0
        while time.monotonic() < settle and self.prompt_text(window).rstrip("\n") == old:
            time.sleep(0.1)
        time.sleep(0.2)
        new = self.prompt_text(window)
        change = added_text(before, new)
        if change is None:
            return "changed", new.strip()
        added, at_end = change
        words = added.strip()
        if not words:
            return "nothing", ""
        other = self._picked_other(window)
        if other is None or not self.is_foreground(window) or not self.focus_prompt(window):
            return "no_box", words
        self.send_chord(Chord(("ctrl",), "end" if at_end else "home"))
        self._select_chars(len(added), backwards=at_end)
        self.send_chord(Chord((), "backspace"))
        if not self._focus_other_box(window, other["name"]):
            self.set_clipboard(words)
            return "no_box", words
        self.paste_text(window, words, send=False)
        return "moved", words

    # -- scrolling --

    def _cursor(self) -> tuple[int, int]:
        Q = self.Quartz
        point = Q.CGEventGetLocation(Q.CGEventCreate(None))
        return int(point.x), int(point.y)

    def scroll_chat(self, window: int, notches: int) -> bool:
        Q = self.Quartz
        if self._parked is None:
            point = self._screen(window).conversation_point()
            if point is None:
                return False
            saved = self._cursor()
            Q.CGWarpMouseCursorPosition(point)
            self._parked = (point, saved)
        event = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, -3 * notches)
        Q.CGEventPost(Q.kCGHIDEventTap, event)
        self._unpark_at = time.monotonic() + CURSOR_PARK_S
        return True

    def _unpark(self) -> None:
        if self._parked is None:
            return
        parked_at, saved = self._parked
        self._parked = None
        if self._cursor() == parked_at:
            self.Quartz.CGWarpMouseCursorPosition(saved)

    # -- looking around (for --inspect) --

    def inspect(self, window: int) -> dict:
        if self.ax.wake(window):
            time.sleep(1.5)
        screen = self._screen(window)
        return {
            "title": "Claude",
            "buttons": screen.buttons(),
            "prompt": screen.prompt() is not None,
            "conversation": screen.conversation_point() is not None,
        }
