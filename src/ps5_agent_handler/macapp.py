"""The Mac menu bar app: a 🎮 in the menu bar instead of a Terminal window.

The controller loop runs on a timer in the app's main run loop (SDL wants the
main thread on the Mac). The menu shows the controller and light bar status,
and has Pause, Open button map, Open log, Start at login and Quit.

Not yet tested on a Mac.
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import Config

log = logging.getLogger("ps5_agent_handler")

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.rajatsinghh.ps5-agent.plist"
LIGHT_WORDS = {
    "working": "Claude is working",
    "attention": "Claude needs you",
    "done": "Claude is done",
    "idle": "No session open",
    "paused": "Paused",
}
TITLES = {"attention": "🎮❗", "paused": "🎮⏸"}


def login_item_enabled() -> bool:
    return LAUNCH_AGENT.exists()


def set_login_item(enabled: bool) -> None:
    """Start ps5-agent when you log in (a LaunchAgent pointing at this app)."""
    if not enabled:
        LAUNCH_AGENT.unlink(missing_ok=True)
        return
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": "com.rajatsinghh.ps5-agent",
        "ProgramArguments": [sys.executable],
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }
    LAUNCH_AGENT.write_bytes(plistlib.dumps(plist))


def run_menubar(config: Config, config_path: Path) -> int:
    import AppKit
    import Foundation
    import objc

    from .cli import LOG_FILE
    from .session import AlreadyRunning, AppSession

    try:
        session = AppSession(config, config_path)
    except AlreadyRunning as exc:
        log.error("%s", exc)
        return 1

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # menu bar only, no Dock icon

    class Delegate(AppKit.NSObject):
        def init(self):
            self = objc.super(Delegate, self).init()
            if self is None:
                return None
            bar = AppKit.NSStatusBar.systemStatusBar()
            self.item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
            self.item.button().setTitle_("🎮")
            menu = AppKit.NSMenu.alloc().init()
            self.status_line = self._add(menu, "Starting…", None)
            self.status_line.setEnabled_(False)
            self.light_line = self._add(menu, "", None)
            self.light_line.setEnabled_(False)
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self.pause_item = self._add(menu, "Pause controller", "togglePause:")
            self._add(menu, "Open button map", "openConfig:")
            self._add(menu, "Open log", "openLog:")
            self.login_item = self._add(menu, "Start at login", "toggleLogin:")
            self.login_item.setState_(1 if login_item_enabled() else 0)
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self._add(menu, "Quit ps5-agent", "quit:")
            self.item.setMenu_(menu)
            self.ticks = 0
            return self

        def _add(self, menu, title, action):
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            if action:
                item.setTarget_(self)
            menu.addItem_(item)
            return item

        def tick_(self, _timer):
            try:
                session.step(timeout_ms=0)
            except Exception:
                log.exception("Controller loop failed")
            self.ticks += 1
            if self.ticks % 25 == 0:  # refresh the menu text a few times a second
                self.refresh()

        def refresh(self):
            controller = session.controller
            self.status_line.setTitle_(
                f"Controller: {controller.name}" if controller.connected else "Controller: waiting (turn it on)"
            )
            light = session.feedback.color_name
            key = "paused" if session.engine.paused else light
            self.light_line.setTitle_(f"Light bar: {LIGHT_WORDS.get(key, key)}")
            self.pause_item.setTitle_("Resume controller" if session.engine.paused else "Pause controller")
            self.item.button().setTitle_(TITLES.get(key, "🎮"))

        def togglePause_(self, _sender):
            session.toggle_pause()
            self.refresh()

        def openConfig_(self, _sender):
            subprocess.run(["open", "-t", str(config_path)], check=False)

        def openLog_(self, _sender):
            subprocess.run(["open", "-a", "Console", str(LOG_FILE)], check=False)

        def toggleLogin_(self, sender):
            enabled = not login_item_enabled()
            set_login_item(enabled)
            sender.setState_(1 if enabled else 0)

        def quit_(self, _sender):
            AppKit.NSApplication.sharedApplication().terminate_(self)

        def applicationWillTerminate_(self, _notification):
            log.info("Stopping.")
            session.close()

    delegate = Delegate.alloc().init()
    app.setDelegate_(delegate)
    timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.008, delegate, "tick:", None, True
    )
    Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(timer, Foundation.NSRunLoopCommonModes)
    log.info("ps5-agent is running in the menu bar. Button map: %s", config_path)
    app.run()
    return 0
