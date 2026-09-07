# Workflow

*Last updated: 2026-08-14 (root launcher on port 8395)*

Every command needed to work in Loud Radish. If a command here is wrong, fix this file in the
same change — do not work around it silently.

## Environment Manager

| Side | Manager | Non-negotiable |
|---|---|---|
| Everything | **`uv`** | Never use bare `pip`, `poetry`, `pipenv`, or `conda`. `uv` owns `.venv/`. |

There is **no Node toolchain**. The frontend is served by the backend as Jinja2 templates plus plain
CSS and ES modules, with no build step — see Decision D-011 in `docs/documentation.md`. `uv.lock` is
committed and authoritative; never hand-edit it.

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11+ |
| `uv` | 0.11+ |

## Install

```bash
uv sync
```

That is the whole install, and **`uv run app.py` performs it for you** — there is nothing else to
add and no second command to remember (Decision **D-023**).

Everything the application can do is an ordinary dependency: real transcription
(`faster-whisper`), microphone and loopback capture (`sounddevice`), the Silero voice detector
(`onnxruntime`), the OS credential store (`keyring`), and the D-Bus client window capture needs
(`jeepney`). **There are no optional groups.** If `GET /api/health` reports a capability missing,
that means a broken or partial install rather than a choice, and `uv sync` puts it back.

Two things are still outside Python and cannot be installed by `uv`:

| Needed for | Install |
|---|---|
| Window capture — GStreamer and its plugins | `sudo dnf install gstreamer1 gstreamer1-plugins-good gstreamer1-plugins-base` |
| Window capture — a desktop screen-sharing portal | `sudo dnf install xdg-desktop-portal-kde` (or `-gnome`, `-wlr`) |

Model *weights* are not in the install either. `faster-whisper` downloads them the first time a
model is loaded, which is why adding it to the base dependencies did not make `uv sync` fetch
gigabytes.

## Adding Dependencies

```bash
uv add <package>
```

```bash
uv add --dev <package>
```

```bash
uv add <package>   # there are no optional groups — see D-023
```

## Run

One command starts everything — API, WebSocket, and the browser interface, from a single process:

```bash
uv run app.py
```

Then open <http://127.0.0.1:8395>.

`app.py` is a launcher and holds no application logic. It loads `.env`, creates `data/` and
`logs/`, reports which capabilities are present, settles the port, and starts the
server.

**Running it again takes the port back.** Starting it twice is the ordinary case — you run it,
leave it, and come back without remembering the first is still up — so the second run stops the
first instead of refusing. `SIGTERM` first, so the old server flushes the last words of any session
in progress and closes its transcript database cleanly; `SIGKILL` only after five seconds.

It will only ever stop a server it has **positively identified as this application**, by two
independent checks that must both pass: the port answers `/api/health` with this application's own
response shape, *and* the process holding it has `app.py` or `app.main:app` on its command line.
Anything else on 8395 is left alone and reported, because a launcher that kills whatever is in its
way is one that will eventually kill a database. Use `--no-takeover` to restore the old refusal.

Process lookup reads `/proc`, so takeover is Linux-only; on any other platform it degrades to the
refusal rather than misbehaving.

| Flag | Effect |
|---|---|
| `--port N` | Serve on another port (default 8395, or `API_PORT`) |
| `--host H` | Bind another interface (default `127.0.0.1`) |
| `--reload` | Restart when anything under `web/backend/app/` changes |
| `--open` | Open the interface in a browser |
| `--no-takeover` | Refuse if the port is busy, rather than stopping an older instance |
| `--log-level L` | `critical`, `error`, `warning`, `info`, or `debug` |

For development, with auto-restart:

```bash
uv run app.py --reload
```

It binds to `127.0.0.1`. That is deliberate: the application is single-user and unauthenticated, so
exposing it on a network interface would publish an unauthenticated transcript of a private room.
The launcher prints a warning if `--host` is set to anything else.

Uvicorn can still be driven directly, which is what `app.py` does underneath:

```bash
uv run uvicorn app.main:app --app-dir web/backend --port 8395
```

Any Python command runs inside the project environment via `uv run`:

```bash
uv run python -c "import sys; print(sys.version)"
```

## Running the pipeline without a UI

The streaming engine can be driven end to end over a recorded file, with console output only. This
is the validation the architecture calls for before any interface exists — and it is how the commit
policy's behaviour, including its inherent 2–4 second latency, is inspected directly.

```bash
uv run python scripts/make_fixture_wav.py --out data/fixtures
```

```bash
uv run python scripts/run_file_session.py data/fixtures/alternating-20s.wav
```

With a real model, which is installed by default:

```bash
uv run python scripts/run_file_session.py talk.wav --backend faster-whisper --model small
```

Replay faster than real time for a long recording:

```bash
uv run python scripts/run_file_session.py talk.wav --speed 10
```

## Regenerating the shared contracts

Run this whenever a route or a WebSocket event changes, and commit the result:

```bash
uv run python scripts/generate_contracts.py
```

## Test

```bash
uv run pytest
```

A single area:

```bash
uv run pytest tests/transcription
```

Tests live in `tests/<area>/test_<behavior>.py`. Add them alongside features, not afterwards.

**The suite never touches `data/`.** An autouse fixture in `tests/conftest.py` points the bottom
configuration layer at a per-test temporary directory, so a store built with no config path — or
one built with a config path that overrides only *some* of the directories — still writes its
sessions and recordings into `tmp_path`. This is not a nicety: before it existed the suite had left
949 session files in the developer's `data/sessions`, 690 of them empty, and the past-sessions page
lists that directory newest first. `tests/utils/test_data_isolation.py` is what keeps it true.

## Lint and Format

```bash
uv run ruff check .
```

```bash
uv run ruff format .
```

Check formatting without writing changes:

```bash
uv run ruff format --check .
```

## Build

There is no build step. The backend runs from source, and the frontend is served as-is.

## Environment Variables

- `.env.example` lists every variable the project uses, with safe placeholder values.
- Copy it to `.env` locally. `.env` is gitignored and must never be committed.
- **Every new variable goes into `.env.example` in the same change that introduces it.**
- **API keys never go into the config file.** They live in the OS credential store, with an
  environment variable as the documented fallback.

```bash
cp .env.example .env
```

## Verification Before Declaring Work Done

Run what applies to the change:

1. `uv run pytest` — Python behaviour.
2. `uv run ruff check .` — lint.
3. `uv run ruff format --check .` — formatting.
4. Manual browser QA for any UI-visible change, including a 320 px viewport and a keyboard-only pass —
   see `docs/design-system.md`.

**If a command was not run, say so and explain why.** Never imply a check passed when it was skipped.
Report failures with the actual output.

### Recorded mode, by hand

`recorded` mode makes two claims that only a real run confirms. With a WAV selected as the source
(or a real microphone), choose **Recorded**, press record, and check that:

1. **No transcript appears while it records.** The pane says so explicitly rather than sitting
   blank. If text appears, the streaming engine is being built when it should not be.
2. **The audio file grows.** `ls -la data/recordings/` mid-recording; the size figure in the header
   should climb in step.
3. **The transcript arrives after the toggle**, with a progress bar while the pass runs.
4. **The audio is gone afterwards** — unless Settings → Storage → *Keep the audio* is on.

To exercise the recovery path, kill the server mid-pass. The recording survives; the next start
lists it under Settings → Storage → *Recordings on disk*, with a button to transcribe it again.

### Window capture, by hand

The portal puts a dialog on screen and waits for a human, so this cannot be automated. Choose
**Window**, press record, and check that:

1. **Your desktop's own picker appears.** It is the compositor's, not ours — this application never
   sees the list of windows.
2. **Cancelling it records nothing at all**, and says so, rather than quietly starting an
   audio-only recording you did not ask for.
3. **Granting it starts the monitor pane**, whose preview updates about once a second.
4. **Closing the captured window ends the video and not the session** — the banner says so and
   audio keeps recording.
5. **A `.webm` lands in `data/recordings/` and plays**, and with `capture.mux_audio` on there is
   also a `-with-audio.webm` beside it.

To measure whether video encoding and live transcription fit together on your machine, run it
against a **real recording** — a synthetic tone cannot answer the question, and the script says so:

```bash
uv run scripts/measure_capture_cost.py --audio path/to/a/talk.wav
```

### The export estimator, on your own machine

The export window predicts a size and a duration for every preset before anything is encoded. Both
halves of that are local: content decides the size and the CPU decides the time, and the constants
shipped were fitted to one recording on a 32-core machine. Check them against your own:

```bash
uv run scripts/calibrate_export_estimate.py data/recordings/<key>/video-with-audio.webm
```

It encodes a slice at every preset and prints the prediction beside the measurement. A prediction is
doing its job when the measurement falls inside the printed range. Against the recording the
estimator was fitted to, five of five did — and the run that produced those numbers is also what
removed the VP9 preset, which measured a sixth larger and ten times slower than H.264 at the same
nominal quality.

### What cannot be verified in a headless environment

State these explicitly rather than implying coverage:

- **Live microphone and system-loopback capture.** No audio device is present. Capture code is unit
  tested against a fake device; confirming real audio arrives is a manual step on the user's machine.
- **Real-model transcription.** `faster-whisper` and its weights are not installed by default.
  Measuring real-time factor on real hardware is a manual step.
- **Live LLM endpoints.** Provider clients are tested against a stubbed HTTP transport. Pointing at a
  real Ollama or Anthropic endpoint is a manual step.

## The desktop presence (D-024)

Start with your session, and get global shortcuts:

```bash
uv run scripts/install_autostart.py
```

Drive it from anywhere without the browser:

```bash
uv run utils/loud_radish_ctl.py toggle
```

`toggle` starts if idle and stops if running — one command, because a keystroke cannot know which.
`--mode recorded` and `--mode window` pick the others; `arm --mode window` opens the options sheet
rather than starting, since window capture has three switches to answer first.

Shortcuts are registered with **your desktop's own service** and are revocable from its settings.
This application never reads input devices. Settings → Shortcuts shows whether they are actually
registered, and names the command to bind by hand if your desktop has no such service.

### After the rename to Loud Radish

Re-run `uv run scripts/install_autostart.py`. It writes `loud-radish.service` and removes the old
`transcriber.service` — leaving both enabled would race two servers for port 8395. Shortcuts
registered through that installer move with it, because registration unregisters the old component.

**A shortcut you bound by hand will not.** It names `utils/transcriber_ctl.py`, which is now
`utils/loud_radish_ctl.py`; re-copy the command from Settings → Shortcuts. Everything else migrates
on its own: the config file is renamed on first start, stored credentials move to the new keyring
service, and `TRANSCRIBER_CONFIG_PATH`, `TRANSCRIBER_ROOT` and `TRANSCRIBER_NO_GPU_REPAIR` are still
read, with a warning. See **D-038**.

## Troubleshooting: everything went quiet but the mixer says full volume

PipeWire applies **two** gains to a playback stream and multiplies them. `channelVolumes` is the one
every mixer shows and lets you drag. `volume` is a single scalar that **nothing in the KDE interface
displays or changes**. Measured on this machine:

    channelVolumes = 0.2847   -> the mixer shows this, as 66%
    volume         = 0.0200   -> invisible in the interface
    what you hear  = 0.00569   (-44.9 dB below full)

So a stream can read 100% everywhere you can look and still be inaudible.

It spreads because WirePlumber falls back to an application's **media role** when it has no entry of
its own. Anything playing music declares `media.role=Music`, so one tool set quiet under that role
silences a video player and a music client's audio-only playback at once — while a browser, which
has an entry under its own name, keeps working. That asymmetry is the giveaway.

```bash
uv run python scripts/repair_stream_volumes.py          # report
uv run python scripts/repair_stream_volumes.py --fix    # repair, restarting WirePlumber
```

**This is prevented at the source rather than by remembering a rule.** Install
`scripts/wireplumber/50-no-tool-volume-memory.conf` once per machine — see
[../scripts/wireplumber/README.md](../scripts/wireplumber/README.md) — and `pw-play`, `pw-cat`,
`pw-record`, `speaker-test`, `paplay` and `parec` stop persisting their volume anywhere. Verified by
running `pw-play --volume=0.02` twice with the drop-in installed: the role stayed at 1.0, where the
first such run previously wrote 0.020000.

The cause is a shared bucket and a default, not carelessness. `formKey` in WirePlumber's
`state-stream.lua` picks where a volume is remembered and its priority list *begins* with
`media.role`, ahead of the application's own name — and `pw-cat --help` says of the role:
"(default Music)". Baking the amplitude into the WAV file rather than passing `--volume` is still
the better habit, and `scripts/make_fixture_wav.py` does.

## Documentation Maintenance

Documentation updates ship with the code, not after it. The full trigger table is in
[skills/global-project-rules/SKILL.md](skills/global-project-rules/SKILL.md) §3. In short:

| Change | Update |
|---|---|
| Directory or significant file added/moved/removed | `docs/structure.md` |
| Stack, architecture, or notable decision | `docs/documentation.md` |
| Any command on this page | `docs/workflow.md` |
| Task finished or new work found | `docs/checklist.md` |
| Route, page, or WebSocket event | `docs/routes.md` |
| API or event shape | `docs/api-contract.md` + `web/shared/contracts/` |
| Template or module ownership | `docs/component-map.md` |
| Data movement | `docs/data-flow.md` |
| Design tokens or UI conventions | `docs/design-system.md` |
| Build or run procedure | `docs/deployment.md` |

## Planning and Handoff

- Multi-step or multi-session work gets a plan in `docs/plans/<short-kebab-topic>.md` **before**
  implementation, following `docs/skills/planner/planner.md`.
- The active plan is [plans/live-seminar-transcriber.md](plans/live-seminar-transcriber.md). Update its
  phase status table as work lands.
- Handoff = the plan plus current `docs/checklist.md` state. Nothing important lives only in chat.

## Git Workflow

- Work on a feature branch. Do not commit directly to `main`.
- Commit per validated phase. Push only when the user explicitly asks.
- Phase commit message format:
  `[Plan Name] ([Step] / [Total]) Complete: <sentence describing what was done>`
- Keep documentation updates in the same commit as the code they describe.

## Supported Agent Environments

Configured: **Claude Code**, **OpenAI Codex**, **Cursor**.

| Tool | Pointer location | Format |
|---|---|---|
| Claude Code | `.claude/skills/<skill>/SKILL.md` | `SKILL.md` + `name`/`description` frontmatter |
| OpenAI Codex | `.agents/skills/<skill>/SKILL.md` | `SKILL.md` + `name`/`description` frontmatter |
| Cursor | `.cursor/rules/<rule>.mdc` | `.mdc` + `description`/`globs`/`alwaysApply` frontmatter |

Adding a canonical skill under `docs/skills/` requires adding the matching pointer to **all three**
tools in the same change.
