# ps5-agent-handler

Use a PS5 DualSense controller to drive the **Code tab** of the Claude desktop app: switch sessions, approve or deny what Claude wants to do, stop it, and talk to it by holding R2. No mouse needed.

It runs on Windows for now. Everything Windows-specific lives in `src/ps5_agent_handler/platform/windows.py`, so a Mac version only needs that one file redone.

## Download (Windows)

1. Download `ps5-agent-windows.zip` from the latest release and unzip it anywhere.
2. Pair the controller (see step 2 below), open the Claude desktop app's Code tab, and double-click `ps5-agent.exe`.
3. Keep its window open while you use the controller. `START HERE.txt` in the zip has the details.

The first run puts `config.toml` next to `ps5-agent.exe`, so you can change the buttons. The app isn't code-signed yet, so Windows may say "Windows protected your PC": choose **More info**, then **Run anyway**. The download leaves out local Whisper to stay small (27 MB); Circle uses Claude's own mic.

To build the download yourself: `uv run python packaging/build.py` (makes `dist/ps5-agent-windows.zip`).

## Set up from source

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Pair the controller over Bluetooth: hold **PS** and **Create** (the small button to the left of the touchpad) until the light bar flashes. Then add it in Windows **Settings > Bluetooth & devices > Add device > Bluetooth**. A USB-C cable works too.
3. In this folder, install everything:

   ```
   uv sync
   ```

4. Check that the buttons come through:

   ```
   uv run ps5-agent --test-buttons
   ```

5. Start it:

   ```
   uv run ps5-agent
   ```

Keep that terminal open: it prints what each button did. The first time it starts, it downloads the Whisper speech model (about 480 MB for `small.en`).

## Buttons

| Button | What it does | How |
|---|---|---|
| D-pad up / down | Previous / next session; **on a question card**, highlight the answer above / below (Cross picks it) | Ctrl+Shift+[ / Ctrl+Shift+] |
| Left stick, flick up / down | Previous / next session (one per flick; let it return to the middle to flick again) | same as the D-pad |
| D-pad left / right | Move to the previous / next thing on screen (hold to repeat); **on a question card**, the previous / next question | Shift+Tab / Tab |
| Cross | Send / confirm; **on a question card**, pick or tick the highlighted answer (on **Other**, it also puts the cursor in its box) | Enter |
| Circle | Talk: press to start, press again to stop; the text waits for Cross | presses Claude's own mic button |
| Triangle | Yes: approve what Claude is asking | presses a permission prompt's **Allow**; with no prompt, the main button of whatever Claude is waiting on (the one with Ctrl+Enter: **Submit** on a question card, the first answer in the next-step box) |
| Square | No: reject it | presses a permission prompt's **Deny**; with no prompt, **Skip** / **Not now** / **No** in that same card, or closes it |
| Create | Open the session that needs you; press again for the next one | reads each session's status in the sidebar |
| L1 / R1 | Previous / next model (Opus, Fable, Sonnet, Haiku) | opens Claude's model menu, picks, closes it |
| L2 / R2 | Less / more thinking (effort) | sends Claude's `/effort` command; no menu |
| Mic button | Stop Claude's reply | Esc |
| Options | New session | Ctrl+N |
| Touchpad swipe, right stick | Scroll the conversation | mouse wheel over the conversation |
| Touchpad press | Jump to the newest message | presses **Scroll to bottom** |
| PS | Pause / resume the controller (for gaming) | |

**Thinking (L2 / R2)** types Claude's own `/effort <level>` command into the prompt box and sends it. Claude handles that itself, so nothing goes to the model and no menu opens. It only does this when the prompt box is empty, so it never mixes into a message you're writing; otherwise it tells you to send or clear it first. A level that the current model doesn't have is skipped.

**Model (L1 / R1)** has no direct command in Claude, so the app opens the model menu, picks the next model and closes it, all in under a second. You'll see the menu flicker.

App switching (Alt+Tab) isn't on a button right now; `l3 = "previous_app"` and `r3 = "next_app"` in `config.toml` put it on the stick presses.

## The light bar and rumble

The light bar follows **the session open on screen** in Claude, checked every second:

| Color | Meaning |
|---|---|
| Red, pulsing | It needs you: an Allow / Deny prompt is showing, it's waiting for an answer (including the next-step box at the end of a reply, "Should I...?"), or it hit an error |
| Yellow | It's working (Claude's **Stop** button is showing, or the sidebar says Running) |
| Blue | It's done |
| Dim white | No session is open (for example, the new-session screen) |
| Nearly off | The controller is paused |

It buzzes when that session starts needing you, and gives a short buzz when it finishes. Switching sessions doesn't buzz. A short low rumble means a button found nothing to do, such as Triangle with no permission prompt on screen.

To make it follow every session instead (red if any needs you, yellow if any is working, blue when all are done), set `light_follows = "all"` in `[feedback]`. **Create** always jumps to whichever session needs you, wherever it is. Set `agent_status = false` to turn all of this off.

The app tells which session is open from the sidebar row Claude marks as the current page, and reads each row's status from the hidden line Claude keeps for screen readers ("Running", "Awaiting input", "Awaiting answer", "Unread response").

Claude actions bring the Claude window to the front first. Keys are only sent while Claude is the front window, so they never land in another app. In an empty prompt box, D-pad right (Tab) accepts Claude's suggested prompt.

## Change what a button does

Edit [config.toml](config.toml) and save. The running app reloads it. For example:

```toml
[buttons]
l2 = { keys = "ctrl+enter" }      # press Claude's suggested next step
create = "toggle_diff"

[actions]
toggle_diff = { keys = "ctrl+shift+d" }
```

Press **Ctrl+/** in the Code tab to see all of Claude's own shortcuts.

## Voice

**Circle** presses the Code tab's own mic button. Press it, talk, press it again, and Claude puts what you said into the prompt box. Then press **Cross** to send it. Claude does the speech-to-text, so this needs the internet.

**Answering "Other" by voice:** on a question card, pick **Other** (D-pad, then Cross), press **Circle**, talk, press **Circle** again. Claude's dictation always writes into the prompt box, so once it finishes the app moves your words into the Other box; then **Triangle** sends the answer. It puts the cursor at the end of the prompt box before dictating and moves only the words that were added, so anything already in the prompt box stays as it was.

**Local Whisper** is the offline alternative: set `circle = "toggle_talk"` (press to start / stop), or put `push_to_talk` on a button you hold, then restart the app. Whisper only loads when a button uses it. With Whisper, wait for the buzz and the pink light before talking, because the laptop's mic gives only silence for its first half second after it opens. Whisper settings are in `[voice]`:

- `keep_mic_open = true` lets you start talking the moment you press. The mic then stays open while the app runs, so Windows shows it as in use. Audio is still only kept while you're talking, plus the 0.3 s before you pressed.
- `auto_send = true` presses Enter for you after pasting.
- `model = "base.en"` is faster; `"medium.en"` is more accurate but slower.
- `microphone = "Realtek"` picks a mic by part of its name.

## Commands

| Command | What it does |
|---|---|
| `uv run ps5-agent` | Run it |
| `uv run ps5-agent --test-buttons` | Only print what the controller sends |
| `uv run ps5-agent --dry-run` | Print what each button would do, without touching Claude (voice still transcribes) |
| `uv run ps5-agent --inspect` | List the buttons Claude is showing right now |
| `uv run pytest` | Run the tests |

## If something doesn't work

- **Nothing shows up in `--test-buttons`.** If Steam is running, quit it or turn off its PlayStation controller support, because Steam Input takes the controller over. Then check the controller is paired in Bluetooth settings.
- **Triangle or Square says "No approve button on screen" while a prompt is showing.** The prompt's labels aren't the ones in `config.toml`. Run `uv run ps5-agent --inspect` while the prompt is showing, find its buttons in the list, and put those labels in `approve` / `reject` in `config.toml`.
- **"Couldn't open the microphone".** Turn on Windows **Settings > Privacy & security > Microphone > Let desktop apps access your microphone**.
- **The first R2 press is slow.** The Whisper model loads in the background at start-up, and the very first run downloads it.
- **"Couldn't bring Claude to the front".** Windows sometimes stops apps from switching windows. Click the Claude window once and try again, or set `bring_claude_to_front = false` so buttons only work while Claude is already in front.

## How it works

- `controller.py` reads the DualSense through SDL2, the way games do, including the triggers and the touchpad.
- `engine.py` decides what each press means: repeats, pause, push-to-talk, scrolling.
- `runner.py` carries out actions one at a time on its own thread, so reading the controller never waits.
- `platform/windows.py` sends shortcuts with SendInput. It finds and presses Claude's on-screen buttons (the permission prompt's Allow / Deny, **Scroll to bottom**, the prompt box) through Windows UI Automation, searching only Claude's main pane and popups, never the sidebar. To do that it switches on Claude's accessibility tree, the same way Windows' Inspect tool does.
- `voice.py` records with `sounddevice` and transcribes with `faster-whisper`.

## Later

- Mac version (`platform/mac.py`).
- An on-screen card that shows what each button does.
- Different button layouts per app (terminal, browser).
- A light bar color or rumble when an agent finishes or needs approval.
