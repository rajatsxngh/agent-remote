"""Mac keyboard details: Claude's Windows shortcuts in their Mac form, and key codes.

No Mac-only imports, so this can be tested anywhere.

Claude's docs: on Windows, Cmd is Ctrl and Cmd+Ctrl is Ctrl+Alt; shortcuts that
already use Ctrl (Ctrl+Tab, Ctrl+`, Ctrl+O) are the same on both.
"""

from __future__ import annotations

from ..keys import Chord

# Shortcuts that are Ctrl on the Mac too.
STAYS_CTRL = {"tab", "`", "o"}
MOD_ORDER = ("ctrl", "alt", "shift", "cmd")

# Quartz event flags for each modifier.
FLAGS = {"cmd": 0x100000, "shift": 0x20000, "ctrl": 0x40000, "alt": 0x80000}

# Mac virtual key codes (US layout).
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26,
    "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
    "enter": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43,
    "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50,
    "backspace": 51, "escape": 53,
    "cmd": 55, "shift": 56, "alt": 58, "ctrl": 59,
    "f5": 96, "f6": 97, "f7": 98, "f3": 99, "f8": 100, "f9": 101, "f11": 103,
    "f10": 109, "f12": 111, "insert": 114, "home": 115, "pageup": 116,
    "delete": 117, "f4": 118, "end": 119, "f2": 120, "pagedown": 121, "f1": 122,
    "left": 123, "right": 124, "down": 125, "up": 126,
}


def mac_chord(chord: Chord) -> tuple[tuple[str, ...], str]:
    """A Windows-style chord in its Mac form: (modifiers, key)."""
    mods = set(chord.mods)
    key = chord.key
    if key == "alt" and not mods:
        return (), "cmd"  # the app switcher holds Alt on Windows, Cmd on the Mac
    if "win" in mods:
        mods.discard("win")
        mods.add("cmd")
    if "ctrl" in mods and "alt" in mods:
        mods -= {"ctrl", "alt"}
        mods |= {"cmd", "ctrl"}
    elif "ctrl" in mods and key not in STAYS_CTRL:
        mods.discard("ctrl")
        mods.add("cmd")
    if "cmd" in mods and key in ("end", "home"):
        key = "down" if key == "end" else "up"  # end / start of a text box
    return tuple(m for m in MOD_ORDER if m in mods), key


def keycode_for(key: str) -> int:
    if key in KEYCODES:
        return KEYCODES[key]
    raise ValueError(f"no Mac key code for '{key}'")


def flags_for(mods: tuple[str, ...]) -> int:
    value = 0
    for mod in mods:
        value |= FLAGS[mod]
    return value
