# Five Faults in One Recording

*Status: in progress — phases are marked complete only once their validation has run.*
*Owner: this plan is the handoff artifact. Resume from the phase table at the bottom.*

## 1. Introduction

Five problems reported against one window recording,
`20260829-190626-ecce6b1e6658`. Four are faults in this repository and one is a property of how
capture is started that has been visible all along and never corrected. None of them lost a
recording: the session on disk holds a complete video, a complete transcript from both passes, and
a muxed file with sound in it. Every one of the five is the application **describing** that
recording incorrectly, or storing it more clumsily than it needs to.

They are unrelated in cause and worth stating separately, because three of them look like the same
bug from the outside — "the app is lying about what it has" — and fixing one would leave the other
two reporting the same lie by a different route.

## 2. Gaps & Unanswered Questions

- **What does the "Audio" chip mean?** *Assumption*: "this recording has sound you can play",
  not "there is a WAV file". A successful transcription pass deletes its own WAV by design
  (retention is off by default), so the literal reading marks every *healthy* recording as having
  no audio — which is exactly backwards, and it is why the web-app export refused a session that
  had everything.
- **Which timeline should the muxed file use — the video's or the audio's?** *Assumption*: the
  **audio's**. Transcript timestamps are audio-relative, and the exported web application syncs the
  transcript to the muxed file. Aligning to the video would silently invalidate every timestamp in
  the export. So the video is delayed to meet the audio, rather than the audio trimmed to meet the
  video; the recording opens on its first frame held still for as long as the capture took to
  start, which is a truthful picture of that moment.
- **Should the audio still start before the window picker?** *Assumption*: yes, unchanged. The
  ordering is deliberate and commented as such — the portal shows a dialog and waits for a human,
  and starting audio afterwards would lose the first words of a talk to however long someone takes
  to choose a window. That decision is *why* the offset exists, which makes measuring and
  correcting it the fix rather than reordering the start.
- **Can the offset be measured after the fact for recordings already on disk?** No — the two start
  times are not recorded in any existing artefact, so a file made before this change cannot be
  corrected automatically. Those recordings keep whatever drift they have. From now on the measured
  offset is written to the log at INFO when a recording is combined; it is deliberately *not* given
  a file of its own, since the same report also asked for fewer files in a recording folder, not
  more.

## 3. Hierarchical Step-by-Step Instructions

### Step 1: A stopped session stops calling itself "recording now"

- **Locations**: `web/backend/app/routes/sessions.py` (`_running_key`, `list_sessions`,
  `delete_session`); `tests/data/test_session_archive.py`.
- **Rationale**: `_running_key` answers "which row is still being written" by reading
  `SessionManager.store` — and since D-031 that property deliberately keeps returning the *last*
  session's store after it ends, so every finished session reports itself as recording. It gets the
  badge and, worse, a disabled Delete button. The remedy is to ask the question that is actually
  meant: `manager.is_running`.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Five Recording Faults (1/5) Complete: a session that has stopped no
  longer reports itself as still recording.

### Step 2: "Audio" means sound you can play, and the counts stop double-counting

- **Locations**: `web/backend/app/services/recording/layout.py` (`RecordingLayout.has_audio`, new
  `playable_audio`); `web/backend/app/services/transcript/archive.py` (`media_for`, `describe`);
  `tests/data/test_session_archive.py`, `tests/transcription/test_recording_layout.py`.
- **Rationale**: two separate misreports on the same row. The Audio chip tests for `audio.wav`,
  which a *successful* pass deletes — so it reads false precisely when everything went right, and
  it dragged `exportable` false with it, hiding the web-app export from the only recording that
  qualified for it. And `segments`/`words` count every row in the table across both transcription
  passes, so a session with a live pass and a post-capture pass reports roughly twice what it holds
  — 46 segments for a recording of 26. Every other read path uses `latest_segments()`; this one was
  missed when that rule landed (D-022, and again in Part 3g).
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Five Recording Faults (2/5) Complete: a recording whose sound survives
  in the video reports that it has audio, and the counts describe one pass rather than two.

### Step 3: One video file, not two

- **Locations**: `web/backend/app/services/capture/mux.py` (`combine`, new `_has_both_streams`);
  `web/backend/app/services/session/manager.py` (`_mux_if_wanted`);
  `tests/transcription/test_capture_mux.py`.
- **Rationale**: `_mux_if_wanted` passes `keep_sources=True`, so every window recording leaves both
  `video.webm` and `video-with-audio.webm` — the same footage twice, one copy silent, 27 MB where
  13 MB would do. The caution behind it is right and only the threshold is wrong: the source must
  survive until the muxed file is known good, not forever. Verifying that the output holds both a
  video and an audio stream, and only then removing the silent original, keeps the protection and
  drops the duplicate.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Five Recording Faults (3/5) Complete: a finished window recording
  leaves one video file, and the silent original goes only once the combined one is verified.

### Step 4: The video is aligned to the audio it was recorded with

- **Locations**: `web/backend/app/services/recording/sink.py` (`WavSink.first_write_monotonic`);
  `web/backend/app/services/capture/recorder.py` (`RecorderState.stopped_at`);
  `web/backend/app/services/capture/mux.py` (`probe_duration`, `combine(video_lag_s=…)`);
  `web/backend/app/services/session/manager.py` (`_mux_if_wanted`, `_measure_video_lag`);
  `tests/transcription/test_capture_mux.py`.
- **Rationale**: audio capture starts before the screen-cast portal is even asked, deliberately, so
  the first words of a talk are not lost while someone chooses a window. The video therefore begins
  somewhere between a fraction of a second and however long that dialog was on screen *after* the
  audio does — measured at ≈2.1 s on the reported recording — and the mux aligns both inputs at
  zero, so the sound runs ahead of the picture by exactly that gap. The offset is measured rather
  than assumed: the video's true start is its stop time minus its own encoded duration, which
  sidesteps the unmeasurable warm-up between spawning GStreamer and its first frame. The output
  keeps the **audio's** timeline, because that is the timeline transcript timestamps are in and the
  exported web application syncs against it.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Five Recording Faults (4/5) Complete: the muxed recording aligns the
  video with the audio it was captured alongside, on a measured offset.

### Step 5: Documentation, tests, and the merge

- **Locations**: `docs/documentation.md`, `docs/checklist.md`, `docs/api-contract.md`,
  `docs/data-flow.md`, `docs/structure.md`; `tests/`.
- **Rationale**: the repository contract requires documentation to ship with the code it describes,
  and three of these five are corrections to behaviour the docs currently assert.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated, push
  changes to GitHub stating: Five Recording Faults (5/5) Complete: documentation and tests updated,
  and the branch merged into main.

## 4. Deliverables

| Deliverable | Description | Location |
| --- | --- | --- |
| Running-key repair | "Recording now" follows `is_running`, not a retained store | `web/backend/app/routes/sessions.py` |
| Playable-audio detection | Audio means sound available, from the WAV or from the muxed video | `web/backend/app/services/recording/layout.py` |
| One-pass counts | `segments` and `words` describe the latest pass, as every other reader does | `web/backend/app/services/transcript/archive.py` |
| Verified source removal | The silent original goes only once the combined file holds both streams | `web/backend/app/services/capture/mux.py` |
| Measured A/V alignment | The capture-start offset, measured and applied to the mux | `sink.py`, `recorder.py`, `mux.py`, `manager.py` |
| Tests | Stopped sessions, media flags on every artefact combination, mux verification and offset | `tests/data/`, `tests/transcription/` |

## 5. Phase Status

| Phase | Status |
| --- | --- |
| 1 — "Recording now" after a stop | ⬜ Not started |
| 2 — Audio chip and one-pass counts | ⬜ Not started |
| 3 — One video file | ⬜ Not started |
| 4 — A/V alignment | ⬜ Not started |
| 5 — Documentation and merge | ⬜ Not started |
