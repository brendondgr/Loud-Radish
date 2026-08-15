# Deployment

*Last updated: 2026-08-15 (Phase 14 — final documentation pass)*

> **This application is not deployed.** It runs on the machine of the person using it, and that is
> the design, not a stage before hosting. This file describes installing and running it there.

Until Phase 14 this file described environments, build artifacts, and an `npm run build` step for a
frontend that does not exist. None of that applied: Decision D-011 removed the Node toolchain, and
Decision D-010 removed the upload-and-poll service model that a hosted deployment would have served.

## Why it is local

The application listens to a room and writes down what is said in it. Three consequences follow, and
together they rule hosting out rather than merely making it unattractive.

**Audio would have to leave the room.** A hosted version means streaming a private meeting to
someone else's machine. With a local speech model and a local language model, nothing leaves at all,
and the header says so at a glance.

**There is no authentication model.** Not an omission — a deliberate consequence of being
single-user and loopback-bound. Adding hosting means adding accounts, sessions, and access control
to a transcript store that currently, correctly, assumes one person.

**Latency is the product.** The commit policy is tuned around inference latency measured in
hundreds of milliseconds. A network hop between capture and inference changes what the whole
streaming engine is solving for.

The server binds to `127.0.0.1` for these reasons. `app.py` warns when `--host` is set to anything
else, because anything that can reach that port can read the transcript of a private room.

## Installing

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). No Node toolchain, no build step.

```bash
uv sync
```

That gives a working application with a scripted mock speech model — enough to see the interface
work. For real use:

```bash
uv sync --extra asr-whisper --extra audio-device
```

| Extra | Adds | Without it |
|---|---|---|
| `asr-whisper` | Real transcription via `faster-whisper` | Only the scripted mock backend is offered |
| `audio-device` | Microphone and system loopback capture | Only the file source is available |
| `vad-silero` | Neural voice-activity detection | The energy detector is used, which is fine in a quiet room |
| `credentials` | Storing API keys in the OS credential store | Keys must come from environment variables |

Everything the extras enable is then selectable **inside the application** — Settings → Audio for
the device, Settings → Transcription for the model. Nothing needs a config file edited by hand.

## Running

```bash
uv run python app.py
```

Then open <http://127.0.0.1:8395>. Flags: `--port`, `--host`, `--reload`, `--open`, `--log-level`.

The launcher checks the port is free and names the remedy if it is not, creates `data/` and `logs/`,
loads `.env` without overriding real environment variables, and reports which extras are installed
so a missing capability is visible at startup rather than as a confusing failure later.

## What ends up on disk

| Path | Holds | Notes |
|---|---|---|
| `data/transcriber-config.json` | Settings saved from the interface | Never contains credentials |
| `data/sessions/*.db` | One SQLite file per session | Transcript, summaries, glossary, conversation |
| `data/audio/` | Recordings uploaded through Settings → Audio | Only what you put there |
| `logs/` | Application logs | Never transcript content (BE §18) |
| `~/.cache/huggingface/` | Whisper model weights | Downloaded once, ~75 MB to 3 GB by model |

Audio is **not** retained by default. Sessions are kept until deleted, from the `/sessions` page or
by removing the file; `storage.retention_days` sets an expiry if you want one.

## Backing up

A session is one self-contained SQLite file. Copying `data/sessions/` copies everything: transcript,
timestamps, summaries, glossary, and the conversation about it. There is no external state, no
database server, and nothing to restore in a particular order.

## Hardware

The one number that matters is the **real-time factor** — how fast the model transcribes relative to
the speech arriving. Below 1.0 the pipeline falls behind and will not catch up, which the status bar
says in words as well as colour.

Measured on this project's development machine (32-core AMD Ryzen AI Max+ 395, CPU only):
`faster-whisper` `small` at `int8` gives **RTF ≈ 1.5** with about 1.4 s of commit latency. That is
comfortable but not generous — `medium` on the same machine would not keep up.

There is no substitute for measuring on your own hardware, which is why the status bar shows the
figure continuously rather than burying it. If it drops below 1.0 the application offers the next
model down as a one-click fix.

A note that outranks any model choice: **a directional or clip-on microphone improves accuracy more
than any upgrade in this table.** A laptop microphone in a lecture hall captures a distant,
reverberant speaker plus the room, and no model recovers what was never captured.

## If it is ever packaged

Whether this stays a browser-plus-local-server application or is wrapped in a desktop shell
(Tauri, Electron, Qt) is still open — see `docs/checklist.md`. Nothing here forecloses it: the
backend is a single ASGI application, the frontend is static files with no build step, and the two
communicate over HTTP and one WebSocket. A shell would embed the server and point a webview at it.
