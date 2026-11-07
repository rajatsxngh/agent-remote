"""Windows: find and focus Claude, press keys, press on-screen buttons, paste, scroll.

Keys go through SendInput. On-screen buttons are found and pressed through UI
Automation, so pressing them never moves the mouse. Scrolling uses the mouse
wheel over the conversation (the cursor is moved there while you scroll, then
put back), because the conversation's UI Automation scroll position isn't
reliable.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes

from ..keys import Chord
from ..matching import FoundButton, center_inside, label_matches, pick_button
from ..menus import EFFORT_NAMES, MenuItem, choose, effort_candidates, effort_from_label
from ..status import SessionStatus, light_for_screen

# Buttons that mean Claude is asking your permission for something.
APPROVAL_LABELS = ("Allow once", "Allow for this session", "Always allow", "Deny")
# The box at the end of a reply where Claude asks what to do next, with answer buttons.
NEXT_STEP_BOX = "Suggested next actions"
NO_LABELS = ("Not now", "No", "Skip", "Later", "Cancel")
DISMISS_LABELS = ("Dismiss recap", "Dismiss suggestion", "Dismiss")

log = logging.getLogger(__name__)

# comtypes sets up COM in whichever thread imports it first. UI Automation
# clients should use the multithreaded apartment, so ask for that.
sys.coinit_flags = 0  # COINIT_MULTITHREADED

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")

ULONG_PTR = ctypes.c_size_t
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
INJECTED_TAG = 0x50533541  # "PS5A": marks the input this app sends
SW_RESTORE = 9
GW_OWNER = 4
OBJID_CLIENT = -4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
ERROR_ALREADY_EXISTS = 183
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
CLAUDE_EXE = "claude.exe"
CLAUDE_WINDOW_CLASS = "Chrome_WidgetWin_1"
CURSOR_PARK_S = 0.7  # how long the cursor stays over the conversation after the last scroll


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _proto(dll, name, restype, *argtypes):
    function = getattr(dll, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


_SendInput = _proto(user32, "SendInput", wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_MapVirtualKeyW = _proto(user32, "MapVirtualKeyW", wintypes.UINT, wintypes.UINT, wintypes.UINT)
_VkKeyScanW = _proto(user32, "VkKeyScanW", ctypes.c_short, wintypes.WCHAR)
_GetForegroundWindow = _proto(user32, "GetForegroundWindow", wintypes.HWND)
_SetForegroundWindow = _proto(user32, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND)
_BringWindowToTop = _proto(user32, "BringWindowToTop", wintypes.BOOL, wintypes.HWND)
_ShowWindow = _proto(user32, "ShowWindow", wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_IsIconic = _proto(user32, "IsIconic", wintypes.BOOL, wintypes.HWND)
_IsWindowVisible = _proto(user32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
_GetWindow = _proto(user32, "GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT)
_GetWindowThreadProcessId = _proto(
    user32, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
)
_AttachThreadInput = _proto(user32, "AttachThreadInput", wintypes.BOOL, wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
_GetClassNameW = _proto(user32, "GetClassNameW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowTextLengthW = _proto(user32, "GetWindowTextLengthW", ctypes.c_int, wintypes.HWND)
_GetWindowTextW = _proto(user32, "GetWindowTextW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_GetWindowRect = _proto(user32, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_EnumWindows = _proto(user32, "EnumWindows", wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
_EnumChildWindows = _proto(user32, "EnumChildWindows", wintypes.BOOL, wintypes.HWND, WNDENUMPROC, wintypes.LPARAM)
_GetCursorPos = _proto(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
_SetCursorPos = _proto(user32, "SetCursorPos", wintypes.BOOL, ctypes.c_int, ctypes.c_int)
_PeekMessageW = _proto(
    user32, "PeekMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
)
_OpenClipboard = _proto(user32, "OpenClipboard", wintypes.BOOL, wintypes.HWND)
_CloseClipboard = _proto(user32, "CloseClipboard", wintypes.BOOL)
_EmptyClipboard = _proto(user32, "EmptyClipboard", wintypes.BOOL)
_IsClipboardFormatAvailable = _proto(user32, "IsClipboardFormatAvailable", wintypes.BOOL, wintypes.UINT)
_GetClipboardData = _proto(user32, "GetClipboardData", wintypes.HANDLE, wintypes.UINT)
_SetClipboardData = _proto(user32, "SetClipboardData", wintypes.HANDLE, wintypes.UINT, wintypes.HANDLE)
_GlobalAlloc = _proto(kernel32, "GlobalAlloc", wintypes.HGLOBAL, wintypes.UINT, ctypes.c_size_t)
_GlobalLock = _proto(kernel32, "GlobalLock", ctypes.c_void_p, wintypes.HGLOBAL)
_GlobalUnlock = _proto(kernel32, "GlobalUnlock", wintypes.BOOL, wintypes.HGLOBAL)
_GlobalFree = _proto(kernel32, "GlobalFree", wintypes.HGLOBAL, wintypes.HGLOBAL)
_OpenProcess = _proto(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_QueryFullProcessImageNameW = _proto(
    kernel32, "QueryFullProcessImageNameW", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
)
_CloseHandle = _proto(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_CreateMutexW = _proto(kernel32, "CreateMutexW", wintypes.HANDLE, ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_GetCurrentThreadId = _proto(kernel32, "GetCurrentThreadId", wintypes.DWORD)
_CoInitializeEx = _proto(ole32, "CoInitializeEx", ctypes.c_long, ctypes.c_void_p, wintypes.DWORD)


def _use_physical_pixels() -> None:
    """Make window, cursor and UI Automation coordinates all agree on scaled displays."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    except AttributeError:  # older Windows
        pass


# ---------------------------------------------------------------- keyboard

VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "enter": 0x0D, "escape": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "apps": 0x5D,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}
US_PUNCTUATION = {
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
    "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
EXTENDED_KEYS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D}


def vk_for(key: str) -> int:
    if key in VK:
        return VK[key]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return ord(key.upper())
    if len(key) == 1:
        found = _VkKeyScanW(key)
        if found != -1:
            return found & 0xFF
        if key in US_PUNCTUATION:
            return US_PUNCTUATION[key]
    raise ValueError(f"no Windows key code for '{key}'")


def _chord_vks(chord: Chord) -> list[int]:
    return [VK[mod] for mod in chord.mods] + [vk_for(chord.key)]


def _key_event(vk: int, up: bool) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    item = INPUT(type=INPUT_KEYBOARD)
    item.ki = KEYBDINPUT(
        wVk=vk, wScan=_MapVirtualKeyW(vk, 0) & 0xFFFF, dwFlags=flags, time=0, dwExtraInfo=INJECTED_TAG
    )
    return item


def _send(items: list[INPUT]) -> None:
    array = (INPUT * len(items))(*items)
    sent = _SendInput(len(items), array, ctypes.sizeof(INPUT))
    if sent != len(items):
        raise OSError(f"Windows took {sent} of {len(items)} input events (error {ctypes.get_last_error()})")


def _wheel_event(notches: int) -> INPUT:
    item = INPUT(type=INPUT_MOUSE)
    # Positive notches scroll down, which is a negative wheel delta.
    item.mi = MOUSEINPUT(0, 0, (-WHEEL_DELTA * notches) & 0xFFFFFFFF, MOUSEEVENTF_WHEEL, 0, INJECTED_TAG)
    return item


# ---------------------------------------------------------------- windows


def _process_name(pid: int) -> str:
    handle = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.basename(buffer.value).lower()
    finally:
        _CloseHandle(handle)


def _class_name(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def window_title(hwnd) -> str:
    length = _GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    _GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _render_widget_windows(hwnd) -> list[int]:
    found: list[int] = []

    @WNDENUMPROC
    def collect(child, _lparam):
        if _class_name(child) == "Chrome_RenderWidgetHostHWND":
            found.append(child)
        return True

    _EnumChildWindows(hwnd, collect, 0)
    return found


def _cursor() -> tuple[int, int]:
    point = wintypes.POINT()
    _GetCursorPos(ctypes.byref(point))
    return point.x, point.y


# ---------------------------------------------------------------- clipboard


def _open_clipboard() -> None:
    for _ in range(25):
        if _OpenClipboard(None):
            return
        time.sleep(0.02)
    raise OSError("the clipboard is busy (another app has it open)")


def clipboard_get_text() -> str | None:
    _open_clipboard()
    try:
        if not _IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = _GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = _GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _GlobalUnlock(handle)
    finally:
        _CloseClipboard()


def clipboard_set_text(text: str) -> None:
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = _GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise MemoryError("couldn't get memory for the clipboard")
    pointer = _GlobalLock(handle)
    ctypes.memmove(pointer, data, len(data))
    _GlobalUnlock(handle)
    _open_clipboard()
    try:
        _EmptyClipboard()
        if not _SetClipboardData(CF_UNICODETEXT, handle):
            _GlobalFree(handle)
            raise OSError(f"couldn't set the clipboard (error {ctypes.get_last_error()})")
    finally:
        _CloseClipboard()


# ---------------------------------------------------------------- UI Automation

PATTERN_INVOKE = 10000
PATTERN_TOGGLE = 10015
PATTERN_LEGACY = 10018
PROP_BOUNDS = 30001
PROP_CONTROL_TYPE = 30003
PROP_NAME = 30005
PROP_ACCELERATOR = 30006
PROP_HAS_FOCUS = 30008
PROP_ENABLED = 30010
PROP_AUTOMATION_ID = 30011
PROP_CLASS_NAME = 30012
PROP_OFFSCREEN = 30022
PROP_ARIA = 30102
PATTERN_SELECTION_ITEM = 10010
PATTERN_VALUE = 10002
TYPE_BUTTON = 50000
TYPE_CHECKBOX = 50002
TYPE_EDIT = 50004
TYPE_LIST_ITEM = 50007
TYPE_MENU = 50009
TYPE_MENU_ITEM = 50011
TYPE_RADIO = 50013
TYPE_TEXT = 50020
TYPE_GROUP = 50026

# What a session row's hidden status line says (for screen readers), and what it
# means. The Code tab's own words come first; the others are what Cowork rows say.
SESSION_STATUS_TEXT = {
    "Awaiting input": "attention",
    "Awaiting answer": "attention",
    "Running": "running",
    "Using the browser": "running",
    "Unread response": "unread",
    "Error": "error",
    "Needs attention": "attention",
    "Unread": "unread",
}
IGNORED_STATUS_TEXT = {"Idle", "Done"}
TYPE_STATUS_BAR = 50017
# Claude's own shortcuts for its menus (from the Code tab's keyboard shortcuts list).
MENU_SHORTCUTS = {"effort": Chord(("ctrl", "shift"), "e"), "model": Chord(("ctrl", "shift"), "i")}
SCOPE_DESCENDANTS = 4
WAKE_GRACE_S = 3.0  # Chromium needs a moment to build its tree after waking

# Where Claude's own controls live. Searching only here keeps session names in
# the sidebar (say, a session called "Deny the refund") from ever matching.
MAIN_PANE = "Primary pane"
POPUPS_ID = "portal-root"
CONVERSATION = "Chat messages"


class _Uia:
    """UI Automation helpers. Create and use on one thread."""

    def __init__(self) -> None:
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uiac

        self.uiac = uiac
        self.ua = comtypes.client.CreateObject(uiac.CUIAutomation, interface=uiac.IUIAutomation)
        self._woken: dict[int, float] = {}
        self._unknown_labels: set[str] = set()

    # Chromium (and so Electron apps like Claude) only builds the web page's
    # accessibility tree once something asks for it. Asking a page window for
    # its MSAA object, as Windows' Inspect tool does, turns it on.
    def wake(self, hwnd: int) -> bool:
        if hwnd in self._woken:
            return False
        import comtypes.client

        comtypes.client.GetModule("oleacc.dll")
        from comtypes.gen.Accessibility import IAccessible

        for child in _render_widget_windows(hwnd):
            accessible = ctypes.POINTER(IAccessible)()
            try:
                ctypes.oledll.oleacc.AccessibleObjectFromWindow(
                    child, ctypes.c_long(OBJID_CLIENT), ctypes.byref(IAccessible._iid_), ctypes.byref(accessible)
                )
                accessible.accChildCount  # noqa: B018 - the query itself is what wakes it
            except Exception as exc:
                log.debug("Accessibility wake-up failed for window %s: %s", child, exc)
        self._woken[hwnd] = time.monotonic()
        return True

    def recently_woken(self, hwnd: int) -> bool:
        return time.monotonic() - self._woken.get(hwnd, 0.0) < WAKE_GRACE_S

    def _is(self, prop: int, value):
        return self.ua.CreatePropertyCondition(prop, value)

    def _group_named(self, name: str):
        return self.ua.CreateAndCondition(self._is(PROP_CONTROL_TYPE, TYPE_GROUP), self._is(PROP_NAME, name))

    def _find_all(self, element, condition, props: tuple[int, ...] = (), raw: bool = False) -> list:
        """Search below element. raw=True also sees text that only screen readers get
        (such as a session's hidden status line), which the normal search skips."""
        cache = self.ua.CreateCacheRequest()
        if raw:
            cache.TreeFilter = self.ua.RawViewCondition
        for prop in props:
            cache.AddProperty(prop)
        found = element.FindAllBuildCache(SCOPE_DESCENDANTS, condition, cache)
        if not found:
            return []
        return [found.GetElement(index) for index in range(found.Length)]

    def _window(self, hwnd: int):
        return self.ua.ElementFromHandle(hwnd)

    def _rects(self, hwnd: int, condition) -> list[tuple[int, int, int, int]]:
        rects = []
        for element in self._find_all(self._window(hwnd), condition, (PROP_BOUNDS,)):
            rect = element.CachedBoundingRectangle
            rects.append((rect.left, rect.top, rect.right, rect.bottom))
        return rects

    def has_web_tree(self, hwnd: int) -> bool:
        return bool(self._window(hwnd).FindFirst(SCOPE_DESCENDANTS, self._group_named(MAIN_PANE)))

    def buttons(self, hwnd: int) -> list[FoundButton]:
        """Buttons in Claude's main pane and its popups (never the sidebar)."""
        window = self._window(hwnd)
        roots = self._find_all(window, self._group_named(MAIN_PANE))
        roots += self._find_all(window, self._is(PROP_AUTOMATION_ID, POPUPS_ID))
        exclude: list[tuple[int, int, int, int]] = []
        if not roots:
            # Claude's layout changed: search everything, minus the sidebar's column.
            roots = [window]
            exclude = [(l, -(10**7), r, 10**7) for l, _t, r, _b in self._rects(hwnd, self._group_named("Sidebar"))]
        found: list[FoundButton] = []
        for root in roots:
            for element in self._find_all(
                root,
                self._is(PROP_CONTROL_TYPE, TYPE_BUTTON),
                (PROP_NAME, PROP_BOUNDS, PROP_OFFSCREEN, PROP_ENABLED, PROP_ACCELERATOR),
            ):
                try:
                    rect = element.CachedBoundingRectangle
                    button = FoundButton(
                        name=element.CachedName or "",
                        left=rect.left,
                        top=rect.top,
                        right=rect.right,
                        bottom=rect.bottom,
                        offscreen=bool(element.CachedIsOffscreen),
                        enabled=bool(element.CachedIsEnabled),
                        accel=element.CachedAcceleratorKey or "",
                        ref=element,
                    )
                except Exception:  # it vanished while Claude was redrawing; skip it
                    continue
                found.append(button)
        return [b for b in found if not center_inside(b, exclude)]

    def invoke(self, element) -> None:
        """Press a button. Plain buttons support Invoke; toggle buttons (like Claude's
        mic) only Toggle; anything else usually still has an MSAA default action."""
        pattern = element.GetCurrentPattern(PATTERN_INVOKE)
        if pattern:
            pattern.QueryInterface(self.uiac.IUIAutomationInvokePattern).Invoke()
            return
        pattern = element.GetCurrentPattern(PATTERN_TOGGLE)
        if pattern:
            pattern.QueryInterface(self.uiac.IUIAutomationTogglePattern).Toggle()
            return
        pattern = element.GetCurrentPattern(PATTERN_LEGACY)
        if pattern:
            pattern.QueryInterface(self.uiac.IUIAutomationLegacyIAccessiblePattern).DoDefaultAction()
            return
        raise RuntimeError("that button can't be pressed through UI Automation")

    def prompt(self, hwnd: int):
        """Claude's prompt box: the editor named "Prompt", else any ProseMirror editor."""
        elements = self._find_all(
            self._window(hwnd),
            self._is(PROP_CONTROL_TYPE, TYPE_EDIT),
            (PROP_NAME, PROP_CLASS_NAME, PROP_HAS_FOCUS, PROP_OFFSCREEN),
        )
        named = [e for e in elements if (e.CachedName or "") == "Prompt"]
        editors = named or [e for e in elements if "ProseMirror" in (e.CachedClassName or "")]
        if not editors:
            return None
        for element in editors:
            if element.CachedHasKeyboardFocus:
                return element
        visible = [e for e in editors if not e.CachedIsOffscreen]
        return (visible or editors)[0]

    def _any(self, conditions):
        result = conditions[0]
        for condition in conditions[1:]:
            result = self.ua.CreateOrCondition(result, condition)
        return result

    def session_statuses(self, hwnd: int) -> list[SessionStatus]:
        """Each sidebar session that's running, needs you, failed or finished unread."""
        window = self._window(hwnd)
        sidebars = self._find_all(window, self._group_named("Sidebar"), (PROP_BOUNDS,))
        if not sidebars:
            return []

        def height(element):
            rect = element.CachedBoundingRectangle
            return rect.bottom - rect.top

        sidebar = max(sidebars, key=height)
        # A row's status is either text inside a status element, or a status
        # element whose label is its name, so look at both kinds.
        condition = self._any([self._is(PROP_CONTROL_TYPE, t) for t in (TYPE_TEXT, TYPE_STATUS_BAR)])
        walker = self.ua.RawViewWalker
        found = []
        seen_rows: set[tuple] = set()
        for element in self._find_all(sidebar, condition, (PROP_NAME, PROP_CONTROL_TYPE), raw=True):
            try:
                label = " ".join((element.CachedName or "").split())
                kind = element.CachedControlType
            except Exception:  # it vanished while Claude was redrawing; skip it
                continue
            state = SESSION_STATUS_TEXT.get(label)
            if state is None:
                if kind == TYPE_STATUS_BAR and label and label not in IGNORED_STATUS_TEXT:
                    if label not in self._unknown_labels:
                        self._unknown_labels.add(label)
                        log.info("A session's status says '%s', which this app doesn't know yet.", label)
                continue
            row = element
            try:
                for _ in range(7):  # the status sits a few levels inside the session's row
                    row = walker.GetParentElement(row)
                    if not row:
                        break
                    if row.CurrentControlType == TYPE_BUTTON:
                        key = tuple(row.GetRuntimeId())
                        if (key, state) not in seen_rows:
                            seen_rows.add((key, state))
                            found.append(SessionStatus(row.CurrentName or "", state, row))
                        break
            except Exception:  # the row went away mid-read
                continue
        return found

    def current_session_title(self, hwnd: int) -> str | None:
        """The title of the session open in Claude: the sidebar row marked as the current page."""
        window = self._window(hwnd)
        sidebars = self._find_all(window, self._group_named("Sidebar"))
        for sidebar in sidebars:
            for row in self._find_all(sidebar, self._is(PROP_CONTROL_TYPE, TYPE_BUTTON), (PROP_NAME, PROP_ARIA), raw=True):
                if "current=page" in (row.CachedAriaProperties or "").split(";"):
                    return SessionStatus(row.CachedName or "", "running").title
        return None

    def question_answers(self, hwnd: int) -> list[dict]:
        """The answers of a question card on screen, in order (empty if none is open).

        Each is a dict: element, kind ("radio" / "checkbox"), name, checked,
        focused, top, group (which question it belongs to).
        """
        window = self._window(hwnd)
        if self._find_all(window, self._is(PROP_CONTROL_TYPE, TYPE_MENU), raw=True):
            return []  # a menu is open, so these are its items, not answers
        # Only Claude's main pane and its popups: the Claude / Code switch at the
        # top of the window is made of radio buttons too.
        roots = self._find_all(window, self._group_named(MAIN_PANE))
        roots += self._find_all(window, self._is(PROP_AUTOMATION_ID, POPUPS_ID))
        kinds = self._any([self._is(PROP_CONTROL_TYPE, t) for t in (TYPE_RADIO, TYPE_CHECKBOX)])
        props = (PROP_NAME, PROP_ARIA, PROP_OFFSCREEN, PROP_HAS_FOCUS, PROP_CONTROL_TYPE, PROP_BOUNDS)
        walker = self.ua.RawViewWalker
        answers = []
        elements = [e for root in roots for e in self._find_all(root, kinds, props, raw=True)]
        for element in elements:
            try:
                rect = element.CachedBoundingRectangle
                if element.CachedIsOffscreen or rect.right - rect.left <= 0:
                    continue
                aria = element.CachedAriaProperties or ""
                checked = "checked=true" in aria or "selected=true" in aria
                toggle = element.GetCurrentPattern(PATTERN_TOGGLE)
                if toggle and not checked:
                    checked = toggle.QueryInterface(self.uiac.IUIAutomationTogglePattern).CurrentToggleState == 1
                parent = walker.GetParentElement(element)
                answers.append(
                    {
                        "element": element,
                        "kind": "radio" if element.CachedControlType == TYPE_RADIO else "checkbox",
                        "name": " ".join((element.CachedName or "").split())[:60],
                        "checked": checked,
                        "focused": bool(element.CachedHasKeyboardFocus),
                        "top": rect.top,
                        "group": tuple(parent.GetRuntimeId()) if parent else None,
                    }
                )
            except Exception:  # it vanished while Claude was redrawing
                continue
        return answers

    def menu_items(self, hwnd: int) -> list[MenuItem]:
        """Items of the menu that's open right now (Claude renders it as a menu
        control near its button, not in the popup layer)."""
        window = self._window(hwnd)
        kinds = self._any([self._is(PROP_CONTROL_TYPE, t) for t in (TYPE_MENU_ITEM, TYPE_RADIO, TYPE_LIST_ITEM)])
        items = []
        for menu in self._find_all(window, self._is(PROP_CONTROL_TYPE, TYPE_MENU), raw=True):
            for element in self._find_all(menu, kinds, (PROP_NAME, PROP_ARIA), raw=True):
                name = " ".join((element.CachedName or "").split())
                if not name:
                    continue
                aria = element.CachedAriaProperties or ""
                checkable = "checked=" in aria or "selected=" in aria
                checked = "checked=true" in aria or "selected=true" in aria
                toggle = element.GetCurrentPattern(PATTERN_TOGGLE)
                if toggle:
                    checkable = True
                    checked = checked or (
                        toggle.QueryInterface(self.uiac.IUIAutomationTogglePattern).CurrentToggleState == 1
                    )
                selection = element.GetCurrentPattern(PATTERN_SELECTION_ITEM)
                if selection:
                    checkable = True
                    checked = checked or bool(
                        selection.QueryInterface(self.uiac.IUIAutomationSelectionItemPattern).CurrentIsSelected
                    )
                items.append(MenuItem(name, checked, checkable, element))
        return items

    def conversation_point(self, hwnd: int) -> tuple[int, int] | None:
        """A spot over the visible part of the conversation, for the mouse wheel."""
        panes = self._rects(hwnd, self._group_named(MAIN_PANE))
        if not panes:
            return None
        left, top, right, bottom = panes[0]
        chats = self._rects(hwnd, self._group_named(CONVERSATION))
        if chats:
            c_left, c_top, c_right, c_bottom = chats[0]
            left, right = max(left, c_left), min(right, c_right)
            top, bottom = max(top, c_top), min(bottom, c_bottom)
        if right - left < 20 or bottom - top < 20:
            return None
        return (left + right) // 2, top + (bottom - top) * 2 // 5


# ---------------------------------------------------------------- the platform


class WindowsPlatform:
    def __init__(self) -> None:
        _use_physical_pixels()
        self._uia: _Uia | None = None
        self._down: list[Chord] = []
        self._mutex = None
        self._parked: tuple[tuple[int, int], tuple[int, int]] | None = None  # (parked at, put back to)
        self._unpark_at = 0.0

    # -- setup --

    def thread_init(self) -> None:
        _CoInitializeEx(None, 0)  # COINIT_MULTITHREADED, before comtypes is imported
        message = wintypes.MSG()
        _PeekMessageW(ctypes.byref(message), None, 0, 0, 0)  # gives this thread an input queue
        self._uia = _Uia()

    @property
    def uia(self) -> _Uia:
        if self._uia is None:
            self._uia = _Uia()
        return self._uia

    def prepare(self) -> None:
        window = self.find_claude_window()
        if window:
            self.uia.wake(window)

    def acquire_single_instance(self) -> bool:
        handle = _CreateMutexW(None, False, "Local\\ps5-agent-handler")
        if handle and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            _CloseHandle(handle)
            return False
        self._mutex = handle  # keep it open for as long as the app runs
        return True

    def idle(self) -> None:
        if self._parked is not None and time.monotonic() >= self._unpark_at:
            self._unpark()

    # -- windows --

    def find_claude_window(self) -> int | None:
        candidates: list[tuple[bool, int, int]] = []

        @WNDENUMPROC
        def check(hwnd, _lparam):
            if not _IsWindowVisible(hwnd) or _GetWindow(hwnd, GW_OWNER):
                return True
            if _class_name(hwnd) != CLAUDE_WINDOW_CLASS:
                return True
            pid = wintypes.DWORD()
            _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _process_name(pid.value) != CLAUDE_EXE:
                return True
            rect = wintypes.RECT()
            _GetWindowRect(hwnd, ctypes.byref(rect))
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            candidates.append((window_title(hwnd) == "Claude", area, hwnd))
            return True

        _EnumWindows(check, 0)
        if not candidates:
            return None
        return max(candidates)[2]

    def is_foreground(self, window: int) -> bool:
        return _GetForegroundWindow() == window

    def _wait_foreground(self, window: int, seconds: float = 0.3) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.is_foreground(window):
                return True
            time.sleep(0.015)
        return self.is_foreground(window)

    def bring_to_front(self, window: int) -> bool:
        if self.is_foreground(window):
            return True
        if _IsIconic(window):
            _ShowWindow(window, SW_RESTORE)
        _SetForegroundWindow(window)
        if self._wait_foreground(window, 0.1):
            return True
        # Windows only lets the app that has the user's input change the
        # foreground window. Borrow the foreground thread's input for a moment.
        foreground = _GetForegroundWindow()
        their_thread = _GetWindowThreadProcessId(foreground, None) if foreground else 0
        our_thread = _GetCurrentThreadId()
        attached = bool(their_thread and their_thread != our_thread and _AttachThreadInput(our_thread, their_thread, True))
        try:
            _BringWindowToTop(window)
            _SetForegroundWindow(window)
        finally:
            if attached:
                _AttachThreadInput(our_thread, their_thread, False)
        if self._wait_foreground(window):
            return True
        # Last resort: Windows allows the switch while Alt is held.
        _send([_key_event(VK["alt"], False)])
        try:
            _SetForegroundWindow(window)
        finally:
            _send([_key_event(VK["alt"], True)])
        return self._wait_foreground(window)

    # -- keys --

    def send_chord(self, chord: Chord) -> None:
        vks = _chord_vks(chord)
        mods, key = vks[:-1], vks[-1]
        events = [_key_event(vk, False) for vk in mods]
        events += [_key_event(key, False), _key_event(key, True)]
        events += [_key_event(vk, True) for vk in reversed(mods)]
        _send(events)

    def chord_down(self, chord: Chord) -> None:
        _send([_key_event(vk, False) for vk in _chord_vks(chord)])
        self._down.append(chord)

    def chord_up(self, chord: Chord) -> None:
        _send([_key_event(vk, True) for vk in reversed(_chord_vks(chord))])
        if chord in self._down:
            self._down.remove(chord)

    def release_all(self) -> None:
        while self._down:
            self.chord_up(self._down[-1])
        self._unpark()

    # -- on-screen buttons --

    def _ready(self, window: int) -> None:
        """Wake Claude's accessibility tree, and give it a moment if that only just happened."""
        uia = self.uia
        uia.wake(window)
        while uia.recently_woken(window) and not uia.has_web_tree(window):
            time.sleep(0.25)

    def click_button(self, window: int, labels: tuple[str, ...]) -> str | None:
        self._ready(window)
        target = pick_button(self.uia.buttons(window), labels)
        if target is None:
            return None
        self.uia.invoke(target.ref)
        return target.name

    def focus_prompt(self, window: int) -> bool:
        self._ready(window)
        element = self.uia.prompt(window)
        if element is None:
            return False
        element.SetFocus()
        return True

    # -- sessions and effort --

    def session_statuses(self, window: int) -> list[SessionStatus]:
        self.uia.wake(window)
        return self.uia.session_statuses(window)

    def open_session(self, window: int, status: SessionStatus) -> None:
        self.uia.invoke(status.ref)

    # -- question cards ("What would you like to do next?" with options) --

    def _choose(self, answer: dict) -> None:
        """Select a single-choice answer, or flip a tick box."""
        element = answer["element"]
        if answer["kind"] == "radio":
            selection = element.GetCurrentPattern(PATTERN_SELECTION_ITEM)
            if selection:
                selection.QueryInterface(self.uia.uiac.IUIAutomationSelectionItemPattern).Select()
                return
            if answer["checked"]:
                return
        toggle = element.GetCurrentPattern(PATTERN_TOGGLE)
        if toggle:
            toggle.QueryInterface(self.uia.uiac.IUIAutomationTogglePattern).Toggle()
            return
        self.uia.invoke(element)

    def _focus_other_box(self, window: int, answer: dict) -> bool:
        """Put the cursor in the text box that belongs to an "Other" answer (just below it)."""
        uia = self.uia
        top = answer["top"]
        best = None
        for edit in uia._find_all(uia._window(window), uia._is(PROP_CONTROL_TYPE, TYPE_EDIT), (PROP_NAME, PROP_BOUNDS, PROP_OFFSCREEN), raw=True):
            try:
                name = edit.CachedName or ""
                rect = edit.CachedBoundingRectangle
                if edit.CachedIsOffscreen or name in ("Prompt", "Terminal input") or rect.top < top - 5:
                    continue
                distance = rect.top - top
                if best is None or distance < best[0]:
                    best = (distance, edit)
            except Exception:
                continue
        if best is None or best[0] > 400:
            return False
        best[1].SetFocus()
        return True

    def question_step(self, window: int, op: str) -> str | None:
        """Work a question card if one is open; None means there isn't one.

        op: "option_previous" / "option_next" move between answers (a single-choice
        answer gets selected as you move onto it), "option_pick" picks or ticks the
        highlighted answer, "question_previous" / "question_next" jump between
        questions on the card.
        """
        self._ready(window)
        answers = self.uia.question_answers(window)
        if not answers:
            return None
        focused = next((i for i, a in enumerate(answers) if a["focused"]), None)
        checked = next((i for i, a in enumerate(answers) if a["checked"]), None)
        here = focused if focused is not None else checked
        if op == "option_pick":
            answer = answers[here if here is not None else 0]
            self._choose(answer)
            label = answer["name"]
            if label.casefold().startswith("other"):
                if self._focus_other_box(window, answer):
                    return "Picked 'Other'. Type or dictate (Circle) your answer, then Triangle sends it."
            if answer["kind"] == "checkbox":
                return f"{'Unticked' if answer['checked'] else 'Ticked'} '{label}'."
            return f"Picked '{label}'."
        if op in ("question_previous", "question_next"):
            groups: list[list[int]] = []
            for i, answer in enumerate(answers):
                if groups and answers[groups[-1][0]]["group"] == answer["group"]:
                    groups[-1].append(i)
                else:
                    groups.append([i])
            current = next((g for g, members in enumerate(groups) if here in members), 0 if here is None else 0)
            target = max(0, min(len(groups) - 1, current + (1 if op == "question_next" else -1)))
            members = groups[target]
            index = next((i for i in members if answers[i]["checked"]), members[0])
            answers[index]["element"].SetFocus()
            return f"Question {target + 1} of {len(groups)}: '{answers[index]['name']}'."
        step = 1 if op == "option_next" else -1
        if here is None:
            target = 0 if step > 0 else len(answers) - 1
        else:
            target = max(0, min(len(answers) - 1, here + step))
        answer = answers[target]
        answer["element"].SetFocus()
        if answer["kind"] == "radio":
            self._choose(answer)
        return f"Option: '{answer['name']}'."

    def focus_question_other(self, window: int) -> bool:
        """If a question card has "Other" picked, put the cursor in its box (for dictation)."""
        answers = self.uia.question_answers(window)
        for answer in answers:
            if answer["checked"] and answer["name"].casefold().startswith("other"):
                return self._focus_other_box(window, answer)
        return False

    def answer_next_step(self, window: int, answer: str) -> str | None:
        """Answer whatever Claude is waiting on: its next-step box ("Should I...?")
        or a question card (pick an option, then Submit / Skip).

        "yes" presses the card's main button: the one with Claude's Ctrl+Enter
        shortcut (Submit, or the box's first answer). "no" presses "Skip" / "Not
        now" / "No" in that same card, or closes it. Returns what was pressed, or
        None if nothing is waiting.
        """
        self._ready(window)
        uia = self.uia
        props = (PROP_NAME, PROP_ENABLED, PROP_ACCELERATOR)
        mains = [b for b in uia.buttons(window) if b.enabled and "enter" in b.accel.casefold()]
        visible = [b for b in mains if not b.offscreen]
        if visible or mains:
            main = max(visible or mains, key=lambda b: b.bottom)  # the newest card, at the bottom
            if answer == "yes":
                uia.invoke(main.ref)
                return main.name
            # "no": look only inside the same card as that main button.
            walker = uia.ua.RawViewWalker
            around = main.ref
            for _ in range(6):
                around = walker.GetParentElement(around)
                if not around:
                    break
                candidates = [
                    b for b in uia._find_all(around, uia._is(PROP_CONTROL_TYPE, TYPE_BUTTON), props) if b.CachedIsEnabled
                ]
                target = next(
                    (b for label in NO_LABELS for b in candidates if label_matches(b.CachedName or "", label)), None
                ) or next((b for label in DISMISS_LABELS for b in candidates if (b.CachedName or "") == label), None)
                if target is not None:
                    uia.invoke(target)
                    return target.CachedName or "the card's button"
            return None
        boxes = uia._find_all(uia._window(window), uia._group_named(NEXT_STEP_BOX))
        if not boxes:
            return None
        box = boxes[-1]  # the newest one, at the bottom
        buttons = [
            b for b in uia._find_all(box, uia._is(PROP_CONTROL_TYPE, TYPE_BUTTON), props) if b.CachedIsEnabled
        ]
        if answer == "yes":
            main = next((b for b in buttons if "enter" in (b.CachedAcceleratorKey or "").casefold()), None)
            target = main or (buttons[0] if buttons else None)
        else:
            target = next(
                (b for label in NO_LABELS for b in buttons if label_matches(b.CachedName or "", label)), None
            )
            if target is None:
                # No "no" answer: close the box with its X, which sits beside the buttons.
                walker = uia.ua.RawViewWalker
                around = box
                for _ in range(4):
                    around = walker.GetParentElement(around)
                    if not around:
                        break
                    candidates = uia._find_all(around, uia._is(PROP_CONTROL_TYPE, TYPE_BUTTON), props)
                    target = next(
                        (b for label in DISMISS_LABELS for b in candidates if (b.CachedName or "") == label), None
                    )
                    if target is not None:
                        break
        if target is None:
            return None
        uia.invoke(target)
        return target.CachedName or "the box's button"

    def screen_light(self, window: int) -> tuple[str | None, str]:
        """(title, light) for the session open on screen: "attention", "working",
        "done", or "idle" when no session is open (say, the new-session screen)."""
        self.uia.wake(window)
        title = self.uia.current_session_title(window)
        if title is None:
            return None, "idle"
        row_states = [s.state for s in self.uia.session_statuses(window) if s.title == title]
        buttons = [b for b in self.uia.buttons(window) if b.enabled]
        names = [b.name for b in buttons if not b.offscreen]
        approval = any(label_matches(name, label) for name in names for label in APPROVAL_LABELS)
        stop = any(name == "Stop" for name in names)  # Claude's stop button shows while it works
        # Claude's next-step box ("Should I...?" with answer buttons) waiting for you.
        boxes = self.uia._rects(window, self.uia._group_named(NEXT_STEP_BOX))
        question = any(center_inside(b, boxes) for b in buttons) if boxes else False
        return title, light_for_screen(row_states, approval, stop, question)

    def _model_button(self, window: int) -> str:
        """Claude's model button text, such as "Model: Opus 5.5 Medium"."""
        return next((b.name for b in self.uia.buttons(window) if b.name.startswith("Model:")), "")

    def prompt_text(self, window: int) -> str:
        element = self.uia.prompt(window)
        pattern = element.GetCurrentPattern(PATTERN_VALUE) if element else None
        if not pattern:
            return ""
        return pattern.QueryInterface(self.uia.uiac.IUIAutomationValuePattern).CurrentValue or ""

    def step_menu(self, window: int, menu: str, step: int) -> tuple[str, str]:
        """Open one of Claude's menus (the model menu), pick the item below (+1) or
        above (-1) the current one, which also closes it. No other key is pressed.

        Returns (outcome, label): "changed", "same" (already at the end), "unknown"
        (couldn't tell which item is picked; the menu stays open), or "no_menu".
        """
        self._ready(window)
        if not self.is_foreground(window):
            return "no_menu", ""
        hint = self._model_button(window)
        items: list[MenuItem] = []
        for _attempt in range(2):
            # Claude's shortcuts only work while its composer has focus, and after a
            # model change the focus is left on the closed menu, so put it back first.
            self.focus_prompt(window)
            if not self.is_foreground(window):
                return "no_menu", ""
            self.send_chord(MENU_SHORTCUTS[menu])
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                items = self.uia.menu_items(window)
                if items:
                    break
                time.sleep(0.03)
            if items:
                break
        if not items:
            return "no_menu", ""
        outcome, target, label = choose(items, step, hint, menu)
        if target is not None:
            self.uia.invoke(target.ref)  # picking the same item just closes the menu
        return outcome, label

    def step_effort(self, window: int, step: int) -> tuple[str, str]:
        """Change effort with Claude's /effort command, typed into the empty prompt
        box and sent. Claude handles it itself, so no menu opens and nothing goes
        to the model.

        Returns (outcome, label): "changed", "same" (already at the end), "busy"
        (the prompt box has your text in it, so nothing was typed), "unknown"
        (couldn't read the current effort), or "no_control".
        """
        self._ready(window)
        if not self.is_foreground(window):
            return "no_control", ""
        label = self._model_button(window)
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
                if effort_from_label(self._model_button(window)) == level:
                    return "changed", EFFORT_NAMES[level]
                time.sleep(0.05)
            # This model doesn't have that level. If the command is still sitting
            # in the box (it's only ever our own text here), clear it and try the next.
            if self.prompt_text(window).strip() and self.is_foreground(window):
                self.send_chord(Chord(("ctrl",), "a"))
                self.send_chord(Chord((), "backspace"))
        return "same", EFFORT_NAMES[current]

    # -- text --

    def set_clipboard(self, text: str) -> None:
        clipboard_set_text(text)

    def paste_text(self, window: int, text: str, send: bool) -> bool:
        try:
            previous = clipboard_get_text()
        except OSError:
            previous = None
        clipboard_set_text(text)
        try:
            if not self.is_foreground(window):
                raise RuntimeError("Claude lost focus before the text could be pasted")
            self.send_chord(Chord(("ctrl",), "end"))  # after anything already typed
            self.send_chord(Chord(("ctrl",), "v"))
            time.sleep(0.2)
            sent = False
            if send:
                if self.is_foreground(window):
                    self.send_chord(Chord((), "enter"))
                    sent = True
                else:
                    log.warning("Claude lost focus, so the message wasn't sent.")
            time.sleep(0.25)  # let Claude read the clipboard before it's put back
            return sent
        finally:
            if previous is not None:
                try:
                    clipboard_set_text(previous)
                except OSError:
                    log.debug("Couldn't put the old clipboard text back")

    # -- scrolling --

    def scroll_chat(self, window: int, notches: int) -> bool:
        if self._parked is None:
            self._ready(window)
            point = self.uia.conversation_point(window)
            if point is None:
                return False
            saved = _cursor()
            _SetCursorPos(*point)
            self._parked = (point, saved)
        _send([_wheel_event(notches)])
        self._unpark_at = time.monotonic() + CURSOR_PARK_S
        return True

    def _unpark(self) -> None:
        if self._parked is None:
            return
        parked_at, saved = self._parked
        self._parked = None
        if _cursor() == parked_at:  # only if you haven't moved the mouse since
            _SetCursorPos(*saved)

    # -- looking around (for --inspect) --

    def inspect(self, window: int) -> dict:
        uia = self.uia
        if uia.wake(window):
            time.sleep(1.5)
        self._ready(window)
        return {
            "title": window_title(window),
            "buttons": uia.buttons(window),
            "prompt": uia.prompt(window) is not None,
            "conversation": uia.conversation_point(window) is not None,
        }
