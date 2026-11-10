"""Command line: run the controller, test the buttons, or look at what Claude is showing."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import BUTTONS, GESTURES, Config, ConfigError, load_config

log = logging.getLogger("agent_remote")

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
        prog="agent-remote",
        description="Drive the Claude desktop app's Code tab with a PS5 DualSense controller.",
    )
    parser.add_argument("--config", type=Path, default=None, help="button map (default: config.toml next to the app)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test-buttons", action="store_true", help="only show what the controller sends")
    mode.add_argument("--dry-run", action="store_true", help="show what each button would do, without touching Claude")
    mode.add_argument("--inspect", action="store_true", help="list the buttons Claude is showing right now")
    mode.add_argument("--menubar", action="store_true", help="Mac: run as a menu bar app (the .app does this)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print debugging details")
    parser.add_argument("--version", action="version", version=f"agent-remote {__version__}")
    args = parser.parse_args(argv)
    menubar = args.menubar or (MAC_APP and not (args.inspect or args.test_buttons or args.dry_run))

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if menubar:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]  # a menu bar app has no window
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    if not args.verbose:
        for noisy in ("comtypes", "faster_whisper", "httpx", "httpx2", "httpcore", "httpcore2"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

    try:
        if args.inspect:
            return _finish(inspect_claude())
        if args.test_buttons:
            return test_buttons()
        config_path = args.config or default_config_path()
        try:
            config = load_config(config_path)
        except ConfigError as exc:
            print(f"config problem: {exc}")
            log.error("config problem: %s", exc)
            return _finish(2)
        if menubar:
            from .macapp import run_menubar

            return run_menubar(config, config_path)
        return _finish(run(config, config_path, dry_run=args.dry_run))
    except KeyboardInterrupt:
        return 0


FROZEN = getattr(sys, "frozen", False)  # running as the packaged app
# Running as the Mac .app (agent-remote.app/Contents/MacOS/...): a menu bar app.
MAC_APP = FROZEN and sys.platform == "darwin" and ".app/Contents/MacOS" in sys.executable
MAC_SUPPORT = Path.home() / "Library" / "Application Support" / "agent-remote"
LOG_FILE = Path.home() / "Library" / "Logs" / "agent-remote.log"


def default_config_path() -> Path:
    """Where config.toml lives.

    From source: the current folder. The Windows / Mac folder app: next to the
    program. The Mac .app: ~/Library/Application Support/agent-remote (never inside
    the app itself). The packaged app carries a default copy and puts it there on
    first run, so it's easy to find and edit.
    """
    if not FROZEN:
        return Path("config.toml")
    folder = MAC_SUPPORT if MAC_APP else Path(sys.executable).resolve().parent
    here = folder / "config.toml"
    if not here.exists():
        bundled = Path(getattr(sys, "_MEIPASS", folder)) / "config.toml"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            here.write_bytes(bundled.read_bytes())
            print(f"Made your button map: {here}")
        except OSError:
            return bundled  # read-only folder: use the built-in copy
    return here


def _finish(code: int) -> int:
    """When the packaged app was double-clicked, keep its window open after an error."""
    if FROZEN and code and not MAC_APP:
        try:
            input("\nPress Enter to close.")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


# ---------------------------------------------------------------- run


def print_banner(config: Config, path: Path, voice, dry_run: bool) -> None:
    note = "   [DRY RUN: nothing is pressed]" if dry_run else ""
    print(f"agent-remote {__version__}: PS5 controller -> Claude (Code tab){note}")
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
    from .session import AlreadyRunning, AppSession

    try:
        session = AppSession(config, config_path, dry_run=dry_run)
    except AlreadyRunning as exc:
        print(exc)
        return 1
    print_banner(config, config_path, session.voice, dry_run)
    try:
        while True:
            session.step(timeout_ms=8)
    except KeyboardInterrupt:
        log.info("Stopping.")
    finally:
        session.close()
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
