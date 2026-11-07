"""Operating-system specific parts. Windows is implemented; the Mac version is planned."""

from __future__ import annotations

import sys


def get_platform(dry_run: bool = False):
    if dry_run:
        from .null import NullPlatform

        return NullPlatform()
    if sys.platform == "win32":
        from .windows import WindowsPlatform

        return WindowsPlatform()
    raise RuntimeError(
        "Only Windows is supported for now (the Mac version is planned). "
        "--test-buttons and --dry-run work everywhere."
    )
