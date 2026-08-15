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
uv sync
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
uv run app.py
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

### Two different real-time factors

Batch and streaming figures are not comparable, and confusing them will lead you to pick a model
that cannot keep up.

**Batch** transcribes each second of audio once. **Streaming** re-runs inference over an overlapping
buffer on every step, because that is what LocalAgreement needs in order to know which words two
consecutive passes agree on. Measured here, that costs roughly **18× the batch work**: `small` on
CPU transcribes a clip at 26× real time but sustains only 1.5× through the live pipeline.

So: divide a published batch figure by about 18 to guess whether a model will keep up live.

### Measured on this project's development machine

AMD Ryzen AI Max+ 395 (32 cores) with a Radeon 8060S iGPU, on a 25-second clip:

| Model | CPU `int8` batch | GPU `float16` batch | Live, through the pipeline |
|---|---|---|---|
| `small` | 26.4× | 40.7× | 1.5× (CPU, measured) |
| `large-v3-turbo` | 11.0× | **40.2×** | **2.6× (GPU, measured)** |
| `large-v3` | — | 10.3× | ~0.6× estimated — will not keep up |

The live figures for `small`/CPU and `large-v3-turbo`/GPU were measured through the running
application, not derived. `large-v3` is an estimate from its batch figure and the ~18× ratio, and is
marked as one.

`large-v3-turbo` is the interesting row. It is a distilled `large-v3` with four decoder layers
instead of thirty-two, and on a GPU it runs at the speed of `small` while being a large-class model.
Live, it sustains 2.6× with commit latency around 1.2 s — comfortably ahead of a speaker, and 1.7×
the headroom of `small` on the CPU while being a far better model. It is the right default for
anyone with a GPU, and the fallback ladder steps through it rather than through `medium`, which it
beats on both speed and accuracy.

On CPU only, `small` remains the right choice: turbo's 11× batch works out to well under real time
once the streaming overhead is applied.

## GPU acceleration on AMD (ROCm)

`faster-whisper` uses CTranslate2, which added AMD support in **v4.7.0**. The wheel on PyPI is
CPU-and-CUDA; the ROCm build is attached to each GitHub release as `rocm-python-wheels-Linux.zip`.
No fork, no source build, and `HSA_OVERRIDE_GFX_VERSION` is not needed.

Verified on this machine — Fedora 44, kernel 7.1, ROCm 7.1.1 from Fedora's own repositories,
Radeon 8060S (gfx1151), Python 3.14:

```bash
# 1. The two ROCm runtime libraries the wheel needs that a base ROCm install does not pull in.
sudo dnf install hiprand rocrand

# 2. The ROCm build of CTranslate2, matching the version already pinned in pyproject.toml.
curl -LO https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.1/rocm-python-wheels-Linux.zip
# The trailing dash after the second tag is not optional: `cp314-cp314*` also matches the
# free-threaded `cp314t` wheel, and uv refuses two conflicting URLs for one package.
unzip -j rocm-python-wheels-Linux.zip '*cp314-cp314-manylinux*x86_64.whl'
uv pip install --reinstall ./ctranslate2-4.8.1-cp314-cp314-manylinux*.whl
```

Then set **Run on** to `GPU (CUDA)` and **Precision** to `float16` in Settings → Transcription. The
label says CUDA because CTranslate2 reports ROCm through the same API; it is your Radeon.

Wheels ship for cp39 through cp314 including free-threaded 3.14t. Substitute your own Python tag.

Two caveats worth knowing before committing to this:

- **It replaces the CPU/CUDA build.** The same package name provides both, so installing the ROCm
  wheel means that environment no longer has a CUDA build. On a machine with only an AMD GPU that
  costs nothing.
- **`uv sync` will put the PyPI wheel back**, because `pyproject.toml` names `ctranslate2` without
  knowing which build you want. Keep the wheel — `data/wheels/` is the conventional place here —
  and re-install it after any sync that touches CTranslate2. The startup banner will tell you when
  that has happened, so it is noticed at the next launch rather than at the next recording.

### Startup tells you where you stand

`app.py` checks all three requirements at startup and prints what is missing along with the exact
commands, with your Python tag and CTranslate2 version filled in. The same report is on
`GET /api/health` under `acceleration`. It never imports the GPU stack to find out, so a broken
ROCm install cannot stop the application starting.

### The container option

A ROCm PyTorch container — such as one built from TheRock's `rocm-sdk` wheels — can run
`faster-whisper` too, and every library the ROCm CTranslate2 needs is present inside one. It needs
one extra step: those wheels put the runtime under `site-packages/_rocm_sdk_*/lib`, and CTranslate2
has no RPATH pointing there, so the directories must be on `LD_LIBRARY_PATH`. PyTorch works without
this because it carries its own RPATH, which makes the failure look like a missing install when it
is a missing path.

It is the harder option for *this* application regardless. The transcriber needs the microphone, so
containerising it means passing `/dev/snd` through as well as the GPU, the port, and the data
directory — whereas on the host the whole thing is two packages and a wheel. A container earns its
keep when the alternative is a PyTorch-based ASR backend, not here.

If ROCm proves troublesome, `whisper.cpp` with its Vulkan backend is the fallback: it runs on the
same iGPU through RADV with no ROCm at all, at the cost of writing a second ASR backend against
Seam A — which is exactly the seam that exists to make that a contained change.

## If it is ever packaged

Whether this stays a browser-plus-local-server application or is wrapped in a desktop shell
(Tauri, Electron, Qt) is still open — see `docs/checklist.md`. Nothing here forecloses it: the
backend is a single ASGI application, the frontend is static files with no build step, and the two
communicate over HTTP and one WebSocket. A shell would embed the server and point a webview at it.
