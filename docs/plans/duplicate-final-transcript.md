# Plan — The final transcript says everything twice

*Written 2026-08-28. Status: complete (4 / 4).*

---

## 1. Introduction

A session that transcribes **live and again afterwards** ends up showing its transcript twice: the
language model's rewritten prose, and then — appended straight after it — the whole raw transcript
again. The same duplication reaches the exported file and the sessions page.

The cause is not the summary pass. It is revision handling (**D-022**). A window session that ran a
live pass holds revision `0`; the post-capture pass writes revision `1`, and both are deliberately
kept. `docs/api-contract.md` already states the rule that follows from that: *"a client showing
revision 1 must **replace** the transcript rather than merge — the two cover the same audio with
different ids, and interleaving them says everything twice."* Four call sites never applied it. The
post-capture pass publishes `transcript.committed` for every revision-1 segment as it is written, and
the browser appends each one onto the revision-0 view it is already showing; the three read paths
(`GET /api/transcript/export`, `GET /api/sessions/{key}`, `GET /api/sessions/{key}/export`) load
*every* segment regardless of revision.

The fix is to make "the transcript" mean one pass everywhere: the browser renders exactly the
revision it is displaying and switches wholesale when the second pass finishes, and the read paths
serve the latest revision rather than the union of all of them.

---

## 2. Gaps & Unanswered Questions

- **Which revision should the default view show?** The latest. `GET /api/transcript/revisions`
  already reports `latest`, the Live/Final switch already exists, and a reader wanting the live pass
  can select it. *Assumption applied.*
- **Should `/export` gain a `revision` query parameter?** Not in this change. It would be the only
  way to export the live pass, but the sessions page has no revision control to drive it and adding
  one widens a bug fix into a feature. Recorded as a follow-up in `docs/checklist.md`.
- **What about `segments_in_range`, which the context, polish, and chat paths use?** It also spans
  revisions. It is not reachable with two revisions present today — the post-capture pass runs after
  those workers have stopped, and it closes the store when it finishes — so it is recorded as a
  follow-up rather than changed speculatively.
- **Full-text search deliberately spans both passes** (`tests/data/test_transcript_revisions.py`).
  Unchanged: a hit in either pass is a real hit, and search results are not the transcript.

---

## 3. Hierarchical Step-by-Step Instructions

#### Step 1: One store method for "the transcript to show"

- **Locations**: `web/backend/app/services/transcript/store.py`, `TranscriptStore`, new
  `latest_segments()` beside `segments_at()` and `all_segments()`.
- **Rationale**: Three routes need the same answer, and three copies of
  `segments_at(latest_revision())` is three places for the next caller to get it wrong.
  `all_segments()` stays — the archive and the migration tests need "every row, whatever revision".
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Duplicate Final Transcript (1 / 4) Complete: the store can name the transcript a
  reader should be shown.

#### Step 2: The read paths serve one pass

- **Locations**: `web/backend/app/routes/transcript.py` (`export`);
  `web/backend/app/routes/sessions.py` (`read_session`, `export_session`).
- **Rationale**: These are the "final result" the user opens. Each currently concatenates every
  pass, so a two-pass session exports the talk twice under one `## Transcript` heading.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Duplicate Final Transcript (2 / 4) Complete: an export and a past session read one
  transcription pass, not the union of all of them.

#### Step 3: The pane renders the revision it is showing

- **Locations**: `web/frontend/static/js/stores/transcript.js` (`TranscriptStore.revision`,
  `setRevision`, `commit`, `commitMany`, `reset`); `web/frontend/static/js/main.js`
  (`showRevision`, the `TRANSCRIPTION_DONE` handler).
- **Rationale**: This is what the user actually saw. The store now drops a committed segment whose
  revision is not the one on screen, so the second pass streaming in cannot append itself under the
  first; and when the pass finishes, the pane switches to the latest revision, which resets and
  refetches rather than merging. Enforced in the store rather than in the pane for the same reason
  the hypothesis is its own field — the shape of the store is the safeguard.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Duplicate Final Transcript (3 / 4) Complete: the transcript pane shows one pass and
  switches to the second wholesale when it lands.

#### Step 4: Tests and documentation

- **Locations**: `tests/data/test_transcript_revisions.py`, `tests/data/test_export_formats.py`,
  `tests/api/` as needed; `docs/api-contract.md`, `docs/data-flow.md`, `docs/component-map.md`,
  `docs/checklist.md`, `docs/documentation.md`.
- **Rationale**: The contract already said the right thing and nothing checked it. A regression test
  that builds a two-pass session and asserts the export contains each sentence once is what stops
  this returning.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Duplicate Final Transcript (4 / 4) Complete: a two-pass session is proved to render
  and export each sentence once.

---

## 4. Deliverables

| Deliverable | Description | Location |
| --- | --- | --- |
| `latest_segments()` | The one transcript a reader is shown | `web/backend/app/services/transcript/store.py` |
| One-pass read paths | Export and past-session reads serve the latest revision | `web/backend/app/routes/transcript.py`, `web/backend/app/routes/sessions.py` |
| Revision-aware store | The browser drops segments from a pass it is not showing | `web/frontend/static/js/stores/transcript.js` |
| Wholesale switch on completion | The pane replaces its content when the second pass finishes | `web/frontend/static/js/main.js` |
| Regression tests | A two-pass session renders and exports each sentence once | `tests/data/test_transcript_revisions.py`, `tests/data/test_export_formats.py` |
