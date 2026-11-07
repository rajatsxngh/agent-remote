"""What the rest of the app needs from the operating system.

Windows implements this in windows.py. A Mac version would implement the same
methods with AppleScript / the accessibility API.
"""

from __future__ import annotations

from typing import Protocol

from ..keys import Chord


class Platform(Protocol):
    def thread_init(self) -> None:
        """Called once on the action thread before anything else."""

    def prepare(self) -> None:
        """Get ready to work with Claude (for example, wake its accessibility tree)."""

    def acquire_single_instance(self) -> bool:
        """False if the app is already running."""

    def idle(self) -> None:
        """Called by the action thread between actions (and at least every 0.25s)."""

    def find_claude_window(self) -> int | None: ...

    def is_foreground(self, window: int) -> bool: ...

    def bring_to_front(self, window: int) -> bool: ...

    def send_chord(self, chord: Chord) -> None: ...

    def chord_down(self, chord: Chord) -> None: ...

    def chord_up(self, chord: Chord) -> None: ...

    def release_all(self) -> None: ...

    def click_button(self, window: int, labels: tuple[str, ...]) -> str | None:
        """Press the best on-screen button matching labels; return its name, or None."""

    def focus_prompt(self, window: int) -> bool: ...

    def paste_text(self, window: int, text: str, send: bool) -> bool:
        """Paste into the focused prompt box; press Enter if send. True if it was sent."""

    def set_clipboard(self, text: str) -> None: ...

    def scroll_chat(self, window: int, notches: int) -> bool: ...

    def inspect(self, window: int) -> dict: ...
