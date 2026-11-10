"""Shortcut strings such as "ctrl+shift+[" turned into platform-neutral chords."""

from __future__ import annotations

from dataclasses import dataclass

_MODIFIERS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "option": "alt",
    "win": "win",
    "windows": "win",
    "cmd": "win",
    "meta": "win",
    "super": "win",
}
MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")

_NAMED_KEYS = {
    "enter": "enter",
    "return": "enter",
    "esc": "escape",
    "escape": "escape",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "ins": "insert",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pgup": "pageup",
    "pagedown": "pagedown",
    "pgdn": "pagedown",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "minus": "-",
    "equals": "=",
    "apps": "apps",
    "menu": "apps",
}
_FUNCTION_KEYS = {f"f{i}" for i in range(1, 25)}
PUNCTUATION = "`-=[]\\;',./"


class KeyParseError(ValueError):
    """A shortcut string the app can't understand."""


@dataclass(frozen=True)
class Chord:
    """One key press, with the modifiers held down while it happens."""

    mods: tuple[str, ...]
    key: str

    def __str__(self) -> str:
        return "+".join((*self.mods, self.key))


def _canonical_key(raw: str, whole: str) -> str:
    if raw in _NAMED_KEYS:
        return _NAMED_KEYS[raw]
    if raw in _FUNCTION_KEYS:
        return raw
    if raw in _MODIFIERS:
        return _MODIFIERS[raw]
    if len(raw) == 1 and ((raw.isascii() and raw.isalnum()) or raw in PUNCTUATION):
        return raw
    raise KeyParseError(f"unknown key '{raw}' in shortcut '{whole}'")


def parse_chord(text: str) -> Chord:
    """Read one shortcut, for example "ctrl+shift+]" or "esc"."""
    if not isinstance(text, str) or not text.strip():
        raise KeyParseError("empty shortcut")
    parts = [part.strip().lower() for part in text.strip().split("+")]
    if any(not part for part in parts):
        raise KeyParseError(
            f"can't read shortcut '{text}' (write 'equals' or 'minus' for those keys)"
        )
    *mod_parts, key_part = parts
    mods: set[str] = set()
    for part in mod_parts:
        if part not in _MODIFIERS:
            raise KeyParseError(f"unknown modifier '{part}' in shortcut '{text}'")
        mods.add(_MODIFIERS[part])
    key = _canonical_key(key_part, text)
    return Chord(tuple(m for m in MODIFIER_ORDER if m in mods), key)


def parse_keys(value: str | list[str]) -> tuple[Chord, ...]:
    """Read a shortcut or a list of shortcuts that are pressed one after another."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise KeyParseError("keys must be a shortcut in quotes, or a list of them")
    return tuple(parse_chord(item) for item in value)
