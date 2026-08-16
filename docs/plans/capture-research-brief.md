# Research Brief — Window Capture on Fedora 44 / KDE / Wayland

**Purpose:** a briefing for research conducted outside this repository. It states what has been
measured on this machine, what those measurements rule out, and the questions that are genuinely
open. It is deliberately specific: a generic answer about "screen recording on Linux" will not help,
because most of that chain already works here.

**Machine:** Fedora KDE 44, kernel 7.1, Wayland, KWin. AMD Ryzen AI MAX+ 395 / Radeon 8060S
(gfx1151, Strix Halo). PipeWire with `xdg-desktop-portal-kde`. GStreamer 1.x with `pipewiresrc`;
ffmpeg 8.1.2 **without** `pipewiregrab`. No hardware video encoder.

---

## 1. Read this first: the premise needs correcting

The working assumption going in was *"it is not extracting ANY audio from the source window, and no
picture appears."* The files on disk from the most recent run (`20260816-151020-2155828d7f9e`,
5 m 20 s) say otherwise:

| What | Measured | Verdict |
| --- | --- | --- |
| Video file | 1080×1064, 15 fps, 18 MB | ✅ real window dimensions |
| Video content | mean luminance 70/255, full 0–255 range | ✅ actual picture, not black |
| Preview JPEG | 480×270, 17 KB | ✅ written correctly |
| Audio track | 320 s, RMS 0.036, peak 1.12 | ✅ continuous real audio |
| Audio coverage | 320 of 320 seconds above −50 dBFS | ✅ nothing dropped |
| Muxed output | vp8 video + opus audio in one file | ✅ both streams present |

**Capture is working.** The earlier failures — a 480×16 recording, a `videoscale` integer overflow,
five portal dialogs per recording — are fixed and the fixes are verified against this machine.

So research aimed at *"how do I capture a window on Wayland"* would re-answer a solved question.
The two things that are actually unresolved are narrower, and section 3 states them.

---

## 2. What the transcript result actually means

The post-capture pass reported `state: done`, `transcribed_seconds: 322.46` — and produced
**2 segments**. That reads like a broken transcriber. It probably is not.

Running `large-v3-turbo` directly over the first 30 seconds of that audio returns **one word,
"Hello."**, with `no_speech_prob = 0.0` — the model is confident there is sound and finds almost
nothing to transcribe. Amplifying the audio 8× changes nothing, so it is not a level problem.

The spectrum explains why. Over a 20-second window:

| Band | Share of energy |
| --- | --- |
| 20–100 Hz | 17.9 % |
| 100–300 Hz | 11.8 % |
| 300–1000 Hz | 19.9 % |
| 1000–3000 Hz | 27.9 % |
| 3000–6000 Hz | 19.0 % |
| 6000–8000 Hz | 3.5 % |

Peak at **52 Hz**. Speech has little energy below 100 Hz — a human fundamental sits at 85–255 Hz —
and this has nearly a fifth of its energy down there, spread broadband above it. That is the profile
of **music or game audio**, not a talk.

**So the most likely reading is: the capture worked, and the thing being captured had almost no
speech in it.** Before researching anything else, this is worth settling in ten seconds — play a
window with someone clearly *talking* (a lecture, a podcast, a news clip) and record 60 seconds. If
the transcript fills, the audio chain is done and section 3's question 2 is the only audio work left.

**Do not skip this step.** Every remaining question below is cheaper to answer once it is known
whether the transcriber is receiving speech at all.

---

## 3. The genuinely open questions

### Question 1 — Why does the Monitor pane show no live picture? *(app-side, not OS)*

This is the one symptom not explained by anything measured above, and it is almost certainly **not**
an operating-system problem: the preview JPEG is written to disk correctly at 480×270, once a
second, by the same GStreamer pipeline that writes the video.

What to establish, in this order:

1. **Is the frame reaching the browser at all?** With a recording running, open
   `http://127.0.0.1:8395/api/capture/preview.jpg` directly in a tab. It returns **404 when idle**,
   which is correct. If it also 404s *during* a recording, the fault is server-side — the route
   resolves `recorder.state.preview_path`, so that is where to look. If it returns an image, the
   fault is in the browser.
2. **Is `capture.state` reaching the client with `recording: true` and `preview: true`?** The pane
   gates the `<img>` on both. Watch the WebSocket frames in the browser's dev tools. **The event is
   emitted once, at the moment capture starts** — if that single emit is lost, or arrives before the
   recorder reports itself running, the pane never learns there is anything to show and nothing ever
   corrects it. This is the most suspicious part of the design.
3. **Is the refresh timer running?** It stops when the pane is hidden or the tab is backgrounded, and
   in the narrow layout the pane exists but may be the inactive tab.

This needs no external research. It needs the three observations above.

### Question 2 — Can a *specific window's* audio be captured, rather than the whole machine's?

This is the real research question, and it is a genuine open problem rather than a lookup.

**What is settled:** `org.freedesktop.portal.ScreenCast` carries **video only**. It returns a
PipeWire node id for the video stream, and no PID, no application id, and no audio node. So the
audio is necessarily a separate capture. Current behaviour records the **default sink's monitor** —
everything the machine plays, with no microphone in it. That is correct for the common case and
wrong when two applications are making sound.

**What to research:**

- Does any current `xdg-desktop-portal` version expose audio alongside a ScreenCast session? Track
  the upstream discussion on portal audio capture and whether `xdg-desktop-portal-kde` implements
  it. Note the version here (`portal_version: 5`) when comparing.
- Can a PipeWire **sink-input** be identified reliably from the window the compositor granted? The
  portal gives no PID, so any mapping is a heuristic on application name — investigate whether
  KWin's own D-Bus interfaces expose a window→PID mapping that could be correlated with
  `pw-cli`'s `application.process.id` on the audio node. Note whether this survives Flatpak/Snap
  sandboxing, where the PID namespace differs.
- Failing that: is a **null-sink + loopback** arrangement acceptable? Create a virtual sink, move the
  target application's stream to it, and record its monitor. This is reliable and *changes where the
  user's audio comes out*, which needs care — a user who suddenly cannot hear the talk has a worse
  problem than an imprecise recording.

**Search terms that matter:** `xdg-desktop-portal ScreenCast audio`, `pipewire sink-input
application.process.id`, `kwin window pid dbus wayland`, `pw-cli move node to sink`,
`pactl move-sink-input`.

### Question 3 — Does the portal report a stream size on any KDE version?

The portal here returns **no `size` property** in the stream, which is why the pipeline was
negotiating blind and produced the 480×16 recording. The current code handles that by not scaling at
all when the size is unknown — correct, but it means the resolution ceiling silently does nothing.

Worth establishing: whether newer `xdg-desktop-portal-kde` populates `size`, and whether the correct
source of truth is instead the PipeWire node's own format once the stream is running (queried
through `pipewiresrc`'s negotiated caps rather than the portal's properties).

**Search terms:** `xdg-desktop-portal ScreenCast stream properties size`, `pipewiresrc caps
negotiation portal`, `libportal screencast stream size`.

---

## 4. What NOT to research

Time spent here would be wasted — each is measured and settled on this machine:

- **Whether Wayland allows window capture at all.** It does, through the portal, and it works here.
- **Whether GStreamer can consume the portal's PipeWire node.** It can — `pipewiresrc` with the
  portal's fd and node id, and the resulting file plays.
- **Whether ffmpeg can do it instead.** This ffmpeg has no `pipewiregrab`. It is still used for the
  remux, which works.
- **Whether the machine can capture system audio.** It can — `pw-record --target <sink>.monitor
  --container=raw` returns clean float32, verified sample by sample.
- **GPU acceleration for transcription.** Settled: the ROCm build of CTranslate2 from the project's
  GitHub releases, kept in `data/wheels/`, restored automatically by `app.py` at startup because
  `uv run` reinstalls the PyPI build on every launch. Measured 33× real time against 8.3× on CPU.

---

## 5. One trap worth carrying into the research

Three separate faults here were **well-formed code that failed invisibly**, and each was found only
by measuring the output rather than reading the code or checking an exit status:

- a pipeline that wrote a **480×16** file — valid command, exit code 0, no warning;
- `pw-record` writing an **AU header** to stdout, whose bytes decode to `NaN` as float32 — a capture
  that ran at the right rate for the right duration and transcribed silence, found by measuring peak
  amplitude and getting `nan`;
- a **1291-test suite passing** while the feature was broken in every respect a user could see,
  because the tests asserted the shape of the launch line and never opened the file.

Whatever the research concludes, the acceptance test should be *"open the artefact and measure it"* —
dimensions, luminance, RMS, spectrum — never *"the command succeeded"*.
