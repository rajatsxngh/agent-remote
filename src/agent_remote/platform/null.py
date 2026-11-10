"""A platform that does nothing, for --dry-run (works on any operating system)."""

from __future__ import annotations


class NullPlatform:
    def thread_init(self) -> None:
        pass

    def prepare(self) -> None:
        pass

    def acquire_single_instance(self) -> bool:
        return True

    def idle(self) -> None:
        pass

    def release_all(self) -> None:
        pass

    def set_clipboard(self, text: str) -> None:
        pass
