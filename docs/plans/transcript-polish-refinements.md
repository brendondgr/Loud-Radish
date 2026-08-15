# Transcript Polish — Dialogue Accuracy, Timestamps, and Continuous Prose

*Created: 2026-08-15 · Status: **complete** (5 / 5 steps)*

## 1. Introduction

The minute-based polish pass (D-018) works, but three things about what it produces are wrong. It
transcribes spoken renderings of code and file references literally, so a speaker saying "guard dot
py" appears on the page as *guard dot py* rather than `guard.py`. It carries no timestamps inside
the rewritten text, so a polished stretch cannot be traced back to the moment it was said. And it
breaks to a new line constantly — a header line, a rule, and a gap every minute, plus whatever
paragraph breaks the model chose inside each one — which is disruptive to read.

This plan changes the pass from *literal transcription with the noise removed* to *accurate
dialogue*. Three changes, and they are interdependent enough to ship together. The chunk is
flattened into one continuous run of text with timestamp markers already placed in it; the model is
asked to rewrite that whole run into a single continuous paragraph, correcting spoken renderings of
technical terms into their written form and repairing the grammar around each correction, and to
carry the markers through to the points where that material ended up; and the result is checked so
that a marker the model invented, moved backwards, or lost cannot corrupt retrieval. On the page,
consecutive blocks then flow as consecutive paragraphs of prose with the timestamps rendered as
quiet inline marks.

The safety properties of D-018 are unchanged and are not up for renegotiation by this plan: the
pass stays additive, the raw segments stay untouched, and every failure still falls back to showing
the transcript exactly as the speech model produced it.

---

## 2. Gaps & Unanswered Questions

- **What "produce a single continuous paragraph, then rewrite it" means mechanically.** The
  requested approach is: build the paragraph, rewrite it, distribute timestamps. Step one is
  described as possibly reading as nonsensical, which is exactly what concatenated speech-model
  output is — so it is the *mechanical* join we already do, not a model call. *Assumption*: one
  model call per chunk, over the whole flattened paragraph, rather than two. A second "revise" pass
  over the model's own output would double the per-minute latency and cost on a local model that
  has to keep up with a live talk, for a benefit no one has measured. Recorded here so a future
  reader knows a second pass was considered rather than overlooked.

- **Where the timestamp markers come from.** *Assumption*: we place them in the source text before
  the model sees it, at a configurable interval (`polish.timestamp_interval_s`, default 15 s), taken
  from the start of the segment that opens each interval. The model is asked to carry them through,
  not to invent them. Asking a model to work out timestamps unaided produces plausible fabrications,
  and a fabricated timestamp is worse than none because it looks exactly like a real one.

- **What happens when the model mishandles a marker.** *Assumption*: reconcile rather than reject. A
  marker that was not supplied is dropped, markers that run backwards are dropped, and adjacent
  duplicates are collapsed. If nothing survives, the block's own start time is prepended so the
  stretch is still locatable. Rejecting the whole minute over a cosmetic marker failure would throw
  away a good rewrite, and the raw segments underneath carry exact times regardless.

- **The tension between "correct spoken forms" and "change nothing else".** Instruction 7 of the
  prompt is the guarantee that the reader is still reading what was said, and correcting a spoken
  rendering is, strictly, a change. *Assumption*: the exception is carved out precisely — the *form*
  of a reference may change and the words immediately around it may be adjusted for grammar, but the
  reference itself, and every claim, number, and qualification, may not. Guessing at what a garbled
  passage meant remains forbidden; rendering "guard dot py" as `guard.py` is transcribing the same
  words, not guessing at different ones.

- **Whether the length guard still holds.** Correcting spoken forms removes words ("guard dot py" is
  three words and `guard.py` is one), so a rewrite is now legitimately shorter than before.
  *Assumption*: keep the 0.6 floor, but stop counting timestamp markers as words on either side, so
  the ratio measures speech rather than punctuation. The default was already provisional and is on
  the checklist to be tuned against a real model.

- **Whether the assistant should now answer from polished text.** Not requested. *Assumption*: no.
  `services/context/` continues to assemble context from raw segments, which is the verbatim record.
  The timestamps added here make the *polished page* navigable and make a polished stretch traceable
  to its moment; feeding polished text to the assistant is a separate decision about what the
  assistant is allowed to read, and is recorded as a follow-up rather than smuggled in here.

- **Whether an inline timestamp should be clickable.** Decided during step 4, against the first
  sketch in this plan, which called for anchors. A citation in a chat answer points *somewhere else*
  and needs a click to get there; a timestamp sitting in the transcript is already at the moment it
  names, so a button there would seek to itself. They are rendered as quiet non-interactive marks,
  which also leaves the region with no focusable elements — the same keyboard surface it had before.

- **The uncommitted change to `core/citations.js`.** The working tree carries someone's in-progress
  edit to that file, broadening the citation regex. This work uses only `parseTimestamp()` from it,
  which both versions export unchanged. *Assumption*: that file is left strictly alone and never
  staged by this work.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: The timestamped source paragraph

- **Locations**:
  - `web/backend/app/config/schema.py` — `PolishConfig.timestamp_interval_s`.
  - `web/backend/app/services/polish/source.py` — new. `build_source(segments, interval)` returns a
    `PolishSource` carrying the flattened one-paragraph text with `[MM:SS]` markers interleaved, and
    the set of marker labels that were legitimately supplied.
  - Reuses `timestamp()` from `services/context/assembler.py` rather than defining a second
    formatter: the polished text and the assistant's citations must render in the same form or
    `core/citations.js` will make one of them clickable and not the other.
  - `tests/assistant/test_polish_source.py` — new.
- **Rationale**: this is step one of the requested approach — the single continuous paragraph, built
  mechanically, before any model sees it. Everything downstream depends on knowing which markers
  were supplied, so it has to exist and be tested first.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/assistant tests/utils`, `uv run ruff check .`). Once validated, commit stating: Transcript
  Polish Refinements (1 / 5) Complete: The chunk is flattened into one timestamped paragraph before
  the model sees it.

### Step 2: Reconciling timestamps and enforcing one paragraph

- **Locations**:
  - `web/backend/app/services/polish/guard.py` — `reconcile_timestamps()` (drop unsupplied markers,
    drop ones that run backwards, collapse adjacent duplicates, guarantee at least one);
    `collapse_to_paragraph()` (join everything that is not a list into one continuous run);
    `preserves_content()` amended to ignore markers when counting words.
  - `tests/assistant/test_polish_guard.py` — extended.
- **Rationale**: the guard is the only thing standing between model output and the page, and both
  new requirements — retrievable timestamps and no constant line breaks — need an enforcement that
  does not depend on the model having obeyed. A model that ignores "one paragraph" must still
  produce one.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/assistant`, `uv run ruff check .`). Once validated, commit stating: Transcript Polish
  Refinements (2 / 5) Complete: Timestamps are reconciled against what was supplied, and the output
  is collapsed to a single paragraph.

### Step 3: The prompt and the worker

- **Locations**:
  - `web/backend/app/services/polish/prompts.py` — the instruction list rewritten: spoken renderings
    of file names, paths, identifiers, and acronyms converted to written form with the surrounding
    wording adjusted to stay grammatical; one continuous paragraph; markers carried through; the
    integrity rule restated so the new licence is bounded.
  - `web/backend/app/services/polish/worker.py` — build the source through `build_source`, pass the
    supplied labels through the guard, reconcile and collapse before storing.
  - `tests/assistant/test_polish_worker.py` — extended: markers survive a round trip, an invented
    marker is dropped, a model that returns three paragraphs still stores one.
- **Rationale**: this is where the three requested changes actually reach the output. The prompt is
  the only thing that can do the spoken-form correction; the worker is the only place the source
  builder and the guard meet.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest
  tests/assistant`, `uv run ruff check .`). Once validated, commit stating: Transcript Polish
  Refinements (3 / 5) Complete: The model is asked for accurate dialogue in one paragraph, with
  spoken code references written properly and timestamps carried through.

### Step 4: The page reads as continuous prose

- **Locations**:
  - `web/frontend/static/js/components/transcript-pane.js` — render the inline `[MM:SS]` markers as
    quiet non-interactive time marks; drop the per-block timestamp header.
  - `web/frontend/static/css/components/transcript.css` — consecutive blocks flow as consecutive
    paragraphs: no rule, no header line, ordinary paragraph spacing. A quiet inline treatment for
    the timestamp marks, distinct from the chat's citation buttons.
  - `web/frontend/templates/partials/settings/context.html` — expose `polish.timestamp_interval_s`.
  - `docs/component-map.md`, `docs/design-system.md` — updated in this step.
- **Rationale**: the third requested change is a reading experience, and it is only half solved in
  the backend. A single-paragraph block still reads as a broken page if the pane draws a rule and a
  header above every one of them.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest`,
  `uv run ruff check .`, plus manual browser QA: the inline marks, consecutive blocks flowing,
  their contrast, 320 px, and a keyboard pass). Once validated, commit stating: Transcript Polish
  Refinements (4 / 5) Complete: Polished minutes flow as continuous prose with inline timestamps.

### Step 5: Documentation, full verification, and merge

- **Locations**:
  - `docs/documentation.md` — D-018 amended rather than duplicated: it is the same decision, with
    the dialogue-accuracy, timestamp, and single-paragraph properties now part of it.
  - `docs/api-contract.md` — the `transcript.polished` payload's `text` now contains `[MM:SS]`.
  - `docs/data-flow.md`, `docs/architecture.md` — the flatten → rewrite → reconcile sequence.
  - `docs/structure.md` — `services/polish/source.py`, the new test file.
  - `docs/checklist.md` — close this work; record the follow-ups (assistant reading polished text,
    tuning the ratio floor against a real model).
  - `docs/plans/README.md` and this file — mark complete.
- **Rationale**: the repository contract is that documentation ships with the code, and D-018 as
  written now describes behaviour that has changed.
- **Action**: Undergo the verification/tests/validation process for this phase (`uv run pytest`,
  `uv run ruff check .`, `uv run ruff format --check .`). Once validated, commit stating: Transcript
  Polish Refinements (5 / 5) Complete: Documented the dialogue-accuracy pass and merged it into
  main.

---

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Source builder | Flattens a chunk into one paragraph with interleaved `[MM:SS]` markers | `web/backend/app/services/polish/source.py` |
| Marker interval setting | How often a timestamp is placed in the source | `web/backend/app/config/schema.py` |
| Timestamp reconciliation | Drops invented, out-of-order, and duplicate markers; guarantees one | `web/backend/app/services/polish/guard.py` |
| Paragraph collapse | Enforces the single-paragraph rule the model may ignore | `web/backend/app/services/polish/guard.py` |
| Dialogue-accuracy prompt | Spoken-form correction, grammar repair, one paragraph, markers carried | `web/backend/app/services/polish/prompts.py` |
| Worker rewiring | Source builder and guard composed into the pass | `web/backend/app/services/polish/worker.py` |
| Inline time marks | Block text rendered with quiet non-interactive timestamps | `web/frontend/static/js/components/transcript-pane.js` |
| Flowing prose styling | Consecutive blocks read as consecutive paragraphs | `web/frontend/static/css/components/transcript.css` |
| Settings control | The marker interval, in Settings → Context | `web/frontend/templates/partials/settings/context.html` |
| Source builder tests | Interval placement, boundaries, the supplied-label set | `tests/assistant/test_polish_source.py` |
| Guard tests | Reconciliation, collapse, marker-blind word counting | `tests/assistant/test_polish_guard.py` |
| Worker tests | Markers round-trip, invented markers dropped, one paragraph stored | `tests/assistant/test_polish_worker.py` |

---

## 5. Step Status

| Step | Title | Status |
|---|---|---|
| 1 | The timestamped source paragraph | ✅ Complete |
| 2 | Reconciling timestamps and enforcing one paragraph | ✅ Complete |
| 3 | The prompt and the worker | ✅ Complete |
| 4 | The page reads as continuous prose | ✅ Complete |
| 5 | Documentation, verification, merge | ✅ Complete |
