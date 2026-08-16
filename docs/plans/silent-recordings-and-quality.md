# Silent Recordings and Recording Quality

**Status:** ✅ **Complete — all 4 phases.**
**Branch:** `main`

---

## 1. Introduction

Window capture now records the right window, and two things about the result are wrong. The recent
recordings contain **no audio at all** — not quiet audio, not the wrong audio, but bit-exact digital
silence — while older ones from the same code contain real sound. And the video is heavily
compressed: blocky, smeared, visibly worse than the window it was pointed at.

Both faults were diagnosed by measurement before this plan was written, and neither is where the
code thinks it is. The audio fault is not in the capture logic at all; it is in a resource the
application leaks. The video fault is a property that was never set and therefore sat at a factory
default chosen for video calls in 2010.

**The end state being built toward:** a window recording whose audio track contains the window's
sound every time, which fails loudly at the start of the session rather than silently for its
length, and whose video is worth watching back.

---

## 2. What was measured

### 2.1 The silence is a name collision

`ApplicationTap` creates a null sink called `transcriber-tap`, links the application's playback
ports into it, and records its monitor. The name is a constant. `pactl load-module` outlives the
process that called it, so every session that ends without reaching `_teardown` — a crash, a
`SIGKILL`, a reload — leaves its sink loaded. **PipeWire does not uniquify the name.** Five of them
were loaded when this was investigated:

```
536870916  module-null-sink  sink_name=transcriber-tap channel_map=stereo
536870917  module-null-sink  sink_name=transcriber-tap channel_map=stereo
536870918  module-null-sink  sink_name=transcriber-tap channel_map=stereo
536870919  module-null-sink  sink_name=transcriber-tap channel_map=stereo
536870920  module-null-sink  sink_name=transcriber-tap channel_map=stereo
```

`pw-link src:output_FL transcriber-tap:playback_FL` and `pw-record --target=transcriber-tap` each
resolve that name independently, and with more than one candidate they can resolve it to **different
nodes**. The recorder then captures a sink nothing is linked to, which is a perfectly healthy
capture of nothing.

The same code, run twice a minute apart with a tone playing:

| | samples | RMS | peak |
|---|---|---|---|
| with the five leaked sinks present | 17039 | **0.0** | **0.0** |
| after `pactl unload-module` on all five | 16698 | 0.005827 | 0.153811 |

That is the whole fault. It matches the artefacts on disk exactly — `rms: 0.0, peak: 0.0,
likely_content: "silent"` in the recent sidecars, against `rms: 0.024665` in the one recorded before
the sinks had accumulated.

### 2.2 `stream.capture.sink` was right after all

The failed capture briefly looked like the previous fix had been backwards, because targeting
`transcriber-tap.monitor` returned audio while the sink-plus-`stream.capture.sink` form returned
zeros. Retested against a **uniquely named** sink with a 440 Hz tone linked in, the result reverses:

| target form | result |
|---|---|
| `--target=<name>.monitor` | `defined target not found`, exit 1 |
| `--target=<name>` + `stream.capture.sink=true` | 63631 samples, **dominant 440.0 Hz** ✅ |
| `--target=no-such-node` + `node.dont-fallback=true` | `defined target not found`, exit 1 ✅ |

So the current target form is correct and stays. The earlier reading was the name collision wearing
a different hat — a reminder that a measurement taken against polluted state measures the pollution.

### 2.3 A failed link reports success

`ApplicationTap.link` tests `if result is not None` against `_run`, which returns `""` on failure and
never returns `None`. Every link attempt is therefore counted as successful, so
`link_all(...) == 0` — the guard that is supposed to catch a tap with nothing in it — cannot fire.
It did not cause this fault, and it is why nothing upstream noticed it.

### 2.4 The compression is an unset property

`vp8enc` is launched with `deadline=1 cpu-used=8 threads=2 keyframe-max-dist=30` and no bitrate, so
`target-bitrate` sits at its default of **256000**. The recordings on disk measure 345–357 kbps at
1080×1064.

Fifteen seconds of a real capture, decoded and re-encoded:

| settings | bitrate | Y-PSNR |
|---|---|---|
| current: VBR, target 256 kbps, `cpu-used=8` | 486 kbps | **35.44 dB** |
| `end-usage=cq cq-level=30`, 8 Mbps ceiling, `cpu-used=4` | 2161 kbps | **44.72 dB** |
| `end-usage=cq cq-level=24`, 8 Mbps ceiling, `cpu-used=4` | 2640 kbps | 45.36 dB |

Nine decibels, on a source that had already been through the 256 kbps encoder once — the gap against
a pristine portal stream is larger. `cq-level=30` takes 94% of the benefit for 82% of the size.

**Constant-quality rather than a fixed bitrate, deliberately.** The portal reports no stream size on
this desktop (D-026), so the pipeline usually does not know the resolution and cannot compute a
bitrate from it. `bits-per-pixel` — the property that exists for exactly this — was measured to have
**no effect** in this build. Constant quality needs no resolution: measured at 4909 kbps for
1080×1064 and 1038 kbps for 640×360 from the same settings, so a small window still produces a small
file.

`static-threshold=100` is added on GStreamer's own recommendation, which the property documentation
states in as many words: *"Recommendation is to set 100 for screen/window sharing"*.

---

## 3. Phases

Each phase ends with a passing suite and a commit.

### Phase 1 — A tap sink no other process can be confused with

Name the sink `transcriber-tap-<pid>-<hex>`, so two of them cannot collide however many leak. Sweep
sinks left behind by processes that are gone, at open, before creating a new one. Fix `link`'s
success test so a link that failed is counted as failed.

**Validated by:** creating two taps concurrently and recording each; a leaked sink from a dead PID
being swept while one belonging to a live process is left alone; a link failure making `link_all`
return zero.

### Phase 2 — A tap that carries nothing says so at the start

Read a few hundred milliseconds from the tap before handing it to the session. Bit-exact zeros
while a linked application node reports `running` means the graph is not delivering, and that is
worth a `MonitorUnavailable` naming the remedy rather than a talk-length silent recording. A source
node that is idle means nothing is playing, which is the user's business and not an error.

**Validated by:** a tap linked to a running source that returns zeros raising; a tap whose sources
are idle not raising; a tap carrying audio passing through untouched.

### Phase 3 — An encoder configured for watching rather than for 2010

Constant-quality VP8/VP9 with a bitrate ceiling, `static-threshold=100`, a real bitrate for
`openh264enc` (whose default is 128 kbps), and a `capture.quality` setting with three values so the
trade against file size is the user's to make.

**Validated by:** the launch line carrying the expected properties for each encoder and each quality;
a real encode at the new settings measuring the bitrate and PSNR this plan predicts.

### Phase 4 — The decisions, written down

D-028 for both faults, `docs/checklist.md` for what is still owed, and the full suite.

---

## 4. What the phases actually found

Two things were not in the plan when it was written, and both were larger than what was.

**The leak was the test suite, not the crashes.** `_open_application_tap` was never stubbed, so any
test opening a window session's audio ran `pactl load-module module-null-sink` against the live
PipeWire daemon, linked whatever the developer happened to be playing into it, and never closed it
— a tap is closed by the session's teardown, which those tests never reach. Measured: five tests in
`test_window_audio.py`, **three leaked sinks per run**. Run the suite a few times and there are
five, which is exactly the state that made every recording that afternoon silent. The suite that
reported the feature green was manufacturing the state that broke it. That is the second time in
two plans that a test reached the real desktop and cost a feature — the first put five screen-share
dialogs on screen — and `tests/conftest.py` now guards both.

**Two faults were sitting in the encoder table, unreached.** `openh264enc ! matroskamux` does not
link at all: openh264 emits a byte-stream elementary stream and Matroska wants AVC, so the entry
needed `h264parse` and did not declare one. And the NV12 capsfilter was keyed off `support.parser`
as a stand-in for "is a hardware encoder", which held only until a software encoder needed a parser
— `openh264enc` accepts I420 and nothing else, so fixing the first alone would have traded a
pipeline that would not link for one that would not negotiate. Neither had ever run here, because
this machine picks `vp8enc`; both would have met the first person whose machine did not.

## 5. Verified

| | before | after |
|---|---|---|
| tap with the fault planted | RMS 0.0, peak 0.0 | RMS 0.071, peak 0.808 |
| sinks left by five window-audio tests | 3 per run | 0 |
| recording bitrate, real 1080x1064 capture | 487 kbps | 2161 kbps |
| Y-PSNR against the source | 35.44 dB | 44.80 dB |
| encoder CPU per 15 s of video | 1.86 s | 2.78 s (5× realtime) |
| suite | 1396 passed | 1419 passed, 4 skipped |

Still owed, and recorded in `docs/checklist.md`: one full window session end to end, which is the
only thing that can confirm a transcript of the window's own sound.
