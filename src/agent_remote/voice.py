"""Push-to-talk: record while R2 is held, turn speech into text, then hand it to the runner."""

from __future__ import annotations

import collections
import logging
import os
import queue
import threading
import time

import numpy as np

from .keys import parse_keys
from .runner import Hold, Paste

log = logging.getLogger(__name__)

RATE = 16000  # what Whisper expects
SILENCE_RMS = 0.0015  # quieter than this is treated as no speech at all
LIVE_RMS = 1e-4  # a just-opened mic counts as live once it picks up any room noise
WARMUP_LIMIT_S = 0.8  # ...or after this long, whatever it hears
PREROLL_S = 0.3  # with keep_mic_open, also keep this much from just before you pressed
WASAPI = "Windows WASAPI"


def make_voice(vcfg, runner, feedback):
    if vcfg.engine == "claude":
        return ClaudeVoice(vcfg, runner, feedback)
    return WhisperVoice(vcfg, runner, feedback)


class NoVoice:
    """Stands in when voice couldn't start, so the buttons still work."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def describe(self) -> str:
        return f"off ({self.reason})"

    def start(self) -> None:
        log.warning("Voice is off: %s", self.reason)

    def stop(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        pass


class ClaudeVoice:
    """Holds Claude's own "Press and hold to record" shortcut while you hold the button."""

    def __init__(self, vcfg, runner, feedback) -> None:
        self.chords = parse_keys(vcfg.claude_keys)
        self.runner = runner
        self.feedback = feedback
        self.active = False

    def describe(self) -> str:
        return "Claude's own dictation (holds " + " then ".join(map(str, self.chords)) + ")"

    def start(self) -> None:
        if not self.active:
            self.active = True
            self.runner.submit(Hold(self.chords, True))
            self.feedback.request("listening")

    def stop(self) -> None:
        if self.active:
            self.active = False
            self.runner.submit(Hold(self.chords, False))
            self.feedback.request("voice_done")

    def cancel(self) -> None:
        self.stop()

    def close(self) -> None:
        self.stop()


# Windows audio interfaces to try, best first. WASAPI starts cleanly; through the
# older MME the Realtek laptop mic returns zeros for a while after it opens.
# WDM-KS is left out on purpose: it's flaky and once hung for a minute.
HOST_API_ORDER = (WASAPI, "Windows DirectSound", "MME", "Core Audio", "ALSA")
OPEN_TIMEOUT_S = 4.0


def _host_api(device_index: int) -> str:
    import sounddevice as sd

    return sd.query_hostapis(sd.query_devices(device_index)["hostapi"])["name"]


def mic_candidates(query: str) -> list[int]:
    """Mics to try, best first: the default mic (or the ones whose name contains query)."""
    import sounddevice as sd

    apis = sd.query_hostapis()
    devices = sd.query_devices()
    rank = {name: n for n, name in enumerate(HOST_API_ORDER)}

    def api_of(index: int) -> str:
        return apis[devices[index]["hostapi"]]["name"]

    if query:
        wanted = query.casefold()
        inputs = [i for i, d in enumerate(devices) if d["max_input_channels"] > 0 and api_of(i) in rank]
        chosen = [i for i in inputs if wanted in devices[i]["name"].casefold()]
        if not chosen:
            names = sorted({devices[i]["name"] for i in inputs})
            raise ValueError(f"no microphone matches '{query}'. Microphones: {', '.join(names)}")
    else:
        chosen = [api["default_input_device"] for api in apis if api["name"] in rank and api["default_input_device"] >= 0]
    return sorted(dict.fromkeys(chosen), key=lambda i: rank[api_of(i)])


def pick_microphone(query: str) -> int | None:
    candidates = mic_candidates(query)
    return candidates[0] if candidates else None


def to_16k(audio: np.ndarray, rate: int) -> np.ndarray:
    """Resample mono audio to 16 kHz (simple, and plenty for speech recognition)."""
    if rate == RATE or audio.size == 0:
        return audio.astype(np.float32)
    if rate % RATE == 0:
        step = rate // RATE
        usable = audio.size // step * step
        return audio[:usable].reshape(-1, step).mean(axis=1).astype(np.float32)
    count = int(round(audio.size * RATE / rate))
    old_times = np.arange(audio.size) / rate
    new_times = np.arange(count) / RATE
    return np.interp(new_times, old_times, audio).astype(np.float32)


class Recorder:
    """Records from the mic while you hold the talk button.

    Laptop mics often give silence for the first half second after opening.
    By default the mic opens when you press, and on_live() fires once it's
    really listening, so you know when to start. With keep_open the mic stays
    open, starts instantly and keeps a short pre-roll, but Windows shows the
    mic as in use the whole time. Audio is only kept while you're holding the
    button either way.
    """

    def __init__(self, microphone: str, max_seconds: float, keep_open: bool = False, on_live=None) -> None:
        import sounddevice as sd

        self._sd = sd
        self._query = microphone
        self._max_seconds = max_seconds
        self._candidates = mic_candidates(microphone)
        if not self._candidates:
            raise ValueError("no microphone found")
        self._opened_once = False
        self._use(self._candidates[0])
        self.keep_open = keep_open
        self._on_live = on_live
        self._lock = threading.Lock()
        self._recording = False
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._ring: collections.deque[np.ndarray] = collections.deque()
        self._ring_frames = 0
        self._live = False
        self._heard_frames = 0
        self._stream = None
        if keep_open:
            self._open()

    def _use(self, index: int) -> None:
        info = self._sd.query_devices(index, "input")
        self.device = index
        self.name = info["name"]
        self.api = _host_api(index)
        self.rate = int(info["default_samplerate"])
        # Let Windows convert channels / sample rate for us if the mic needs it.
        self._extra = self._sd.WasapiSettings(auto_convert=True) if self.api == WASAPI else None
        self._max_frames = int(self._max_seconds * self.rate)
        self._preroll_frames = int(PREROLL_S * self.rate)
        self._warmup_frames = int(WARMUP_LIMIT_S * self.rate)

    def _open_stream(self, gave_up: threading.Event):
        stream = self._sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=self.rate,
            dtype="float32",
            callback=self._callback,
            extra_settings=self._extra,
        )
        stream.start()
        if gave_up.is_set():  # it answered too late; we've moved on
            stream.stop()
            stream.close()
            return None
        return stream

    def _try_open(self) -> str | None:
        """Open the current mic on a helper thread, so a stuck driver can't freeze anything. None = OK."""
        result: dict = {}
        gave_up = threading.Event()

        def target():
            try:
                result["stream"] = self._open_stream(gave_up)
            except Exception as exc:
                result["error"] = str(exc).splitlines()[0]

        thread = threading.Thread(target=target, name="mic-open", daemon=True)
        thread.start()
        thread.join(OPEN_TIMEOUT_S)
        if thread.is_alive():
            gave_up.set()
            return f"no answer after {OPEN_TIMEOUT_S:.0f}s"
        if "error" in result:
            return result["error"]
        self._stream = result["stream"]
        return None

    def _open(self) -> None:
        self._live = False
        self._heard_frames = 0
        problems = []
        first = self.device
        for index in [first] + [i for i in self._candidates if i != first]:
            self._use(index)
            problem = self._try_open()
            if problem is None:
                if not self._opened_once or index != first:
                    log.info("Mic: %s (%s)", self.name, self.api)
                self._opened_once = True
                return
            log.warning("Mic %s (%s) didn't open: %s", self.name, self.api, problem)
            problems.append(f"{self.name} ({self.api})")
        self._use(first)
        raise RuntimeError("no microphone would open (tried " + ", ".join(problems) + ")")

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    @property
    def live(self) -> bool:
        return self._live

    def _callback(self, indata, frames, _time_info, _status) -> None:
        block = indata[:, 0].copy()
        became_live = False
        with self._lock:
            if not self._live:
                self._heard_frames += frames
                if self._heard_frames >= self._warmup_frames or float(np.sqrt(np.mean(np.square(block)))) > LIVE_RMS:
                    self._live = True
                    became_live = self._recording
            if self._recording:
                if self._frames < self._max_frames:
                    self._chunks.append(block)
                    self._frames += frames
            elif self.keep_open:
                self._ring.append(block)
                self._ring_frames += frames
                while self._ring and self._ring_frames - len(self._ring[0]) >= self._preroll_frames:
                    self._ring_frames -= len(self._ring.popleft())
        if became_live and self._on_live is not None:
            self._on_live()

    def start(self) -> bool:
        """Start keeping audio. Returns True if the mic is already live."""
        with self._lock:
            self._chunks = list(self._ring)
            self._frames = self._ring_frames
            self._ring.clear()
            self._ring_frames = 0
            self._recording = True
        if self._stream is None or not self._stream.active:
            self._close()
            try:
                self._open()
            except Exception:
                with self._lock:
                    self._recording = False
                raise
        return self._live

    def stop(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
            self._frames = 0
        if not self.keep_open:
            self._close()
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        return to_16k(audio, self.rate)

    def close(self) -> None:
        with self._lock:
            self._recording = False
        self._close()


class WhisperVoice:
    """Records with the Recorder and transcribes on this computer with faster-whisper."""

    def __init__(self, vcfg, runner, feedback) -> None:
        self.vcfg = vcfg
        self.runner = runner
        self.feedback = feedback
        self.recording = False  # what the buttons asked for
        self._listening = False  # what the mic thread is doing
        self._announced = False
        self._announce_lock = threading.Lock()
        self.recorder = Recorder(
            vcfg.microphone, vcfg.max_seconds, keep_open=vcfg.keep_mic_open, on_live=self._announce
        )
        self._model = None
        self._model_error: Exception | None = None
        self._model_ready = threading.Event()
        self._commands: queue.Queue = queue.Queue()
        self._jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._load_model, name="whisper-load", daemon=True).start()
        threading.Thread(target=self._mic_loop, name="mic", daemon=True).start()
        threading.Thread(target=self._work, name="whisper", daemon=True).start()

    def describe(self) -> str:
        mic = "kept open" if self.vcfg.keep_mic_open else "opens while you talk"
        return f"Whisper {self.vcfg.model} ({self.vcfg.device}), mic: {self.recorder.name} ({mic})"

    def _load_model(self) -> None:
        started = time.monotonic()
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        try:
            from faster_whisper import WhisperModel

            options = dict(
                device=self.vcfg.device,
                compute_type=self.vcfg.compute_type,
                cpu_threads=max(1, (os.cpu_count() or 8) // 2),
            )
            try:
                # Already downloaded: load it without going online.
                self._model = WhisperModel(self.vcfg.model, local_files_only=True, **options)
            except Exception:
                log.info("Downloading Whisper '%s' (first run only, a few hundred MB)...", self.vcfg.model)
                self._model = WhisperModel(self.vcfg.model, **options)
            log.info("Whisper is ready (%.1fs).", time.monotonic() - started)
        except Exception as exc:
            self._model_error = exc
            log.error("Couldn't load Whisper '%s': %s", self.vcfg.model, exc)
        finally:
            self._model_ready.set()

    def _announce(self) -> None:
        """The mic is live: tell you to start talking (blue light and a buzz)."""
        with self._announce_lock:
            if not self._listening or self._announced:
                return
            self._announced = True
        self.feedback.request("listening")
        log.info("Listening... talk now.")

    # The buttons only queue commands; the mic thread does the slow work, so
    # the controller never waits on the audio driver.
    def start(self) -> None:
        if not self.recording:
            self.recording = True
            self._commands.put("start")

    def stop(self) -> None:
        if self.recording:
            self.recording = False
            self._commands.put("stop")

    def cancel(self) -> None:
        if self.recording:
            self.recording = False
            self._commands.put("cancel")

    def close(self) -> None:
        self.cancel()
        self._commands.put(None)

    def _mic_loop(self) -> None:
        opened = False
        while True:
            command = self._commands.get()
            if command is None:
                self.recorder.close()
                self._jobs.put(None)
                return
            if command == "start":
                self._announced = False
                self._listening = True
                try:
                    live = self.recorder.start()
                    opened = True
                except Exception as exc:
                    self._listening = False
                    log.error("Couldn't open the microphone: %s", exc)
                    self.feedback.request("error")
                    continue
                if live:
                    self._announce()
                continue
            # "stop" or "cancel"
            self._listening = False
            if not opened:
                continue
            opened = False
            audio = self.recorder.stop()
            if command == "cancel":
                self.feedback.request("voice_empty")
                continue
            self._finish(audio)

    def _finish(self, audio: np.ndarray) -> None:
        seconds = audio.size / RATE
        if seconds < self.vcfg.min_seconds:
            log.info("That was too short (%.1fs), so it was ignored.", seconds)
            self.feedback.request("voice_empty")
            return
        if not np.any(audio):
            log.warning(
                "The mic (%s) recorded complete silence. Is it muted (check the mute key and "
                "Windows sound settings) or blocked in Settings > Privacy & security > Microphone?",
                self.recorder.name,
            )
            self.feedback.request("error")
            return
        log.info("Got %.1fs of audio, turning it into text...", seconds)
        self.feedback.request("transcribing")
        self._jobs.put(audio)

    def _work(self) -> None:
        while True:
            audio = self._jobs.get()
            if audio is None:
                return
            try:
                text = self.transcribe(audio)
            except Exception:
                log.exception("Couldn't turn that into text")
                self.feedback.request("error")
                continue
            if not text:
                log.info("Didn't catch any words.")
                self.feedback.request("voice_empty")
                continue
            log.info("Heard: %s", text)
            self.feedback.request("voice_done")
            self.runner.submit(Paste(text, self.vcfg.auto_send))

    def transcribe(self, audio: np.ndarray) -> str:
        if not self._model_ready.is_set():
            log.info("Whisper is still loading; your words will go through once it's ready.")
            self._model_ready.wait()
        if self._model is None:
            raise RuntimeError(f"Whisper isn't available: {self._model_error}")
        if audio.size == 0 or float(np.sqrt(np.mean(np.square(audio)))) < SILENCE_RMS:
            return ""
        started = time.monotonic()
        segments, _info = self._model.transcribe(
            audio,
            language=self.vcfg.language or None,
            beam_size=5,
            vad_filter=True,
            initial_prompt=self.vcfg.hint_words or None,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        log.debug("Transcribed %.1fs of audio in %.1fs.", audio.size / RATE, time.monotonic() - started)
        return text
