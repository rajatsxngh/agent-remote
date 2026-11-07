"""Command line: run the controller, test the buttons, or look at what Claude is showing."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .config import BUTTONS, GESTURES, Config, ConfigError, load_config

log = logging.getLogger("ps5_agent_handler")

PRETTY = {
    "cross": "Cross", "circle": "Circle", "square": "Square", "triangle": "Triangle",
    "dpad_up": "D-pad up", "dpad_down": "D-pad down", "dpad_left": "D-pad left", "dpad_right": "D-pad right",
    "l1": "L1", "r1": "R1", "l2": "L2", "r2": "R2", "l3": "L3", "r3": "R3",
    "options": "Options", "create": "Create", "ps": "PS", "touchpad": "Touchpad press", "mute": "Mic button",
    "lstick_up": "Left stick up", "lstick_down": "Left stick down",
    "lstick_left": "Left stick left", "lstick_right": "Left stick right",
    "touchpad_swipe": "Touchpad swipe", "right_stick": "Right stick",
}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        prog="ps5-agent",
        description="Drive the Claude desktop app's Code tab with a PS5 DualSense controller.",
    )
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="button map (default: ./config.toml)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test-buttons", action="store_true", help="only show what the controller sends")
    mode.add_argument("--dry-run", action="store_true", help="show what each button would do, without touching Claude")
    mode.add_argument("--inspect", action="store_true", help="list the buttons Claude is showing right now")
    parser.add_argument("-v", "--verbose", action="store_true", help="print debugging details")
    parser.add_argument("--version", action="version", version=f"ps5-agent {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        for noisy in ("comtypes", "faster_whisper", "httpx", "httpx2", "httpcore", "httpcore2"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

    try:
        if args.inspect:
            return inspect_claude()
        if args.test_buttons:
            return test_buttons()
        try:
            config = load_config(args.config)
        except ConfigError as exc:
            print(f"config problem: {exc}")
            return 2
        return run(config, args.config, dry_run=args.dry_run)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------- run


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


def print_banner(config: Config, path: Path, voice, dry_run: bool) -> None:
    note = "   [DRY RUN: nothing is pressed]" if dry_run else ""
    print(f"ps5-agent {__version__}: PS5 controller -> Claude (Code tab){note}")
    print(f"Button map: {path.resolve()} (saved changes load automatically)")
    shown = [b for b in (*BUTTONS, *GESTURES) if b in config.bindings]
    width = max((len(PRETTY[b]) for b in shown), default=0)
    for button in shown:
        action = config.bindings[button]
        label = action.name if action.kind != "builtin" else ""
        detail = action.describe()
        print(f"  {PRETTY[button]:<{width}}  {label + ': ' if label else ''}{detail}")
    print(f"Whisper: {voice.describe()}")
    if config.feedback.agent_status:
        which = "the session on screen" if config.feedback.light_follows == "screen" else "all sessions"
        print(f"Light bar ({which}): red = needs you, yellow = working, blue = done")
    print("Press PS to pause. Press Ctrl+C here to quit.")


def run(config: Config, config_path: Path, dry_run: bool = False) -> int:
    from .controller import Controller
    from .engine import Engine
    from .feedback import Feedback
    from .platform import get_platform
    from .runner import Runner
    from .voice import NoVoice, make_voice

    platform = get_platform(dry_run=dry_run)
    if not platform.acquire_single_instance():
        print("ps5-agent is already running in another window. Close that one first.")
        return 1

    controller = Controller()
    feedback = Feedback(controller, config.feedback)
    runner = Runner(platform, config, feedback, dry_run=dry_run)
    runner.start()
    uses_voice = any(
        action.kind == "builtin" and action.name in ("push_to_talk", "toggle_talk")
        for action in config.bindings.values()
    )
    if not uses_voice:
        voice = NoVoice("no button uses Whisper; map toggle_talk or push_to_talk and restart to load it")
    else:
        try:
            voice = make_voice(config.voice, runner, feedback)
        except Exception as exc:
            log.error("Voice is off: %s", exc)
            voice = NoVoice(str(exc))
    engine = Engine(config, runner, voice, feedback)
    watcher = ConfigWatcher(config_path)

    print_banner(config, config_path, voice, dry_run)
    if controller.connected:
        log.info("Controller: %s", controller.name)
        feedback.request("connected")
    else:
        log.info("Waiting for a controller. Turn it on (Bluetooth) or plug it in.")

    try:
        while True:
            events = controller.poll(timeout_ms=8, threshold=engine.config.settings.trigger_threshold)
            now = time.monotonic()
            for event in events:
                if event[0] == "connected":
                    log.info("Controller connected: %s", event[1])
                elif event[0] == "disconnected":
                    log.info("Controller disconnected.")
                engine.handle(event, now)
            engine.tick(now)
            feedback.drain()
            new_config = watcher.poll(now)
            if new_config is not None:
                if new_config.voice != engine.config.voice:
                    log.info("Voice settings change the next time you start ps5-agent.")
                engine.set_config(new_config)
                runner.set_config(new_config)
                feedback.set_settings(new_config.feedback)
                log.info("Reloaded %s.", config_path)
    except KeyboardInterrupt:
        log.info("Stopping.")
    finally:
        voice.close()
        runner.stop()
        feedback.goodbye()
        controller.close()
    return 0


# ---------------------------------------------------------------- --test-buttons


def test_buttons() -> int:
    from .controller import Controller

    controller = Controller()
    print("Button test: press buttons, pull triggers, swipe the touchpad. Ctrl+C to quit.")
    if controller.connected:
        print(f"Connected: {controller.name}")
    else:
        print("No controller yet. Turn it on (Bluetooth) or plug it in...")
    last_stick = 0.0
    swipe_total = 0.0
    try:
        while True:
            for event in controller.poll(timeout_ms=10, threshold=0.5):
                kind = event[0]
                if kind == "button":
                    print(f"  {event[1]:<10} {'down' if event[2] else 'up'}")
                elif kind == "swipe":
                    swipe_total += event[1]
                    if abs(swipe_total) >= 0.1:
                        print(f"  touchpad   swipe {'down' if swipe_total > 0 else 'up'}")
                        swipe_total = 0.0
                elif kind == "stick":
                    if abs(event[1] - last_stick) >= 0.3:
                        last_stick = event[1]
                        print(f"  right stick {last_stick:+.1f}")
                elif kind == "connected":
                    print(f"Connected: {event[1]}")
                elif kind == "disconnected":
                    print("Disconnected.")
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
    return 0


# ---------------------------------------------------------------- --inspect


def inspect_claude() -> int:
    from .platform import get_platform

    platform = get_platform()
    platform.thread_init()
    window = platform.find_claude_window()
    if not window:
        print("Claude desktop isn't open.")
        return 1
    info = platform.inspect(window)
    print(f"Claude window: {info['title']}")
    print(f"Prompt box: {'found' if info['prompt'] else 'not found'}")
    print(f"Conversation (for scrolling): {'found' if info['conversation'] else 'not found'}")
    counts: dict[str, int] = {}
    accels: dict[str, str] = {}
    for button in info["buttons"]:
        name = " ".join(button.name.split())
        if not name or len(name) > 48:
            continue  # long names are message summaries, not controls
        counts[name] = counts.get(name, 0) + 1
        if button.accel:
            accels[name] = button.accel
    print("Buttons in Claude's main pane and popups:")
    for name, count in counts.items():
        extra = f"   [{accels[name]}]" if name in accels else ""
        times = f"  x{count}" if count > 1 else ""
        print(f"  {name}{extra}{times}")
    print("\nIf Triangle / Square can't find a prompt's buttons, run this while the prompt is")
    print("showing, then put those labels in [actions] approve / reject in config.toml.")
    return 0
