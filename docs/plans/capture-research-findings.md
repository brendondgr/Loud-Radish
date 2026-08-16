# Research Findings — Window Capture + Transcription on Fedora 44 / KDE / Wayland

**Audience:** an AI coding agent that will modify the capture/transcription application.
**Input:** `capture-research-brief.md` (the measurements taken on the target machine).
**Date of research:** 2026-08-16.
**Target machine (from brief):** Fedora KDE 44, kernel 7.1, Wayland/KWin, AMD Ryzen AI MAX+ 395 /
Radeon 8060S (gfx1151, Strix Halo), PipeWire + `xdg-desktop-portal-kde` (`portal_version: 5`),
GStreamer with `pipewiresrc`, ffmpeg 8.1.2 without `pipewiregrab`.

---

## 0. How to read this document

Every claim is tagged:

| Tag | Meaning |
| --- | --- |
| **[SPEC]** | Comes from a normative specification or upstream API documentation. Treat as authoritative. |
| **[REF-IMPL]** | Comes from a shipping reference implementation (OBS Studio, KDE's own portal backend). Treat as a proven design. |
| **[FIELD]** | Comes from reported behaviour in the wild (issue trackers, distro docs). Reliable as a hazard warning, not as a spec. |
| **[MEASURE]** | Not established by research. The agent must run the stated command on the target machine and record the output before acting. |
| **[DESIGN]** | A recommendation derived from the above. Argued, not cited. |

**Do not skip the [MEASURE] steps.** Section 5 of the source brief is correct: three separate
faults on this machine were well-formed code that failed invisibly, and each was found only by
measuring an artefact. Nothing below overrides that rule. Where this document tells you to change
code, it also tells you what to measure to prove the change worked.

**Priority order** (justification in §8): §1 → §6 → §5 → §4 → §3 → §2.

---

## 1. Corrections to the brief's premises

The brief is largely accurate. Four things in it are wrong or have moved, and one of them is
expensive.

### 1.1 "No hardware video encoder" is almost certainly false — it is a Fedora packaging artifact

This is the single highest-value finding in this document.

**What the brief assumes:** the machine has no hardware video encoder, so VP8 software encoding is
the only option, which is why the recording runs at 15 fps.

**What is actually true:**

- The Radeon 8060S in the Ryzen AI MAX+ 395 is an RDNA 3.5 iGPU (`gfx1151`) and carries AMD's VCN
  media engine, which does hardware H.264, HEVC and AV1 **encode**. **[FIELD]** Reported `vainfo`
  output from this exact chip (gfx1151, Radeon 8060S, Mesa 25.2.2) lists `VAEntrypointEncSlice`
  for H.264 Constrained Baseline / Main / High and HEVC Main / Main10, alongside AV1.
- **Fedora deliberately strips the H.264, H.265 and VC-1 VA-API entry points from its Mesa build**
  for patent reasons. **[FIELD]** On a stock Fedora install, `vainfo` on this hardware will show
  AV1 and JPEG/VP9 decode and *look* like a GPU with no useful encoder. RPM Fusion's
  `mesa-va-drivers-freeworld` restores the stripped entry points; on Fedora 44 the freeworld
  variant is the only `mesa-va-drivers` package left.
- **AV1 encode is royalty-free and is present on stock Fedora Mesa.** **[FIELD]** So even without
  touching RPM Fusion there is very likely a usable hardware encoder on this machine right now.

**[MEASURE] — run these three, record verbatim output:**

```bash
# 1. What the VA driver actually advertises. Look for VAEntrypointEncSlice lines.
vainfo 2>&1 | sed -n '/Supported profile/,$p'

# 2. Which GStreamer VA elements registered. The 'va' plugin only registers an encoder
#    element if the driver advertises the matching entrypoint, so this IS the capability test.
gst-inspect-1.0 va 2>/dev/null | grep -Ei 'enc|postproc'

# 3. Which mesa VA package is installed.
rpm -q mesa-va-drivers mesa-va-drivers-freeworld 2>&1
```

Expected element names in the modern `va` plugin (gst-plugins-bad): `vah264enc`, `vah264lpenc`,
`vah265enc`, `vah265lpenc`, `vaav1enc`, `vajpegenc`, `vapostproc`. **[SPEC]** These supersede the
old `gstreamer-vaapi` elements (`vaapih264enc`, `vaapipostproc`); do not write new code against the
old names.

**[DESIGN] Why this matters more than it looks.** This application transcribes *and* records at the
same time. Whisper `large-v3-turbo` under ROCm is competing for the same machine as a software VP8
encoder. Every core `vp8enc` occupies is a core the transcriber does not get. The brief already
measured 33× realtime on GPU vs 8.3× on CPU for transcription — the same asymmetry applies here.
Moving the video encode onto VCN is likely the cheapest large win available.

**Migration sketch** (see §6.2 for the full pipeline and the even-dimension trap):

```
# from (software):
pipewiresrc ! videoconvert ! videoscale ! vp8enc ! webmmux

# to (hardware, H.264 — needs freeworld drivers):
pipewiresrc ! videoconvert ! videoscale ! video/x-raw,format=NV12,width=W,height=H
           ! vah264enc ! h264parse ! matroskamux

# to (hardware, AV1 — works on stock Fedora Mesa, no RPM Fusion needed):
pipewiresrc ! videoconvert ! videoscale ! video/x-raw,format=NV12,width=W,height=H
           ! vaav1enc ! av1parse ! matroskamux
```

**Caveat to verify by measurement, not by reading:** AV1 hardware encode on RDNA 3.5 is real but
the Mesa/GStreamer path is younger than the H.264 path. If `vaav1enc` produces a file that will not
decode, fall back to `vah264enc` (with freeworld) before falling back to software.

### 1.2 The ScreenCast portal is at version 6 upstream, not 5

**[SPEC]** The current `org.freedesktop.portal.ScreenCast` interface is **version 6**. The machine
reports `portal_version: 5`. Version 6 adds one thing that matters:

- A new per-stream property **`pipewire-serial` (type `t`)**, carrying the PipeWire `object.serial`
  of the stream's node.
- As of version 6 the **node ID in the stream tuple is deprecated for stream targeting**. The
  documented reason: PipeWire node IDs are reused after a node is destroyed, so a long-lived
  session can end up bound to the wrong node across monitor hotplug, resolution or refresh-rate
  changes, or a suspend/resume cycle. `object.serial` is a monotonically increasing 64-bit value
  that is never reused. Consumers are told to prefer the serial and target via
  `PW_KEY_TARGET_OBJECT`. Backends must still populate the node ID for older clients.

**[DESIGN]** The machine is on v5 so `pipewire-serial` will be absent today. Write the code to read
it when present and fall back to the node ID when not, and log which path it took. This costs about
six lines now and removes a whole class of "recording bound to the wrong window after I docked my
laptop" bug later.

### 1.3 The `no_speech_prob = 0.0` reasoning is inverted

The brief reads `no_speech_prob = 0.0` as "the model is confident there is sound." The conclusion it
draws (this is music, not speech) is almost certainly right, but not for that reason.

**[SPEC/FIELD]** `no_speech_prob` is the probability Whisper assigns to a special `<|nospeech|>`
token. It is a well-documented *unreliable* indicator. Published analysis of Whisper hallucination
finds that on non-speech audio the model frequently produces confident output while `no_speech_prob`
stays unexpectedly *low* and `avg_logprob` stays *high* — precisely the combination that lets a
hallucinated segment slip past Whisper's own internal filters. A `no_speech_prob` of 0.0 on music is
a known failure of the indicator, not evidence that speech is present.

This does not change the recommended next action — record 60 seconds of someone visibly talking and
see whether the transcript fills — but it does change what you conclude if the transcript *stays*
empty. See §5.

### 1.4 The portal `size` property was never going to solve the sizing problem

Fully addressed in §2. Summary: `size` is optional *and* is defined in compositor logical
coordinates, so it is not a pixel dimension even on a backend that populates it. Question 3 as
posed has a "no" answer, and the current code's behaviour (don't scale when size is unknown) is
accidentally closer to correct than using `size` would have been.

---

## 2. Question 3 — Does the portal report a usable stream size? **Answered: no, and it never will**

### 2.1 What the specification actually says

**[SPEC]** In the `Start` response, each stream is `(node_id, properties)`. The `size` entry in the
properties dict is defined as:

- **Optional.** A backend is conformant without it.
- A `(width, height)` pair describing the stream **as displayed in the compositor coordinate
  space**, with the spec explicitly warning that this may not be equivalent to a size in a *pixel*
  coordinate space, and that it may differ from the size of the stream itself.

By contrast `position` is documented as available for **monitor streams only**.

### 2.2 The consequence

`size` is a *logical* size. On any display running fractional or integer scaling — which is the
normal case on a modern KDE laptop — the logical size and the pixel size differ by the scale factor.
So:

> **Even a backend that populates `size` does not give you the number you need.** Using it to
> configure an encoder or a scaler is wrong by construction, not merely unavailable.

The recorded 1080×1064 came out of caps negotiation and is real. Whatever the portal would have said
is a different quantity.

**Therefore: stop researching whether newer `xdg-desktop-portal-kde` populates `size`. The answer is
irrelevant. Delete the resolution-ceiling code path that depends on it and rebuild the ceiling on
negotiated caps.** This closes Question 3.

**[FIELD]** Corroborating detail on KDE's implementation: `xdg-desktop-portal-kde` produces the
stream dict from a `ScreencastingStream` object created over KDE's own
`zkde_screencast_unstable_v1` Wayland protocol, with separate code paths for outputs, regions,
virtual outputs and windows. Outputs and regions have a geometry the backend knows a priori; a
window's does not exist until the compositor has composited it and can change at any moment. That
asymmetry is why the property is absent for windows here, and it is structural, not a bug that will
be fixed.

### 2.3 The correct source of truth: negotiated caps

**[DESIGN]** The only authoritative dimensions are the ones GStreamer negotiates. Read them from a
**downstream CAPS event**, not from `get_current_caps()` at an arbitrary moment, because a caps
probe fires on renegotiation too (which you need — see §6.3).

Python/GI sketch:

```python
from gi.repository import Gst


def _on_caps(pad, info, user_data):
    ev = info.get_event()
    if ev.type != Gst.EventType.CAPS:
        return Gst.PadProbeReturn.OK
    caps = ev.parse_caps()
    s = caps.get_structure(0)
    ok_w, width = s.get_int("width")
    ok_h, height = s.get_int("height")
    ok_f, fr_n, fr_d = s.get_fraction("framerate")
    if ok_w and ok_h:
        user_data.on_source_geometry(width, height, (fr_n / fr_d) if ok_f and fr_d else None)
    return Gst.PadProbeReturn.OK


src_pad = pipewiresrc.get_static_pad("src")
src_pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, _on_caps, state)
```

`on_source_geometry` is where the resolution ceiling belongs. It must:

1. **Reject nonsense before using it.** `width < 16 or height < 16 or width > 16384 or
   height > 16384` → log loudly, do not scale, do not silently continue. The 480×16 file was a
   valid command with exit code 0; the only thing that would have caught it is an assertion at this
   point.
2. **Compute the scale in floating point, then round.** The reported `videoscale` integer overflow
   is the signature of computing `w * ceiling / source_w` in ints with a zero or absurd
   `source_w`. Guard the divisor first.
3. **Round to even.** See §6.2 — this is mandatory for H.264/HEVC and it is the trap that will bite
   the moment you move off VP8.
4. **Be idempotent.** It will be called again on every renegotiation.

**Acceptance test for §2 (mandatory):**

```bash
# Record a 20s window capture, then measure the artefact — never trust exit code 0.
ffprobe -v error -select_streams v:0 \
        -show_entries stream=width,height,avg_frame_rate,nb_frames \
        -of default=nw=1 "$OUT"
# Assert: width and height match the window's actual pixel size (± the ceiling you applied),
# both are even, and nb_frames / duration is within 10% of the configured fps.
```

---

## 3. Question 2 — Capturing one window's audio. **Answered: yes, but not via the portal**

This was the brief's real research question. It has a definite answer with a shipping reference
implementation.

### 3.1 What is settled, and what to stop tracking

**[SPEC]** `org.freedesktop.portal.ScreenCast` carries **video only**, at version 6, today. There is
no audio node, no PID, and no application identity in the response.

**[FIELD] Upstream status, so you can stop watching it:**

- `flatpak/xdg-desktop-portal` **issue #957** ("Screencast: send an audio stream to capture
  alongside video"), opened January 2023, is still **open** and labelled `help wanted` / `new api`.
  No implementation exists in any backend.
- A separate proposal for a dedicated per-application audio-capture portal
  (**issue #543**) was **closed**.

**[DESIGN] Conclusion: there is no portal path and none is imminent. Design around it permanently,
not as a stopgap.** Remove "wait for portal audio" from the roadmap.

### 3.2 The mechanism that does work

PipeWire is a graph, and this is the fact everything below rests on:

> An application playing audio is a node with `media.class = Stream/Output/Audio` and output ports.
> **A node's output ports may be linked to more than one destination.** Adding a second link does
> not remove the first.

**[SPEC]** `media.class` values are documented: `Audio/Sink`, `Audio/Source`, `Audio/Duplex`,
`Stream/Output/Audio` (a playback stream), `Stream/Input/Audio` (a capture stream). The session
manager routes based on these.

So you can *additively* tap an application's audio into a private sink of your own **without taking
it off the user's speakers**. This directly retires the brief's stated worry — "a user who suddenly
cannot hear the talk has a worse problem than an imprecise recording" — because the correct design
never moves the user's audio at all.

### 3.3 The reference implementation to copy

**[REF-IMPL]** OBS Studio's *Application Audio Capture (PipeWire)* source
(`dimtpap/obs-pipewire-audio-capture`, merged upstream into obs-studio's `linux-pipewire` module,
shipping since OBS 30). Its architecture, which you should copy:

1. **Connect to PipeWire and add a registry listener.** Track two object types:
   - **Clients** — carry the application identity properties.
   - **Nodes** with `media.class = Stream/Output/Audio` — the actual playback streams, each
     referencing its client.
2. **Create a virtual sink** whose channel configuration matches the current default sink.
   The upstream implementation gives it **`media.class = Audio/Sink/Internal`** specifically so it
   is *hidden from PulseAudio clients* — it will not appear in the user's output picker, will not be
   auto-selected as a default, and will not confuse `pavucontrol`. Copy that choice exactly.
3. **Link the target application's node output ports to the virtual sink's input ports** using
   PipeWire's link factory. The application's pre-existing link to the real sink is untouched.
4. **Capture from the virtual sink.** Either connect your own capture stream to it, or, for a shell
   prototype, `pw-record` its monitor.
5. **Watch the registry continuously.** Applications create and destroy playback nodes constantly —
   a browser typically opens a new node per media element or per tab. The stated reason a
   third-party predecessor script captured *every* node sharing an application name was exactly
   this. **You must link the set of nodes, not one node**, and link new ones as they appear.
6. **Handle default-sink change** by recreating the virtual sink with the new channel layout.

The upstream implementation also exposes, as a user-visible setting, whether to identify
applications by **executable name first** or by **application name first**, plus an "all apps
except these" inversion. That the reference implementation's authors felt they had to make matching
user-configurable is itself a finding — see §3.5.

### 3.4 A shell-level prototype you can run before writing any code

**[MEASURE]** Do this before implementing anything. It proves the mechanism on this machine in about
two minutes and tells you whether the additive-link claim holds here.

```bash
# 0. Play audio from exactly one app (e.g. a browser tab with a talk).

# 1. Find the app's playback node. Note object.serial AND node.id.
pw-dump | jq -r '
  .[] | select(.type=="PipeWire:Interface:Node")
      | select(.info.props."media.class"=="Stream/Output/Audio")
      | "\(.id)\t\(.info.props."object.serial")\t\(.info.props."application.name" // "?")\t\(.info.props."node.name" // "?")\t\(.info.props."media.name" // "?")"'

# 2. Create a private capture sink. object.linger keeps it alive past the creating client.
pactl load-module module-null-sink \
      sink_name=captap object.linger=1 media.class=Audio/Sink \
      channel_map=FL,FR   # -> prints a module id; keep it to unload later

# 3. Additively link the app's outputs to it. Do NOT use `pactl move-sink-input`.
pw-link "<app-node-name>:output_FL" "captap:playback_FL"
pw-link "<app-node-name>:output_FR" "captap:playback_FR"

# 4. THE CRITICAL CHECK: can you still hear the app? You should be able to.
#    If you cannot, stop and report — the additive-link assumption failed on this box.

# 5. Record the tap's monitor for 20s, raw, with explicit format.
timeout 20 pw-record --target captap.monitor \
        -P '{ stream.capture.sink = true }' \
        --raw --format=f32 --rate=48000 --channels=2 /tmp/tap.raw

# 6. Measure the artefact.
python3 - <<'EOF'
import numpy as np
a = np.fromfile('/tmp/tap.raw', dtype='<f4')
print("samples", a.size, "expected", 48000*2*20)
print("nan?", bool(np.isnan(a).any()), "rms", float(np.sqrt((a**2).mean())), "peak", float(np.abs(a).max()))
EOF

# 7. Tear down.
pw-link -d "<app-node-name>:output_FL" "captap:playback_FL"
pw-link -d "<app-node-name>:output_FR" "captap:playback_FR"
pw-cli destroy <module-id-from-step-2>
```

Step 6 is not optional. It is the exact check that caught the `pw-record` AU-header bug: a capture
that ran for the right duration at the right rate and decoded to `NaN`.

### 3.5 The PID mapping question — **it does not have a robust answer, and you should stop trying**

The brief proposes correlating KWin's window→PID mapping with the audio node's
`application.process.id`. Research says this cannot be made reliable. Three independent reasons:

**(a) `application.process.id` is not authenticated. [SPEC]** PipeWire's property reference carries
an explicit warning on client properties: only the `pipewire.*` properties are safe to use for
identifying applications, because clients may set and change the other properties freely.
`application.name` and `application.process.id` are client-declared strings, not facts.

**(b) The authenticated PID is the wrong PID for almost every app you care about. [SPEC]**
`pipewire.sec.pid` *is* set by the protocol and is trustworthy. But the same documentation states
that **for PulseAudio applications it is the PID of the `pipewire-pulse` process**, not the
application. On a Fedora KDE desktop, essentially every media-playing application — browsers,
Electron apps, most games, anything using the PulseAudio API — reaches audio through
`pipewire-pulse`. So the one trustworthy PID collapses to a single shared value across the exact
population of applications you need to distinguish.

**(c) Sandboxing breaks the namespace anyway.** Flatpak and Snap applications run in a separate PID
namespace, so the PID KWin reports and any PID visible on an audio node are not comparable numbers
even in principle.

**[DESIGN] What to build instead — scored matching plus a picker.**

Do not pretend to be certain. Build a ranked candidate list and let the user confirm. This is what
the reference implementation does, and it is what KDE's own portal backend does internally:

**[REF-IMPL]** `xdg-desktop-portal-kde` restores a persisted *window* screencast session by fuzzy
matching — it compares `appId` exactly and then computes a **Levenshtein edit distance** between the
saved window title and each candidate's current title, accepting a best match only when the distance
is under roughly half the title length. Name matching *is* the sanctioned heuristic, even inside the
portal backend itself. (See §6.4 for the operational consequence of this.)

Scoring inputs available to you:

| From KWin (per window) | From PipeWire (per audio node / its client) |
| --- | --- |
| `pid` (see caveats above) | `pipewire.sec.pid` **[trustworthy but usually pipewire-pulse]** |
| `resourceClass` (the appId) | `application.name` **[client-declared]** |
| `resourceName` | `application.process.binary` **[client-declared]** |
| `caption` (title) | `node.name`, `media.name` **[client-declared]** |
| `internalId` (stable UUID) | `object.serial` **[stable, never reused]** |

Suggested scoring, highest first:
1. Exact `pipewire.sec.pid` == KWin `pid` → near-certain (native PipeWire client, unsandboxed).
2. `resourceClass` case-insensitively matches `application.process.binary` or `application.name`.
3. Normalised token overlap between KWin `caption` and PipeWire `media.name` (browsers put the
   page/media title in `media.name`, which often equals part of the window title).
4. Only one `Stream/Output/Audio` node exists at all → use it, but say so in the UI.

Then: present the ranked list, preselect rank 1, and let the user change it. Persist the choice
keyed on `resourceClass` so the second recording of the same app needs no interaction.

### 3.6 Tiered fallback — what to actually ship

**[DESIGN]** Three tiers. Ship tier 0 and tier 1. Never ship tier 2.

| Tier | Mechanism | User's audio | Ship? |
| --- | --- | --- | --- |
| **0** | Default sink monitor (current behaviour) | untouched | Yes — keep as the default and the fallback |
| **1** | Additive link into a hidden `Audio/Sink/Internal` tap | untouched | Yes — this is the feature |
| **2** | `pactl move-sink-input` / null-sink + move | **taken off the user's speakers** | **No** |

Tier 2 is what the brief listed as the "failing that" option. The additive-link design makes it
unnecessary, and it is the only one of the three that can leave a user unable to hear the thing they
are recording. Do not implement it, not even behind a flag.

**Tier-1 failure modes to handle explicitly:**
- Target app has no audio node yet (nothing is playing) → record silence from the tap, and surface
  "no audio stream found for <app>" rather than writing a silent file with no explanation.
- App creates a *new* node mid-recording (new tab, new media element) → registry listener must link
  it. Test this: start recording, then open a second video in the same browser.
- Default sink changes mid-recording (headphones plugged in) → channel layout may change. Simplest
  correct behaviour: keep the existing tap and its layout for the life of the recording; log the
  event into the sidecar.
- **[SPEC]** Set `object.linger` on the tap if it must outlive the creating client, and destroy it
  explicitly on stop. Leaked null sinks accumulate across crashes and are user-visible.

### 3.7 One more sizeable detail: sink monitors are pre-volume by default

**[SPEC]** PipeWire's merger exposes monitor ports carrying the **raw unmodified** input signal;
applying the input channels' volume to the monitor ports is opt-in via `monitor.channel-volumes`
(default off).

Two consequences:
- **Good:** the current tier-0 recording level is independent of the user's volume slider. Turning
  the speakers down does not quieten the recording. Keep it that way.
- **Watch:** the brief measured **peak 1.12** in float32 — above 0 dBFS. Raw pre-volume taps can and
  do exceed unity. Opus encoding of clipped float will audibly distort and can hurt transcription.
  **[MEASURE]** Add peak to the per-recording metrics (§5.4) and, if peak > 1.0 recurs, insert a
  limiter or a fixed −3 dB pad before the encoder rather than a normaliser.

---

## 4. Question 1 — The Monitor pane shows no live picture

Confirming the brief: **this needs no external research and is not an OS problem.** The preview JPEG
is written correctly to disk at 480×270 once a second by the same pipeline that writes the video.
What follows is a diagnosis order and a design fix, because the brief already identified the design
smell correctly and it deserves a concrete replacement.

### 4.1 Diagnosis, in order — [MEASURE]

```bash
# A. Is the route serving during a recording? (404 while idle is correct.)
#    Run this in a loop for 10s WHILE a recording is running.
for i in $(seq 1 10); do
  curl -s -o /tmp/p_$i.jpg -w "%{http_code} %{size_download} " \
       "http://127.0.0.1:8395/api/capture/preview.jpg"
  sleep 1
done; echo
# Assert: ten 200s with non-zero, changing sizes.

# B. Are the frames actually changing, or is one frame being served forever?
md5sum /tmp/p_*.jpg | awk '{print $1}' | sort -u | wc -l
# Assert: > 1. If it prints 1, the server is serving a stale file — the fault is in
# how preview_path is written (write-to-temp + atomic rename, or you will also
# intermittently serve a half-written JPEG).

# C. What headers is the route sending?
curl -sI "http://127.0.0.1:8395/api/capture/preview.jpg"
# Look for Cache-Control. See 4.3.
```

If A returns 200s and B returns > 1, the server is fine and the fault is entirely in the browser.
Go to §4.3.

### 4.2 The design fault the brief already found

The brief's own words: *"The event is emitted once, at the moment capture starts — if that single
emit is lost, or arrives before the recorder reports itself running, the pane never learns there is
anything to show and nothing ever corrects it. This is the most suspicious part of the design."*

That is correct and it is the whole bug class. **A single emit is a fact broadcast once into a lossy
channel with no reconciliation.** Any of these breaks it permanently:

- The WebSocket connects after the emit (page loaded late, or reloaded mid-recording).
- The WebSocket drops and reconnects (laptop sleep, network stack churn) — the client comes back
  with no state and nothing re-sends it.
- The emit races the recorder: `capture.state` goes out with `recording: true` before
  `recorder.state.preview_path` is populated, so the pane fetches, 404s, and gives up.
- A second browser tab opens on the same app and never receives anything.

**[DESIGN] Replace event-only with state-plus-event (the standard fix):**

1. **Add `GET /api/capture/state`** returning the authoritative current state as JSON
   (`recording`, `preview`, `started_at`, `output_path`, `dimensions`, `fps`). This is the source of
   truth.
2. **Client fetches it on every WebSocket open and re-open.** Not just the first.
3. **Keep the WebSocket event** as a latency optimisation, not as the only path.
4. **Re-emit `capture.state` on a heartbeat** (every 2–5 s while recording). A missed emit then
   self-heals within one heartbeat instead of never.
5. **Derive `preview: true` from the file existing**, not from a flag set at start. The 404-while-
   idle behaviour is already correct; make the client tolerate 404 as "not yet" rather than "never".

### 4.3 Browser-side items that will bite even after the server is right

- **A cached 404 is sticky.** If the route returns 404 while idle without cache headers, some
  browsers will not re-request for a while. **Set `Cache-Control: no-store` on the preview route.**
- **Cache-bust the `<img>` src** — `preview.jpg?t=${Date.now()}` — or you will get one frame and
  then a frozen image, which looks exactly like "no live picture" but is a different bug.
- **Do not gate the `<img>` on a flag.** Gate on nothing; set `img.onerror` to schedule a retry.
  A pane that retries forever is strictly better than a pane that gives up once.
- **Background-tab throttling is real.** `setInterval` is throttled (typically to ≥1 s, and can be
  frozen entirely) in backgrounded tabs; `requestAnimationFrame` stops outright. Listen for
  `document.visibilitychange` and **refresh once immediately on becoming visible**, then resume the
  timer. The brief's point 3 is correct — and in the narrow layout the pane exists but may be the
  inactive tab, which is the same problem one level up. Also refresh on tab-panel activation.
- **`<img>` on a JPEG that is being rewritten in place will occasionally render a torn frame.**
  Write to `preview.jpg.tmp` and `os.replace()` onto `preview.jpg`. `os.replace` is atomic on the
  same filesystem.

**Acceptance test:** with a recording running, (a) hard-reload the page — picture appears within one
heartbeat; (b) open a second tab — picture appears there too; (c) switch to another tab for 30 s and
back — picture is live within 1 s; (d) kill and restart the WebSocket — picture recovers.

---

## 5. The transcription result — what to do about it

### 5.1 The brief's ten-second test is the right first move. Do it first.

Record 60 s of a window playing someone clearly talking (a lecture, a podcast, a news clip) and
transcribe it. Nothing else in this section is worth doing until you know the answer.

- **Transcript fills** → the audio chain is finished. Go to §3 (per-app audio) and §5.3
  (make the transcriber report *why* it produced nothing).
- **Transcript still empty** → the fault is in the transcriber's configuration or its input
  handoff, and §5.2/§5.3 apply immediately.

### 5.2 The spectrum reading is sound

The brief's band table — 17.9 % of energy below 100 Hz, peak at 52 Hz, broadband above it — is a
music/game profile. A human fundamental sits at roughly 85–255 Hz and speech carries very little
energy below 100 Hz. Combined with "amplifying 8× changes nothing", the diagnosis "the thing being
captured had almost no speech in it" is well-supported. The only thing to fix is the
`no_speech_prob` reasoning (§1.3), which does not change the conclusion.

### 5.3 Configuration items that will change behaviour — [SPEC/FIELD]

**The single biggest trap: batched inference silently ignores most quality knobs.**

**[SPEC]** In the CTranslate2/faster-whisper family, **batched inference uses a VAD filter and
ignores** `compression_ratio_threshold`, `logprob_threshold`, `no_speech_threshold`,
`condition_on_previous_text`, `prompt_reset_on_temperature`, `prefix`, and
`hallucination_silence_threshold`.

> **[MEASURE]** Determine right now whether this app uses `BatchedInferencePipeline` (or
> `whisper-ctranslate2 --batched`). If it does, every threshold in the config is dead code and
> tuning them is wasted effort. Record the answer in the sidecar so nobody re-litigates it.

For the sequential path, the parameters that actually matter here:

| Parameter | Recommendation | Why |
| --- | --- | --- |
| `vad_filter` | `True` (Silero) | Removes non-speech before Whisper sees it — the direct fix for "recording had no speech". **[FIELD]** Caveat: near-silence is not always removed, and running `noisereduce` beforehand has been reported to make VAD dramatically *worse* (short false-positive regions ballooning into long ones). Do not add denoising as a fix. |
| `condition_on_previous_text` | `False` for anything over ~10 min | **[FIELD]** The default `True` feeds each segment's text forward as a prefix; on long or variable audio this causes repetition loops. A 5 m 20 s recording is borderline; a lecture recording is not. |
| `compression_ratio_threshold` | `2.4` (pin explicitly) | Catches degenerate repeated output. |
| `log_prob_threshold` | `-1.0` (pin explicitly) | Drops low-confidence segments that correlate with hallucination. |
| `no_speech_threshold` | `0.6` (pin explicitly) | Note §1.3 — this is a weak filter; do not rely on it alone. |

**[FIELD] Known artifact:** when the internal thresholds *do* fire on non-speech, the reported
failure mode is not silence but a stock hallucinated phrase (the canonical example being
subtitle-credit boilerplate). Published work on this proposes maintaining a **"bag of
hallucinations"** — a denylist of the phrases Whisper emits on non-speech — and filtering them out,
with best results when combined with VAD. **[DESIGN]** Add a small denylist and drop any segment
that matches it after normalisation. Two segments over five minutes is exactly the shape this
produces.

### 5.4 The real fix: make an empty transcript self-explanatory

`state: done, transcribed_seconds: 322.46, 2 segments` is unfalsifiable from the outside. That is
the bug worth fixing regardless of what the 60-second test shows.

**[DESIGN] Write an audio-characterisation sidecar next to every recording**, computed once from the
captured audio before transcription. Promote what the brief did ad hoc into a permanent artefact:

```json
{
  "audio": {
    "duration_s": 320.4,
    "sample_rate": 48000,
    "channels": 2,
    "rms": 0.036,
    "peak": 1.12,
    "clipped": true,
    "frames_above_-50dBFS_pct": 100.0,
    "dominant_hz": 52,
    "band_energy_pct": {
      "20-100": 17.9, "100-300": 11.8, "300-1000": 19.9,
      "1000-3000": 27.9, "3000-6000": 19.0, "6000-8000": 3.5
    },
    "speech_band_ratio": 0.41,
    "likely_content": "music_or_game"
  },
  "transcription": {
    "backend": "faster-whisper",
    "model": "large-v3-turbo",
    "batched": false,
    "duration_s": 320.4,
    "duration_after_vad_s": 3.1,
    "speech_ratio": 0.0097,
    "segments": 2,
    "segments_dropped_by_denylist": 0,
    "state": "done_no_speech"
  }
}
```

Two specific fields carry most of the value:

- **`duration_after_vad_s` / `speech_ratio`.** faster-whisper exposes the post-VAD duration
  separately from the total. Reporting only `transcribed_seconds: 322.46` conflates "we processed
  322 s" with "there were 322 s of speech". With both, `2 segments` stops looking like a broken
  transcriber and starts looking like an accurate one.
- **A distinct terminal state.** `done` should not be the state for "finished and found nothing".
  Add `done_no_speech`, chosen when `speech_ratio` is below a threshold. The UI can then say *"no
  speech detected in this recording"* instead of showing an empty transcript that looks like a
  crash. This is the difference between the user filing a bug and the user understanding what
  happened.

**[SPEC] A free debugging tool you are not using:** PipeWire nodes accept a `debug.wav-path`
property that makes the stream write its raw samples to a WAV file. That is a first-class
"measure the artefact" facility for the audio path, available without touching your code — useful
the next time an audio capture looks fine and decodes to garbage.

---

## 6. Findings the brief did not ask for, in descending order of expected damage

### 6.1 (Covered in §1.1) The hardware encoder

Highest expected value. See §1.1.

### 6.2 Odd dimensions will break H.264/HEVC the day you switch encoders

The recording is 1080×1064 — both even, so VP8 and the current pipeline are fine. **This is luck.**
Window sizes are arbitrary; a window can be 1081×1063.

H.264 and HEVC use 4:2:0 chroma subsampling and **require even width and height**. VA-API encoders
and `vapostproc` are stricter still about dimensions and formats than software encoders are.

**[DESIGN] Mandatory in `on_source_geometry` (§2.3):**

```python
def _even(n: int) -> int:
    return n - (n & 1)


target_w = _even(max(16, min(source_w, ceiling_w)))
target_h = _even(max(16, min(source_h, ceiling_h)))
```

and then set the scaler's capsfilter to exactly `target_w × target_h`. Use `videoscale
add-borders=false` if you would rather crop one pixel than letterbox — but pick one deliberately,
because `videoscale` adds borders by default when the aspect ratio does not match, which is a
silent visual change nobody will notice until a user complains that their recording has black bars.

### 6.3 Window resize mid-recording is a live failure mode

**[FIELD]** `pipewiresrc` renegotiation failures are widely reported and take several shapes:

- `handle_format_change: finish format with error`, `stream error: unhandled format` — reported
  against Kooha on cursor movement / format change, with GStreamer criticals about null caps in
  `gst_caps_intersect_full` and DMA-BUF format fixation immediately before.
- `pw.stream: error (-32) no more input formats` → `basesrc: streaming stopped, reason
  not-negotiated (-4)` → pipeline never reaches PLAYING. Reported against multiple recorders. One
  well-diagnosed instance traced to a compositor advertising **10-bit** formats that `pipewiresrc`
  could not handle, breaking every GStreamer-based recorder and WebRTC screen share on that system.

This machine is AMD + KWin + DMA-BUF, which is squarely inside the affected configuration space.

**[DESIGN] Defensive pipeline structure:**

```
pipewiresrc fd=<fd> path=<node-id> do-timestamp=true keepalive-time=1000
  ! video/x-raw,format={BGRx,RGBx,BGRA,RGBA}      # <- constrain BEFORE anything downstream
  ! queue leaky=downstream max-size-buffers=8
  ! videoconvert
  ! videoscale
  ! video/x-raw,format=NV12,width=<even>,height=<even>,framerate=<n>/1   # <- fixed for the
  ! <encoder>                                                           #    whole recording
  ! <parser> ! <muxer> ! filesink
```

Three things that pipeline buys you:

1. **The early format capsfilter** keeps negotiation away from 10-bit and exotic-modifier formats
   that `pipewiresrc` mishandles. Restrict to the packed 8-bit RGB formats you know work.
2. **The fixed capsfilter before the encoder** means a window resize is absorbed by `videoscale` and
   the encoder never sees a caps change. Encoders generally cannot handle mid-stream resolution
   changes; the widely-given advice for that situation is to rebuild the pipeline, which you do not
   want to do mid-recording.
3. **`keepalive-time=1000`** — **[SPEC]** this property makes `pipewiresrc` periodically resend the
   last buffer (0 disables). Without it, a completely static window can starve the muxer of buffers
   and produce timestamp gaps or a truncated file. At 15 fps on a mostly-idle window this is a real
   risk. **`do-timestamp=true`** is off by default and you almost certainly want it on.

Other `pipewiresrc` properties worth knowing: **[SPEC]** `fd` (the portal's fd, default −1),
`path` (the node id), `client-properties`, `min-buffers` (default 8), `max-buffers`,
`always-copy`, `use-bufferpool`, `provide-clock`, `num-buffers`. Run `gst-inspect-1.0 pipewiresrc`
on the target machine and check whether a `target-object` property exists — if it does, prefer it
over `path` in line with §1.2.

**[DESIGN] Also add a bus watch.** Attach `GST_MESSAGE_ERROR` / `GST_MESSAGE_WARNING` handlers that
write the element name, error domain, message and debug string into the recording's sidecar JSON.
A mid-recording renegotiation failure currently produces a short file and no explanation, which is
the same failure class as the 480×16 file: a valid-looking artefact with no signal attached.

### 6.4 The restore-token pattern, and why KDE's window restore is fragile

The brief says the "five portal dialogs per recording" problem is fixed. Two things to verify the
fix against, because they are the standard ways this regresses:

**(a) [SPEC] The restore token is single-use.** You pass `restore_token` plus `persist_mode` to
`SelectSources`; if permission to persist is granted, a **new** token comes back in the `Start`
response. The old token is invalidated the moment it is used. If you persist the token once and
replay it, you will get a dialog on the second recording and every one after. Persist the *new*
token after every `Start`.

`persist_mode` values: `0` = do not persist, `1` = persist while the app runs, `2` = persist until
explicitly revoked.

**(b) [REF-IMPL] KDE's window restore is a fuzzy match and can legitimately fail.** As described in
§3.5, `xdg-desktop-portal-kde` restores window sessions by matching `appId` exactly and then
choosing the candidate with the smallest Levenshtein distance to the saved title, accepting only if
the distance is under about half the title length — and it requires the number of restored windows
to equal the number saved, or it discards the restore and prompts.

Operational consequences, which you should design for rather than fight:
- Restoring a *browser* window whose title changed (different tab) will often fail to match and
  re-prompt. This is correct behaviour, not a bug in your code.
- Restore is inherently more reliable for monitors than for windows.
- **Handle re-prompting gracefully.** Do not treat "the portal showed a dialog" as an error state.
  Log which path was taken (restored silently / restored after prompt / prompted fresh) so this is
  diagnosable from the sidecar instead of from user reports.

### 6.5 The `pw-record` AU-header bug was documented behaviour — pin the format anyway

**[SPEC]** The `pw-cat`/`pw-record` manual states that when output goes to STDOUT and no container
is specified, **the AU container is used** (because it is streamable and preserves format, rate and
channels). The brief's fix — `--container=raw` — is correct and matches the documentation.

**[DESIGN] Belt and braces**, since this bug decoded to `NaN` and ran for the right duration:

- Pass `--raw` **and** `--format=f32 --rate=48000 --channels=2` explicitly. Never rely on defaults.
  (Defaults are `s16`, 48000, 2 channels — an unpinned `--format` silently gives you `s16`, which
  will "work" and be wrong if you interpret it as float.)
- **Assert byte count.** For raw f32 stereo at 48 kHz, expect `48000 × 2 × 4 = 384000` bytes/sec.
  Compare against wall-clock duration with a ±2 % tolerance and fail loudly on mismatch.
- **Assert `not numpy.isnan(samples).any()`** on the first second of every capture, in the code, not
  in a test. This is a 3-line check that would have caught the original bug at runtime.

Other useful `pw-record` flags: **[SPEC]** `--target=<object.serial | node.name>`;
**`--target=0` means "do not try to link this node"** (essential if you intend to make the links
yourself, as in §3.4); `--latency=VALUE[s|ms|us|ns]` (default 100 ms); `-P/--properties` for extra
stream properties as JSON (this is where `stream.capture.sink=true` goes); `--volume`;
`--sample-count` to stop after N samples.

### 6.6 Session/graph properties worth knowing when you build the tap

**[SPEC]** Relevant PipeWire node properties for §3:

| Property | Use |
| --- | --- |
| `target.object` | Where a node should link to — a `node.name` or an `object.serial`. Prefer this over the deprecated `node.target`. |
| `node.dont-reconnect` | If the target is destroyed, destroy this node too, and never move it to another sink/source. |
| `node.dont-move` | Prevent the node's target being changed via metadata. |
| `node.dont-fallback` | If linking to the specified target fails, do **not** silently fall back to the default target. **Set this on your capture stream** — a silent fallback to the default sink is exactly how you would end up recording the wrong audio and not knowing. |
| `node.passive` | Passive nodes do not keep sinks/sources busy; they suspend together with the sink. Right for a tap that should not prevent power management. |
| `object.linger` | Keep the object alive after its creator disconnects. |
| `media.class` | `Audio/Sink/Internal` for a tap you want hidden from PulseAudio clients. |
| `debug.wav-path` | Dump the stream's raw samples to a WAV for debugging. |
| `stream.capture.sink` | Set `true` on a capture stream to capture from a sink rather than a source. |

`node.dont-fallback` deserves emphasis: without it, the default PipeWire behaviour on a failed link
is to connect you to the default target instead. That produces a recording that plays back fine and
contains the wrong audio — the exact failure signature the brief warns about in §5.

### 6.7 KWin window enumeration — the concrete recipe

**[SPEC]** The KWin scripting API's `Window` object exposes `pid` (int, since 5.20), `caption`,
`resourceClass`, `resourceName`, `internalId`, plus geometry. `EffectWindow` also exposes `pid`
(since 5.18).

**[FIELD]** Working D-Bus invocation pattern (Plasma 6):

```bash
# 1. Write the script to a file kwin can read.
# 2. Load it — returns a script id, or -1 if a script with that name is already loaded.
dbus-send --session --dest=org.kde.KWin --print-reply=literal \
  /Scripting org.kde.kwin.Scripting.loadScript \
  string:/path/to/script.js string:my-unique-name
# 3. Run it.  KWin 6 object path: /Scripting/Script<id>   (KWin 5 used: /<id>)
#    Interface: org.kde.kwin.Script, methods run() then stop()
# 4. Unload: org.kde.kwin.Scripting.unloadScript(name)
```

Script body — enumerate and call back:

```javascript
// Plasma 6: workspace.windowList() ; Plasma 5: workspace.clientList()
// workspace.stackingOrder also works and gives you z-order for free.
function dump() {
  var out = [];
  for (let w of workspace.windowList()) {
    out.push(w.internalId + "\t" + w.pid + "\t" +
             w.resourceClass + "\t" + w.resourceName + "\t" + w.caption);
  }
  callDBus("com.example.CaptureApp", "/com/example/CaptureApp",
           "com.example.CaptureApp", "WindowList", out.join("\n"));
}
dump();
```

Your application publishes a tiny D-Bus service to receive `WindowList`. That is the only way to get
data *out* of a KWin script.

Gotchas: `loadScript` returns −1 if that script name is already loaded — unload first, then reload.
`console.log`/`console.warn` from a KWin script goes to the journal
(`journalctl _COMM=kwin_wayland`), which is a useful fallback when the D-Bus callback is what is
broken.

---

## 7. Consolidated answers to the brief's three questions

| # | Question | Answer |
| --- | --- | --- |
| **1** | Why does the Monitor pane show no live picture? | Not researched externally (correctly). The design fault the brief identified — a single, unreconciled `capture.state` emit — is the bug class. Replace with state-plus-event: add `GET /api/capture/state`, fetch on every WS connect, heartbeat the event. Then fix the browser-side items in §4.3 (`Cache-Control: no-store`, cache-busted src, `onerror` retry, `visibilitychange`, atomic rename of the JPEG). |
| **2** | Can a specific window's audio be captured? | **Yes, but never through the portal.** ScreenCast is video-only at v6 and upstream issue #957 is still open and unimplemented. The working mechanism is PipeWire's graph: additively link the target app's `Stream/Output/Audio` node ports into a hidden `Audio/Sink/Internal` tap and record the tap. Shipping reference implementation: OBS's Application Audio Capture (PipeWire). **Never move the stream** — additive linking leaves the user's audio intact. **PID mapping is not solvable**: `application.process.id` is client-declared and unauthenticated, `pipewire.sec.pid` resolves to the `pipewire-pulse` PID for nearly every relevant app, and sandboxes break the namespace. Build scored name matching plus a user picker, exactly as both OBS and KDE's own portal backend do. |
| **3** | Does the portal report a stream size on any KDE version? | **Wrong question — abandon it.** `size` is optional *and* is specified in compositor logical coordinates, explicitly not pixels, and explicitly permitted to differ from the stream's actual size. It would be the wrong number even where present. The only source of truth is the negotiated caps, read from a downstream `GST_EVENT_CAPS` probe, which also gives you renegotiation handling for free. |

---

## 8. Ordered work plan

Each item states its acceptance test. **An item is not done until its artefact has been opened and
measured** — never until a command exited 0.

### P0 — Settle the premise (minutes, blocks everything else)

1. **Run the 60-second talking-window test** (§5.1). Record the result in the repo.
2. **Run the three hardware-encoder probes** (§1.1 `[MEASURE]`). Record `vainfo` and
   `gst-inspect-1.0 va` output verbatim.
3. **Determine whether the transcriber uses batched inference** (§5.3). If yes, note that the
   threshold config is inert.

*Acceptance: three recorded answers committed alongside the brief.*

### P1 — Stop shipping invisible failures (highest damage-reduction per line)

4. **Caps-driven geometry** (§2.3 + §6.2). Move sizing to a `GST_EVENT_CAPS` probe. Add the
   bounds assertion (16 ≤ dim ≤ 16384), the guarded divisor, and even-rounding.
   *Acceptance:* `ffprobe` on a fresh recording shows the window's real pixel size, both dimensions
   even; deliberately provoke a zero/absent source size in a unit test and confirm it raises rather
   than producing a 480×16 file.
5. **Audio format pinning + NaN/length assertions** (§6.5).
   *Acceptance:* a capture whose byte count deviates by >2 % from `rate × channels × 4 × seconds`,
   or whose first second contains any NaN, fails loudly at runtime.
6. **Bus-watch error capture into the sidecar** (§6.3).
   *Acceptance:* kill the source window mid-recording; the sidecar contains the element name and
   GStreamer debug string.
7. **Audio-characterisation sidecar + `done_no_speech` state** (§5.4).
   *Acceptance:* re-run the existing 5 m 20 s recording; the sidecar reproduces the brief's own band
   table and the state reads `done_no_speech`, not `done`.

### P2 — The Monitor pane (self-contained, visible to the user)

8. **`GET /api/capture/state` + fetch-on-connect + heartbeat** (§4.2).
9. **Browser-side fixes** (§4.3): `Cache-Control: no-store`, cache-busted src, `onerror` retry,
   `visibilitychange`, atomic JPEG rename.
   *Acceptance:* the four scenarios at the end of §4.3 all recover.

### P3 — Hardware encode (largest performance win)

10. **Add a VA encode path with software fallback** (§1.1, §6.2). Select at startup by probing
    which `va*enc` elements registered; log the choice.
    *Acceptance:* recording at the same resolution shows a measurable drop in CPU time
    (`/usr/bin/time -v` on the recorder process) and the output file plays and `ffprobe`s clean.
    Compare transcription throughput before/after on the same input.
11. **Add the `format` capsfilter immediately after `pipewiresrc`** and the fixed pre-encoder
    capsfilter (§6.3).
    *Acceptance:* resize the source window repeatedly during a 60 s recording; the file is 60 s
    long, is the fixed dimensions throughout, and the frame count matches.

### P4 — Per-window audio (the feature)

12. **Run the shell prototype** (§3.4) and confirm the additive-link assumption on this machine —
    specifically step 4, that the user can still hear the app.
13. **Implement tier 1** (§3.3): registry listener, hidden `Audio/Sink/Internal` tap, link the *set*
    of matching nodes, relink on new nodes, capture the tap, explicit teardown. Set
    `node.dont-fallback` on the capture stream (§6.6).
14. **Implement scored matching + picker** (§3.5), persisted per `resourceClass`.
15. **KWin enumeration over D-Bus** (§6.7) to supply the window-side scoring inputs.
    *Acceptance:* with two applications playing different audio simultaneously, a recording of
    window A contains only A's audio (verified by spectrum, not by ear), both applications remain
    audible on the user's speakers throughout, and no null sink survives the recording's end
    (`pactl list short modules | grep null-sink` is clean).

### P5 — Robustness

16. **`pipewire-serial` preference with node-id fallback** (§1.2).
17. **Restore-token rotation** (§6.4a) — persist the new token after every `Start`; log which restore
    path was taken.
18. **Hallucination denylist + explicit threshold pinning** (§5.3), *only if* the transcriber is not
    running batched inference.

### Not to be done

- Researching whether newer `xdg-desktop-portal-kde` populates `size` (§2.2).
- Watching upstream for portal audio support (§3.1).
- Implementing `pactl move-sink-input` / stream-moving audio capture, at any priority, behind any
  flag (§3.6).

---

## 9. Testing doctrine (extending the brief's §5)

The brief's rule — *the acceptance test should be "open the artefact and measure it", never "the
command succeeded"* — is correct and this document is written to it. Two additions:

**9.1 Assert in production code, not only in tests.** A 1291-test suite passed while the feature was
broken in every user-visible respect, because the tests asserted the shape of the launch line and
never opened the file. Tests that check what your code *does* cannot catch what the *environment*
does. The NaN check, the byte-count check, and the dimension bounds check belong in the recorder at
runtime, where they see real output on a real machine, not in a test suite that sees neither.

**9.2 Write a measured artefact summary next to every recording.** The sidecar JSON in §5.4 is not
documentation — it is the regression suite. Once every recording carries its own measured
dimensions, luminance, RMS, peak, spectrum and speech ratio, a whole class of "it seemed to work"
becomes a diff between two JSON files. That is the cheapest possible version of the discipline the
brief already arrived at the hard way.

---

## 10. Sources

**Specifications and upstream API documentation**
- ScreenCast portal interface (v6), incl. `size`, `position`, `pipewire-serial`, `restore_token`, `persist_mode` — https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html
- ScreenCast backend (impl) interface — https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.impl.portal.ScreenCast.html
- `pw-cat(1)` / `pw-record` manual (`--target`, `--target=0`, `--raw`, `--container`, `-P`, AU-on-STDOUT default) — https://docs.pipewire.org/page_man_pw-cat_1.html
- `pipewire-props(7)` (`media.class`, `target.object`, `node.dont-fallback`, `node.dont-move`, `node.dont-reconnect`, `node.passive`, `object.linger`, `monitor.channel-volumes`, `debug.wav-path`, `application.process.id`, `pipewire.sec.pid`, and the warning that only `pipewire.*` props are safe for identifying applications) — https://docs.pipewire.org/page_man_pipewire-props_7.html
- PipeWire loopback module — https://docs.pipewire.org/page_module_loopback.html
- KWin scripting API (`Window.pid`, `workspace.windowList`) — https://develop.kde.org/docs/plasma/kwin/api/
- GStreamer `va` plugin element index — https://gstreamer.freedesktop.org/documentation/va/index.html
- GStreamer `videoscale` (border behaviour) — https://gstreamer.freedesktop.org/documentation/videoconvertscale/videoscale.html

**Upstream status**
- Portal audio-alongside-video request (open) — https://github.com/flatpak/xdg-desktop-portal/issues/957
- Per-application audio portal proposal (closed) — https://github.com/flatpak/xdg-desktop-portal/issues/543
- Serial-vs-node-id investigation — https://github.com/flatpak/xdg-desktop-portal/issues/979
- xdg-desktop-portal releases (pipewire-serial landing) — https://github.com/flatpak/xdg-desktop-portal/releases

**Reference implementations**
- OBS PipeWire audio capture — architecture — https://deepwiki.com/dimtpap/obs-pipewire-audio-capture/4.1-capturing-application-audio
- OBS PipeWire audio capture — pipeline — https://deepwiki.com/dimtpap/obs-pipewire-audio-capture/2.2-audio-capture-pipeline
- Upstream merge PR (virtual-sink design rationale) — https://github.com/obsproject/obs-studio/pull/6207
- `xdg-desktop-portal-kde` `screencast.cpp` (window restore via appId + Levenshtein title match; `stream->metaData()`) — https://github.com/KDE/xdg-desktop-portal-kde/blob/master/src/screencast.cpp
- `xdg-desktop-portal-kde` `waylandintegration.cpp` (`zkde_screencast_unstable_v1`; separate output/region/virtual/window stream paths) — https://github.com/KDE/xdg-desktop-portal-kde/blob/master/src/waylandintegration.cpp
- KWin script over D-Bus, working Plasma 5 and 6 recipes — https://rudd-o.com/linux-and-free-software/how-to-raise-a-window-under-wayland-or-x11-when-using-kde-kwin-plasma

**Field reports**
- `vainfo` on gfx1151 / Radeon 8060S showing H.264 + HEVC `VAEntrypointEncSlice` — https://github.com/alvr-org/ALVR/issues/3017
- Strix Halo H.264 encode requiring RPM Fusion freeworld Mesa — https://github.com/LizardByte/Sunshine/issues/4464
- Fedora Hardware Video Acceleration wiki — https://fedoraproject.org/wiki/Hardware_Video_Acceleration
- Fedora 44 codec setup (freeworld is now the only mesa-va-drivers package) — https://computingforgeeks.com/rpm-fusion-fedora-44-codecs/
- `pipewiresrc` property list — https://github.com/SeaDve/Kooha/issues/347
- `handle_format_change` / "unhandled format" on renegotiation — https://github.com/SeaDve/Kooha/issues/323
- "no more input formats" → not-negotiated — https://github.com/vkohaupt/vokoscreenNG/issues/392
- Root-caused instance: compositor advertising 10-bit formats pipewiresrc cannot handle — https://github.com/YaLTeR/niri/issues/3145
- Multiple audio nodes per application (browsers/games) — https://github.com/PhantomShift/PipewireAudioCaptureScript

**Transcription**
- Batched inference ignores compression/logprob/no_speech/condition_on_previous_text/hallucination thresholds — https://pypi.org/project/whisper-ctranslate2/
- Thresholds and `vad_filter` interaction — https://github.com/SYSTRAN/faster-whisper/discussions/349
- Silero VAD near-silence caveat; denoising making it worse — https://github.com/SYSTRAN/faster-whisper/issues/843
- Hallucinated output can carry high `avg_logprob` and low `no_speech_prob` — https://arxiv.org/pdf/2606.07473
- Bag-of-Hallucinations filtering on non-speech audio — https://arxiv.org/pdf/2501.11378
