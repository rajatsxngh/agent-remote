"""Load and check config.toml: which controller button runs which action."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .keys import Chord, KeyParseError, parse_keys

BUTTONS = (
    "cross", "circle", "square", "triangle",
    "dpad_up", "dpad_down", "dpad_left", "dpad_right",
    "l1", "r1", "l2", "r2", "l3", "r3",
    "options", "create", "ps", "touchpad", "mute",
    "lstick_up", "lstick_down", "lstick_left", "lstick_right",
)
GESTURES = ("touchpad_swipe", "right_stick")
BUILTINS = {
    "none": "nothing",
    "push_to_talk": "hold to talk, let go to stop",
    "toggle_talk": "press to start talking, press again to stop",
    "pause": "pause / resume the controller",
    "previous_app": "switch app back (Alt+Shift+Tab)",
    "next_app": "switch app (Alt+Tab)",
    "scroll": "scroll the conversation",
    "scroll_up": "scroll the conversation up",
    "scroll_down": "scroll the conversation down",
    "jump_to_attention": "open the session that needs you (press again for the next one)",
    "effort_down": "less effort",
    "effort_up": "more effort",
    "model_previous": "previous model in Claude's model menu",
    "model_next": "next model in Claude's model menu",
}
_SECTIONS = ("buttons", "actions", "settings", "voice", "feedback")
# What a button can do instead while one of Claude's question cards is open.
QUESTION_OPS = ("option_previous", "option_next", "option_pick", "question_previous", "question_next")


class ConfigError(ValueError):
    """Something in config.toml the app can't use."""


@dataclass(frozen=True)
class Action:
    name: str
    kind: str  # "keys", "click" or "builtin"
    chords: tuple[Chord, ...] = ()
    labels: tuple[str, ...] = ()
    fallback: tuple[Chord, ...] = ()
    repeat: bool = False
    focus_prompt: bool = False  # click into Claude's prompt box first
    next_step: str = ""  # if no button matched: "yes" / "no" answers Claude's next-step box
    question: str = ""  # what to do instead while a question card is open (see QUESTION_OPS)

    def describe(self) -> str:
        if self.kind == "keys":
            text = " then ".join(str(chord) for chord in self.chords)
            if self.question:
                text += f" (on a question card: {self.question.replace('_', ' ')})"
            return text
        if self.kind == "click":
            text = "press " + " / ".join(f"'{label}'" for label in self.labels)
            if self.next_step == "yes":
                text += ", or say yes in Claude's next-step box"
            elif self.next_step == "no":
                text += ", or 'Not now' / close Claude's next-step box"
            return text
        return BUILTINS[self.name]


@dataclass(frozen=True)
class Settings:
    bring_claude_to_front: bool = True
    trigger_threshold: float = 0.5
    stick_deadzone: float = 0.25
    scroll_speed: float = 1.0
    invert_scroll: bool = False
    app_switch_delay: float = 0.8
    log_buttons: bool = True


@dataclass(frozen=True)
class VoiceSettings:
    engine: str = "whisper"
    model: str = "small.en"
    language: str = "en"
    device: str = "cpu"
    compute_type: str = "int8"
    microphone: str = ""
    keep_mic_open: bool = False
    auto_send: bool = True
    min_seconds: float = 0.4
    max_seconds: float = 120.0
    hint_words: str = ""
    claude_keys: str = "shift+f10"


@dataclass(frozen=True)
class FeedbackSettings:
    rumble: bool = True
    lightbar: bool = True
    agent_status: bool = True  # light bar and rumble follow what your Claude sessions are doing
    light_follows: str = "screen"  # "screen": the session open in Claude; "all": every session
    buzz_needs_you: bool = True  # buzz when the session starts needing you
    buzz_done: bool = False  # buzz when it finishes
    buzz_nothing: bool = False  # low buzz when a button found nothing to do


@dataclass(frozen=True)
class Config:
    settings: Settings
    voice: VoiceSettings
    feedback: FeedbackSettings
    bindings: dict[str, Action]


def load_config(path: Path | str) -> Config:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"can't find {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} isn't valid TOML: {exc}") from None
    return parse_config(data)


def parse_config(data: dict[str, Any]) -> Config:
    unknown = sorted(set(data) - set(_SECTIONS))
    if unknown:
        raise ConfigError(
            f"unknown section [{unknown[0]}]. Sections: {', '.join(f'[{s}]' for s in _SECTIONS)}"
        )
    settings = _section(Settings, data.get("settings"), "settings")
    voice = _section(VoiceSettings, data.get("voice"), "voice")
    feedback = _section(FeedbackSettings, data.get("feedback"), "feedback")
    _check_values(settings, voice)
    if feedback.light_follows not in ("screen", "all"):
        raise ConfigError("[feedback] light_follows must be \"screen\" or \"all\"")

    raw_actions = data.get("actions", {})
    if not isinstance(raw_actions, dict):
        raise ConfigError("[actions] must be a table")
    actions: dict[str, Action] = {}
    for name, spec in raw_actions.items():
        if name in BUILTINS:
            raise ConfigError(f"[actions] can't redefine the built-in action '{name}'")
        actions[name] = _parse_action(name, spec, f"[actions] {name}")

    raw_buttons = data.get("buttons", {})
    if not isinstance(raw_buttons, dict):
        raise ConfigError("[buttons] must be a table")
    bindings: dict[str, Action] = {}
    for button, value in raw_buttons.items():
        where = f"[buttons] {button}"
        if button not in BUTTONS and button not in GESTURES:
            raise ConfigError(
                f"{where}: '{button}' isn't a controller button. "
                f"Buttons: {', '.join(BUTTONS + GESTURES)}"
            )
        if isinstance(value, dict):
            action = _parse_action(button, value, where)
        elif isinstance(value, str):
            if value in BUILTINS:
                action = Action(value, "builtin")
            elif value in actions:
                action = actions[value]
            else:
                raise ConfigError(
                    f"{where} = '{value}' isn't an action. Define it under [actions], "
                    f"or use a built-in: {', '.join(BUILTINS)}"
                )
        else:
            raise ConfigError(f"{where} must be an action name in quotes or {{ keys = \"...\" }}")

        is_scroll = action.kind == "builtin" and action.name == "scroll"
        is_none = action.kind == "builtin" and action.name == "none"
        if button in GESTURES and not (is_scroll or is_none):
            raise ConfigError(f"{where} can only be \"scroll\" or \"none\"")
        if button in BUTTONS and is_scroll:
            raise ConfigError(f"{where}: for a button, use \"scroll_up\" or \"scroll_down\"")
        if not is_none:
            bindings[button] = action
    return Config(settings, voice, feedback, bindings)


def _section(cls, raw: Any, name: str):
    defaults = cls()
    if raw is None:
        return defaults
    if not isinstance(raw, dict):
        raise ConfigError(f"[{name}] must be a table")
    known = {field.name for field in fields(cls)}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known:
            raise ConfigError(
                f"[{name}] has an unknown setting '{key}'. Settings: {', '.join(sorted(known))}"
            )
        default = getattr(defaults, key)
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"[{name}] {key} must be true or false")
        elif isinstance(default, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"[{name}] {key} must be a number")
            value = float(value)
        elif isinstance(default, str) and not isinstance(value, str):
            raise ConfigError(f"[{name}] {key} must be text in quotes")
        values[key] = value
    return dataclasses.replace(defaults, **values)


def _check_values(settings: Settings, voice: VoiceSettings) -> None:
    if not 0.05 <= settings.trigger_threshold <= 0.95:
        raise ConfigError("[settings] trigger_threshold must be between 0.05 and 0.95")
    if not 0.0 <= settings.stick_deadzone <= 0.9:
        raise ConfigError("[settings] stick_deadzone must be between 0 and 0.9")
    if settings.scroll_speed <= 0:
        raise ConfigError("[settings] scroll_speed must be more than 0")
    if not 0.1 <= settings.app_switch_delay <= 5:
        raise ConfigError("[settings] app_switch_delay must be between 0.1 and 5 seconds")
    if voice.engine not in ("whisper", "claude"):
        raise ConfigError("[voice] engine must be \"whisper\" or \"claude\"")
    if voice.device not in ("cpu", "cuda", "auto"):
        raise ConfigError("[voice] device must be \"cpu\", \"cuda\" or \"auto\"")
    if voice.min_seconds < 0 or voice.max_seconds <= voice.min_seconds:
        raise ConfigError("[voice] max_seconds must be more than min_seconds")
    try:
        parse_keys(voice.claude_keys)
    except KeyParseError as exc:
        raise ConfigError(f"[voice] claude_keys: {exc}") from None


def _parse_action(name: str, spec: Any, where: str) -> Action:
    if not isinstance(spec, dict):
        raise ConfigError(f"{where} must look like {{ keys = \"ctrl+n\" }} or {{ click = [\"Deny\"] }}")
    extra = sorted(set(spec) - {"keys", "click", "fallback_keys", "repeat", "focus_prompt", "next_step", "question"})
    if extra:
        raise ConfigError(f"{where} has unknown fields: {', '.join(extra)}")
    if ("keys" in spec) == ("click" in spec):
        raise ConfigError(f"{where} needs either keys = \"...\" or click = [...], not both")
    repeat = spec.get("repeat", False)
    focus_prompt = spec.get("focus_prompt", False)
    if not isinstance(repeat, bool) or not isinstance(focus_prompt, bool):
        raise ConfigError(f"{where}: repeat and focus_prompt must be true or false")
    question = spec.get("question", "")
    if question not in ("", *QUESTION_OPS):
        raise ConfigError(f"{where}: question must be one of {', '.join(QUESTION_OPS)}")
    try:
        if "keys" in spec:
            if "fallback_keys" in spec:
                raise ConfigError(f"{where}: fallback_keys only goes with click")
            return Action(
                name,
                "keys",
                chords=parse_keys(spec["keys"]),
                repeat=repeat,
                focus_prompt=focus_prompt,
                question=question,
            )
        labels = spec["click"]
        if isinstance(labels, str):
            labels = [labels]
        if not isinstance(labels, list) or not labels or not all(
            isinstance(label, str) and label.strip() for label in labels
        ):
            raise ConfigError(f"{where}: click must be a list of button labels")
        fallback = parse_keys(spec["fallback_keys"]) if "fallback_keys" in spec else ()
        next_step = spec.get("next_step", "")
        if next_step not in ("", "yes", "no"):
            raise ConfigError(f"{where}: next_step must be \"yes\" or \"no\"")
        return Action(
            name,
            "click",
            labels=tuple(label.strip() for label in labels),
            fallback=fallback,
            repeat=repeat,
            focus_prompt=focus_prompt,
            next_step=next_step,
            question=question,
        )
    except KeyParseError as exc:
        raise ConfigError(f"{where}: {exc}") from None
