"""One running copy of the app: controller, actions, light bar and config reloading.

The Terminal version calls step() in a loop; the Mac menu bar app calls it from a
timer. Both share everything else.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import Config, ConfigError, load_config

log = logging.getLogger("ps5_agent_handler")


class AlreadyRunning(RuntimeError):
    pass


class ConfigWatcher:
    """Reloads config.toml after you save it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime = self._stat()
        self._next_check = 0.0

    def _stat(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def poll(self, now: float) -> Config | None:
        if now < self._next_check:
            return None
        self._next_check = now + 1.0
        mtime = self._stat()
        if mtime is None or mtime == self._mtime:
            return None
        self._mtime = mtime
        try:
            return load_config(self.path)
        except ConfigError as exc:
            log.error("config.toml has a problem, so the old settings stay: %s", exc)
            return None


class AppSession:
    def __init__(self, config: Config, config_path: Path, dry_run: bool = False) -> None:
        from .controller import Controller
        from .engine import Engine
        from .feedback import Feedback
        from .platform import get_platform
        from .runner import Runner
        from .voice import NoVoice, make_voice

        self.config_path = config_path
        self.dry_run = dry_run
        self.platform = get_platform(dry_run=dry_run)
        if not self.platform.acquire_single_instance():
            raise AlreadyRunning("ps5-agent is already running. Close that one first.")
        self.controller = Controller()
        self.feedback = Feedback(self.controller, config.feedback)
        self.runner = Runner(self.platform, config, self.feedback, dry_run=dry_run)
        self.runner.start()
        uses_voice = any(
            action.kind == "builtin" and action.name in ("push_to_talk", "toggle_talk")
            for action in config.bindings.values()
        )
        if not uses_voice:
            self.voice = NoVoice("no button uses Whisper; map toggle_talk or push_to_talk and restart to load it")
        else:
            try:
                self.voice = make_voice(config.voice, self.runner, self.feedback)
            except Exception as exc:
                log.error("Voice is off: %s", exc)
                self.voice = NoVoice(str(exc))
        self.engine = Engine(config, self.runner, self.voice, self.feedback)
        self.watcher = ConfigWatcher(config_path)
        if self.controller.connected:
            log.info("Controller: %s", self.controller.name)
            self.feedback.request("connected")
        else:
            log.info("Waiting for a controller. Turn it on (Bluetooth) or plug it in.")

    @property
    def config(self) -> Config:
        return self.engine.config

    def step(self, timeout_ms: int = 8) -> None:
        """Read the controller once, run what it asked for, update the light bar."""
        events = self.controller.poll(timeout_ms=timeout_ms, threshold=self.config.settings.trigger_threshold)
        now = time.monotonic()
        for event in events:
            if event[0] == "connected":
                log.info("Controller connected: %s", event[1])
            elif event[0] == "disconnected":
                log.info("Controller disconnected.")
            self.engine.handle(event, now)
        self.engine.tick(now)
        self.feedback.drain()
        new_config = self.watcher.poll(now)
        if new_config is not None:
            if new_config.voice != self.config.voice:
                log.info("Voice settings change the next time you start ps5-agent.")
            self.engine.set_config(new_config)
            self.runner.set_config(new_config)
            self.feedback.set_settings(new_config.feedback)
            log.info("Reloaded %s.", self.config_path)

    def toggle_pause(self) -> None:
        self.engine.toggle_pause()

    def close(self) -> None:
        self.voice.close()
        self.runner.stop()
        self.feedback.goodbye()
        self.controller.close()
