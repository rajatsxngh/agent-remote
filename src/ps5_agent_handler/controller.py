"""Read the DualSense through SDL2 and turn it into simple events.

poll() returns a list of tuples:
  ("connected", name) / ("disconnected", name)
  ("button", button_name, pressed)
  ("swipe", dy)    the touchpad finger moved down (+) or up (-), in touchpad heights
  ("stick", y)     right stick up/down position, -1 (up) to 1 (down)
"""

from __future__ import annotations

import ctypes
import logging
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore")  # pysdl2-dll announces which SDL2 build it loaded
    import sdl2

from .gestures import StickFlick, SwipeTracker, TriggerButton

log = logging.getLogger(__name__)

BUTTON_NAMES = {
    sdl2.SDL_CONTROLLER_BUTTON_A: "cross",
    sdl2.SDL_CONTROLLER_BUTTON_B: "circle",
    sdl2.SDL_CONTROLLER_BUTTON_X: "square",
    sdl2.SDL_CONTROLLER_BUTTON_Y: "triangle",
    sdl2.SDL_CONTROLLER_BUTTON_BACK: "create",
    sdl2.SDL_CONTROLLER_BUTTON_GUIDE: "ps",
    sdl2.SDL_CONTROLLER_BUTTON_START: "options",
    sdl2.SDL_CONTROLLER_BUTTON_LEFTSTICK: "l3",
    sdl2.SDL_CONTROLLER_BUTTON_RIGHTSTICK: "r3",
    sdl2.SDL_CONTROLLER_BUTTON_LEFTSHOULDER: "l1",
    sdl2.SDL_CONTROLLER_BUTTON_RIGHTSHOULDER: "r1",
    sdl2.SDL_CONTROLLER_BUTTON_DPAD_UP: "dpad_up",
    sdl2.SDL_CONTROLLER_BUTTON_DPAD_DOWN: "dpad_down",
    sdl2.SDL_CONTROLLER_BUTTON_DPAD_LEFT: "dpad_left",
    sdl2.SDL_CONTROLLER_BUTTON_DPAD_RIGHT: "dpad_right",
    sdl2.SDL_CONTROLLER_BUTTON_MISC1: "mute",
    sdl2.SDL_CONTROLLER_BUTTON_TOUCHPAD: "touchpad",
}
TRIGGERS = {
    sdl2.SDL_CONTROLLER_AXIS_TRIGGERLEFT: "l2",
    sdl2.SDL_CONTROLLER_AXIS_TRIGGERRIGHT: "r2",
}
AXIS_MAX = 32767.0
_TOUCH_EVENTS = (
    sdl2.SDL_CONTROLLERTOUCHPADDOWN,
    sdl2.SDL_CONTROLLERTOUCHPADMOTION,
    sdl2.SDL_CONTROLLERTOUCHPADUP,
)


def _sdl_error() -> str:
    return sdl2.SDL_GetError().decode(errors="replace")


class Controller:
    """Owns SDL and the open controller. Call poll() often, always from the same thread."""

    def __init__(self) -> None:
        # This app never has a window of its own, so SDL must report the
        # controller while other apps (Claude) are in front.
        sdl2.SDL_SetHint(sdl2.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1")
        # Over Bluetooth the DualSense ignores light bar and rumble commands until
        # it's switched to its extended reports, which this asks SDL to do.
        sdl2.SDL_SetHint(sdl2.SDL_HINT_JOYSTICK_HIDAPI_PS5_RUMBLE, b"1")
        if sdl2.SDL_Init(sdl2.SDL_INIT_GAMECONTROLLER) != 0:
            raise RuntimeError(f"couldn't start SDL: {_sdl_error()}")
        self._pad = None
        self._instance: int | None = None
        self.name = ""
        self._event = sdl2.SDL_Event()
        self._triggers = {name: TriggerButton() for name in TRIGGERS.values()}
        self._swipe = SwipeTracker()
        self._left_stick = StickFlick("lstick")
        self._open_any()

    @property
    def connected(self) -> bool:
        return self._pad is not None

    def _open_any(self) -> bool:
        for index in range(sdl2.SDL_NumJoysticks()):
            if sdl2.SDL_IsGameController(index) and self._open(index):
                return True
        return False

    def _open(self, index: int) -> bool:
        pad = sdl2.SDL_GameControllerOpen(index)
        if not pad:
            log.warning("Couldn't open controller %d: %s", index, _sdl_error())
            return False
        self._pad = pad
        self._instance = sdl2.SDL_JoystickInstanceID(sdl2.SDL_GameControllerGetJoystick(pad))
        raw_name = sdl2.SDL_GameControllerName(pad)
        self.name = raw_name.decode(errors="replace") if raw_name else "Controller"
        if sdl2.SDL_GameControllerGetType(pad) != sdl2.SDL_CONTROLLER_TYPE_PS5:
            log.info(
                "%s isn't a DualSense, so buttons go by position (bottom = cross, right = circle...).",
                self.name,
            )
        return True

    def _close_pad(self) -> None:
        if self._pad is not None:
            sdl2.SDL_GameControllerClose(self._pad)
        self._pad = None
        self._instance = None
        for trigger in self._triggers.values():
            trigger.down = False
        self._swipe.up()
        self._left_stick.reset()

    def poll(self, timeout_ms: int, threshold: float) -> list[tuple]:
        """Wait up to timeout_ms for input, then return everything that happened."""
        events: list[tuple] = []
        event = self._event
        got = sdl2.SDL_WaitEventTimeout(ctypes.byref(event), timeout_ms)
        while got:
            self._translate(event, threshold, events)
            got = sdl2.SDL_PollEvent(ctypes.byref(event))
        return events

    def _translate(self, event, threshold: float, out: list[tuple]) -> None:
        kind = event.type
        if kind == sdl2.SDL_CONTROLLERDEVICEADDED:
            # cdevice.which is a device index here (SDL also sends this for
            # controllers that were already connected at start-up).
            if self._pad is None and self._open(event.cdevice.which):
                out.append(("connected", self.name))
            return
        if kind == sdl2.SDL_CONTROLLERDEVICEREMOVED:
            # ...and an instance id here.
            if event.cdevice.which == self._instance:
                name = self.name
                self._close_pad()
                out.append(("disconnected", name))
                if self._open_any():
                    out.append(("connected", self.name))
            return
        if self._pad is None:
            return
        if kind in (sdl2.SDL_CONTROLLERBUTTONDOWN, sdl2.SDL_CONTROLLERBUTTONUP):
            if event.cbutton.which == self._instance:
                name = BUTTON_NAMES.get(event.cbutton.button)
                if name:
                    out.append(("button", name, kind == sdl2.SDL_CONTROLLERBUTTONDOWN))
        elif kind == sdl2.SDL_CONTROLLERAXISMOTION:
            if event.caxis.which != self._instance:
                return
            axis = event.caxis.axis
            value = event.caxis.value / AXIS_MAX
            if axis in TRIGGERS:
                name = TRIGGERS[axis]
                change = self._triggers[name].update(max(0.0, value), threshold)
                if change is not None:
                    out.append(("button", name, change))
            elif axis == sdl2.SDL_CONTROLLER_AXIS_RIGHTY:
                out.append(("stick", max(-1.0, min(1.0, value))))
            elif axis in (sdl2.SDL_CONTROLLER_AXIS_LEFTX, sdl2.SDL_CONTROLLER_AXIS_LEFTY):
                value = max(-1.0, min(1.0, value))
                if axis == sdl2.SDL_CONTROLLER_AXIS_LEFTX:
                    changes = self._left_stick.update(x=value)
                else:
                    changes = self._left_stick.update(y=value)
                for name, pressed in changes:
                    out.append(("button", name, pressed))
        elif kind in _TOUCH_EVENTS:
            touch = event.ctouchpad
            if touch.which != self._instance or touch.touchpad != 0 or touch.finger != 0:
                return
            if kind == sdl2.SDL_CONTROLLERTOUCHPADDOWN:
                self._swipe.down(touch.y)
            elif kind == sdl2.SDL_CONTROLLERTOUCHPADMOTION:
                dy = self._swipe.move(touch.y)
                if dy:
                    out.append(("swipe", dy))
            else:
                self._swipe.up()

    def rumble(self, low: int, high: int, ms: int) -> None:
        if self._pad is not None:
            sdl2.SDL_GameControllerRumble(self._pad, low, high, ms)

    def set_led(self, rgb: tuple[int, int, int]) -> None:
        if self._pad is not None:
            sdl2.SDL_GameControllerSetLED(self._pad, *rgb)

    def close(self) -> None:
        self._close_pad()
        sdl2.SDL_Quit()
