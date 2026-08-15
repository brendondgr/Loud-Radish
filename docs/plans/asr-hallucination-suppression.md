# Suppressing Invented Speech on Silence and Noise

*Created: 2026-08-15 · Status: not started (0 / 4 steps)*

## 1. Introduction

Reported from real use: the transcript picks up room noise and invents speech that was never said.
"Thank you" and "bye" recur, along with stray single words. This reproduced immediately on a
synthetic tone fixture, which contains no speech at all and produced a segment reading **"you"**.

This is Whisper's best-known failure mode rather than a bug in the wiring. The model was trained on
captioned video and, given audio with no speech in it, returns the phrases that end such videos —
"Thank you", "Bye", "Thanks for watching", "Subtitles by …". It does this confidently, and the text
is indistinguishable in the transcript from something the speaker said.

The pipeline already has a silence gate that skips inference when the voice detector reports no
speech, and that gate works. The problem is what it is being asked to detect with: an **energy**
detector at a fixed sensitivity, which a fan, a keyboard, a chair, or a conversation two rooms away
all clear comfortably. Inference then runs on audio that is noise rather than speech, and the model
does what it does. Meanwhile the one signal that would settle the question — Whisper's own
`no_speech_prob`, its estimate that a window contains no speech — is computed on every pass and
thrown away unread, because the backend reads `segment.words` and nothing else off each result.

The plan is therefore to stop guessing from the outside and start using what the model already
knows, in four layers: carry the model's own no-speech and average-log-probability signals out of
the backend; drop output that fails them, plus a small blocklist for the specific phrases that
survive every threshold; turn on the decoder's built-in speech filter so non-speech never reaches
it; and expose all of it so a user whose room is quiet can loosen it and one whose room is not can
tighten it.

**The governing trade-off, stated once.** Every filter here can in principle discard something real.
A quietly-spoken "thank you" at the end of a talk is a real sentence and the blocklist would eat it.
So the defaults lean conservative, each layer is individually switchable, and the discards are
counted and reported rather than silent — a filter that quietly removes speech is a worse bug than
the one it fixes.

---

## 2. Gaps & Unanswered Questions

- **How aggressive the defaults should be.** *Assumption*: conservative. `no_speech_prob > 0.6`
  combined with a low average log-probability, rather than either alone; the blocklist applies only
  when a discarded phrase is the *entire* segment and the segment is short. A user can tighten it in
  settings. The alternative — aggressive defaults with an opt-out — trades a visible failure for an
  invisible one, and invisible is worse in a document someone will rely on.

- **Whether to enable faster-whisper's built-in `vad_filter`.** It runs Silero over the buffer and
  strips non-speech before decoding, which is exactly the right tool. *Assumption*: on by default,
  with a setting. Two risks to verify rather than assume: it costs CPU per pass, which matters when
  the real-time factor is already near 1, and it can clip the first syllable after a pause. Both are
  measurable on the fixtures and neither is a reason not to try it.

- **Where the filter belongs.** *Assumption*: in the ASR layer for the model-confidence signals,
  since only that layer sees them, and in the streaming layer for the phrase blocklist, since only
  that layer knows a segment's duration and whether it stands alone. Putting the blocklist in the
  backend would mean every backend reimplementing it.

- **Whether the energy detector should simply be replaced by Silero.** The optional `vad-silero`
  group already exists and `services/vad/silero.py` implements it behind the same interface.
  *Assumption*: not in this plan. Enabling the decoder's own filter addresses the same problem
  inside the model, and swapping the detector is a one-line setting the user can already change.
  Recorded so it is a considered decision rather than an oversight.

- **What the user sees when text is dropped.** *Assumption*: nothing in the transcript — a dropped
  hallucination should leave no gap, because there was nothing there. The count goes to the metrics
  the status bar already publishes, so "42 suppressed" is visible to anyone who looks and silent to
  anyone who does not. **Human intervention may be needed** on whether that count deserves a place
  in the status bar itself rather than only in the payload.

- **The mock backend.** It is scripted and cannot hallucinate, so none of this is testable end to
  end against it. *Assumption*: the filter is unit-tested directly against synthesised results, and
  the real-model behaviour goes on the verification-debt list where the other unfalsifiable claims
  live.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: Carry the model's own confidence out of the backend

- **Locations**:
  - `web/backend/app/services/asr/contract.py` — `AsrResult` gains `no_speech_prob` and
    `avg_logprob`; `WordToken` already carries `confidence`.
  - `web/backend/app/services/asr/faster_whisper.py` — `_collect_words` currently reads only
    `segment.words` and discards the rest of each segment; it must also collect
    `segment.no_speech_prob` and `segment.avg_logprob` and aggregate them across the pass.
  - `web/backend/app/services/asr/mock.py` — able to script these values, so the filter is testable.
  - `tests/transcription/test_asr_contract.py` — the values survive a pass.
- **Rationale**: nothing downstream can filter on a signal that is thrown away at the point it is
  produced. This is the whole reason the problem has been invisible to the existing guards.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/transcription`, `uv run ruff check .`). Once validated, commit stating: ASR Hallucination
  Suppression (1 / 4) Complete: The speech model's own no-speech estimate is carried out of the
  backend instead of discarded.

### Step 2: The hallucination filter

- **Locations**:
  - `web/backend/app/config/schema.py` — a `hallucination` block on `AsrConfig` or its own section:
    `enabled`, `no_speech_threshold`, `logprob_threshold`, `min_word_confidence`,
    `drop_phrases_enabled`, `max_phrase_seconds`.
  - `web/backend/app/services/asr/hallucination.py` — new. The threshold test, the phrase list
    ("thank you", "thanks for watching", "bye", "you", "subtitles by …" and the usual set), and a
    `Suppression` record saying what was dropped and why.
  - `web/backend/app/services/streaming/engine.py` / `guards.py` — apply it where a pass's words
    become segments, and count suppressions.
  - `web/backend/app/services/session/metrics.py` — a `suppressed` count on the status payload.
  - `tests/transcription/test_hallucination_filter.py` — new: each threshold, the blocklist, and
    the case that matters most — **a real "thank you" in a long segment of genuine speech survives**.
- **Rationale**: this is the fix. It is separated from the backend so it applies to any speech model,
  and it counts rather than hides, because a filter that silently deletes speech is worse than the
  hallucination it removes.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/transcription`, `uv run ruff check .`). Once validated, commit stating: ASR Hallucination
  Suppression (2 / 4) Complete: Invented speech is dropped on the model's own confidence, with a
  blocklist for the phrases that survive it.

### Step 3: Stop feeding the model non-speech in the first place

- **Locations**:
  - `web/backend/app/services/asr/faster_whisper.py` — pass `vad_filter` and its parameters through
    to `transcribe`.
  - `web/backend/app/config/schema.py` — the setting.
  - `scripts/run_file_session.py` — the measurement: run `silent-20s.wav` and `sparse-20s.wav`
    before and after, and record the real-time-factor cost and whether the first syllable after a
    pause survives.
- **Rationale**: filtering output is a second line of defence. Not decoding non-speech at all is the
  first, and it is one argument to a call the code already makes. The measurement is part of the step
  because the cost is real and unmeasured.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest`,
  plus the fixture runs above with their numbers recorded). Once validated, commit stating: ASR
  Hallucination Suppression (3 / 4) Complete: The decoder's own speech filter keeps non-speech out
  of inference, with the cost measured.

### Step 4: Settings, documentation, verification, and merge

- **Locations**:
  - `web/frontend/templates/partials/settings/asr.html` — the controls, with plain-language hints
    saying what tightening them costs.
  - `web/frontend/static/js/components/status-bar.js` — surface the suppression count if the open
    question above is answered yes.
  - `docs/documentation.md` (a new decision), `docs/architecture.md` (the failure table),
    `docs/data-flow.md`, `docs/api-contract.md` (the status payload), `docs/structure.md`,
    `docs/checklist.md` (close it; move the real-model check to verification debt).
  - `docs/plans/README.md` and this file — mark complete.
- **Rationale**: the user reporting this has a specific room and a specific microphone, and no
  default chosen here will be right for both that and the next one. The setting is the deliverable
  as much as the filter is.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest`,
  `uv run ruff check .`, `uv run ruff format --check .`, plus browser QA of the new controls). Once
  validated, commit stating: ASR Hallucination Suppression (4 / 4) Complete: Documented the filter,
  exposed its thresholds, and merged into main.

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Confidence on the result | `no_speech_prob` and `avg_logprob` carried out of a pass | `web/backend/app/services/asr/contract.py`, `faster_whisper.py` |
| Hallucination filter | Thresholds, phrase blocklist, and the record of what was dropped | `web/backend/app/services/asr/hallucination.py` |
| Filter settings | Every threshold, individually switchable | `web/backend/app/config/schema.py` |
| Suppression count | Published with the rest of pipeline health | `web/backend/app/services/session/metrics.py` |
| Decoder-side VAD | `vad_filter` on the faster-whisper call, with its cost measured | `web/backend/app/services/asr/faster_whisper.py` |
| Settings controls | The thresholds, with hints on what tightening them costs | `web/frontend/templates/partials/settings/asr.html` |
| Filter tests | Each threshold, the blocklist, and a real "thank you" surviving | `tests/transcription/test_hallucination_filter.py` |
| Contract tests | The new signals survive a pass | `tests/transcription/test_asr_contract.py` |

---

## 5. Step Status

| Step | Title | Status |
|---|---|---|
| 1 | Carry the model's own confidence out of the backend | Not started |
| 2 | The hallucination filter | Not started |
| 3 | Stop feeding the model non-speech | Not started |
| 4 | Settings, documentation, verification, merge | Not started |
