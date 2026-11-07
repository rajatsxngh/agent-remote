"""Operating-system specific parts: Windows (windows.py) and macOS (mac.py)."""

from __future__ import annotations

import sys


def get_platform(dry_run: bool = False):
    if dry_run:
        from .null import NullPlatform

        return NullPlatform()
    if sys.platform == "win32":
        from .windows import WindowsPlatform

        return WindowsPlatform()
    if sys.platform == "darwin":
        from .mac import MacPlatform

        return MacPlatform()
    raise RuntimeError("Only Windows and macOS are supported. --test-buttons and --dry-run work everywhere.")
