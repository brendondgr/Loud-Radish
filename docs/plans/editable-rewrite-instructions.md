# Editable Rewrite Instructions

*Status: **Not started (0 / 7)*** · *Created 2026-09-09* · *Decision **D-068***

## 1. Introduction

Two passes in this application hand a piece of transcribed speech to a language model and ask it to
rewrite it. The live **polish** pass (`services/polish/`) turns each finished minute of a talk into
readable prose. The **dictation** cleanup (`services/dictation/`) puts punctuation on a push-to-talk
sentence before it is pasted into whatever window has focus. Both are driven by an instruction list
compiled into the source, and neither can be changed without editing Python.

That is the wrong place for it. The instructions encode judgements — how aggressively to remove
filler, whether spoken punctuation becomes real punctuation, whether a passage stays one paragraph —
and those judgements belong to whoever is listening to the talk, not to the repository. A user
transcribing legal dictation wants the disfluencies kept. A user transcribing a code walkthrough
wants far more aggressive expansion of spoken identifiers. Today both get the same shipped opinion.

This plan makes both instruction lists editable from Settings, seeded from the shipped text and
restorable to it, and exposes the output guards that would otherwise silently reverse an edit. The
approach is a sparse override: an empty setting means "use what shipped", so a later release's
improved default still reaches anyone who never touched it, and a full replacement is stored only
once someone actually writes one.

## 2. Gaps & Unanswered Questions

- **Override or append?** Answered by the user: full replacement with a Reset button. *Assumption
  carried forward*: the shipped text is never stored on disk unless the user edited it, so an
  untouched installation keeps tracking the default.
- **The guards versus the instructions.** `collapse_to_paragraph`, `reconcile_timestamps`,
  `strip_decoration` and `preserves_content` enforce parts of the shipped prompt after the fact.
  A user who rewrites rule 7 to ask for paragraphs gets one paragraph anyway, and nothing tells them
  why. *Answered by the user*: each guard becomes a setting. The two ratio bounds
  (`min_retained_ratio`, and `MAX_EXPANSION_RATIO` which is currently a module constant) come with
  them, since they are what discards a whole rewrite.
- **Should the dictation prompt live in the web settings or the native window?** The native settings
  window (`companion/settings_window.py`) is a small Tk window built for one-line switches, and a
  multi-line instruction editor does not belong in it. *Assumption*: the editor lives in the web
  settings dialog; the native window keeps its existing tidy on/off switch.
- **Does an edited instruction list persist across restarts?** `polish.*` currently lands in the
  runtime layer and is lost unless Save is pressed; `dictation.*` already persists by prefix.
  *Assumption*: `polish.instructions` joins `PERSISTENT_PATHS`. An authored paragraph of prose is a
  deliberate artefact like a chosen microphone, not a threshold someone nudges mid-talk and
  abandons. The numeric polish settings keep the Save button.
- **What stops an instruction list that breaks the pass?** Nothing can validate prose. The existing
  failure path already covers it: a rewrite that fails the guard is discarded and the raw transcript
  is shown, and a dictation tidy that fails falls back to what was said. *Assumption*: no new
  validation beyond a length cap on the field, and a visible reminder in the panel that the raw
  transcript is the fallback.
- **Version drift on an edited copy.** If the shipped instructions improve in a later release, a
  user holding a full override never sees it. *Assumption*: out of scope for this plan; the Reset
  button is the remedy, and the panel shows when a stored override differs from the shipped text.

---

## 3. Hierarchical Step-by-Step Instructions

### Step 1: The instructions and their guards become configuration

- **Locations**:
  - `web/backend/app/config/schema.py` — `PolishConfig`, `DictationConfig`.
  - `web/backend/app/config/store.py` — `PERSISTENT_PATHS`.
  - `web/backend/app/services/polish/prompts.py` — rename `POLISH_PROMPT` to
    `DEFAULT_POLISH_PROMPT`, add `resolve(config)`.
  - `web/backend/app/services/dictation/prompts.py` — rename `DICTATION_PROMPT` to
    `DEFAULT_DICTATION_PROMPT`, add `resolve(config)`.
- **What changes**: `PolishConfig` gains `instructions: str = ""` plus the guard switches
  `collapse_paragraphs`, `strip_decoration`, `reconcile_timestamps` and the bound
  `max_expansion_ratio` alongside the existing `min_retained_ratio`. `DictationConfig` gains
  `instructions: str = ""` and the two word-count bounds currently hardcoded in `tidy_one`. Each
  `resolve` returns the stored text when it is non-blank and the shipped constant otherwise.
- **Rationale**: every later step reads these fields, so they exist first. Putting `resolve` in the
  prompts module rather than at each call site keeps the empty-means-default rule in one place —
  written twice it will eventually disagree with itself, which is the same reason
  `partials/settings/modal.html` holds no defaults.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Editable Rewrite Instructions (1/7) Complete: The two rewrite instruction lists
  and their output guards are configuration, defaulting to the shipped text.

### Step 2: The API serves the shipped text alongside the resolved configuration

- **Locations**: `web/backend/app/schemas/api.py` — `ConfigResponse`, `ConfigPatchResponse`;
  `web/backend/app/routes/config.py` — `get_config`, `patch_config`, `apply_preset`.
- **What changes**: both responses carry a `prompt_defaults` map keyed by dotted path
  (`polish.instructions`, `dictation.instructions`) holding the shipped text.
- **Rationale**: the settings panel has to show the shipped instructions in an empty field and has
  to restore them on Reset. Serving them with the configuration means no second request and, more
  importantly, no copy of the prompt text in the frontend — the frontend already owns no defaults
  and must not start now.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Editable Rewrite Instructions (2/7) Complete: The configuration endpoints serve
  the shipped instruction text so the interface never holds a copy.

### Step 3: The polish pass reads the configured instructions and guards

- **Locations**: `web/backend/app/services/polish/worker.py` — `PolishWorker._generate`,
  `PolishWorker._polish`, `PolishWorker._tick` (the guard chain around
  `collapse_to_paragraph` / `reconcile_timestamps` / `preserves_content`);
  `web/backend/app/services/polish/guard.py` — `preserves_content`, `MAX_EXPANSION_RATIO`.
- **What changes**: the system message comes from `prompts.resolve`. Each guard call becomes
  conditional on its switch. `preserves_content` takes the expansion ceiling as an argument instead
  of reading a module constant, with the constant kept as the schema default.
- **Rationale**: this is the pass the user watches while a talk is running, and `PolishWorker`
  already re-reads configuration on every tick by design — so an edited instruction list applies to
  the next minute rather than the next session, with no new plumbing.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Editable Rewrite Instructions (3/7) Complete: The live polish pass follows the
  configured instructions, and each guard can be turned off rather than silently reversing an edit.

### Step 4: The dictation cleanup reads the configured instructions and bounds

- **Locations**: `web/backend/app/services/dictation/pipeline.py` — `tidy_one`.
- **What changes**: the system message comes from `prompts.resolve`; the `written > spoken * 2 + 8`
  and `written < spoken * 0.5` bounds come from `DictationConfig`.
- **Rationale**: dictation is the pass where a bad rewrite is worst — the text goes straight into
  someone's document — so the fallback to the raw transcript stays exactly as it is, and only the
  numbers and the wording become adjustable.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Editable Rewrite Instructions (4/7) Complete: The dictation tidy follows the
  configured instructions within adjustable bounds, still falling back to what was said.

### Step 5: A Rewriting tab in the settings dialog

- **Locations**:
  - New `web/frontend/templates/partials/settings/rewriting.html`.
  - `web/frontend/templates/partials/settings/nav.html` — a seventh tab.
  - `web/frontend/templates/partials/settings/modal.html` — include the new panel.
  - `web/frontend/templates/partials/settings/context.html` — the "Tidy the transcript" group moves
    out of it.
  - New `web/frontend/static/js/components/settings/rewriting.js`.
  - `web/frontend/static/js/components/settings-modal.js` — register it.
  - `web/frontend/static/css/components/` — the multi-line instruction field, if
    `field__control--text` does not already carry it.
- **What changes**: one panel with two groups, transcript and dictation. Each holds the existing
  numeric settings, an instruction editor bound with `data-config`, a Reset control that writes an
  empty string, a note saying whether the stored text differs from what shipped, and the guard
  switches. The panel states the fallback plainly: an instruction list the model cannot follow
  leaves the raw transcript on the page.
- **Rationale**: the Context tab is about what the assistant is *given*, and the polish settings have
  always sat there awkwardly; the dictation tidy has no home in the web interface at all. A tab named
  for the thing both passes do is where a user looks. The Reset writing an empty string rather than
  the shipped text is what keeps an untouched installation tracking future defaults.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Editable Rewrite Instructions (5/7) Complete: A Rewriting tab edits both
  instruction lists, resets each to what shipped, and exposes the guards.

### Step 6: Tests

- **Locations**:
  - `tests/assistant/test_polish_worker.py` — the configured instruction list reaches the backend;
    each guard switch is honoured.
  - `tests/assistant/test_polish_guard.py` — `preserves_content` against a supplied ceiling.
  - New `tests/assistant/test_rewrite_instructions.py` — `resolve` for both passes: blank yields the
    shipped text, whitespace counts as blank, a stored list wins.
  - `tests/transcription/test_dictation.py` — the configured dictation instructions and bounds.
  - `tests/api/test_config_persistence.py` — `polish.instructions` reaches the file without Save.
  - `tests/api/test_health.py` or a new config-route test — `prompt_defaults` in the response.
  - `tests/frontend/test_rendered_accessibility.py` — the new panel and tab.
- **Rationale**: the empty-means-default rule and the persistence rule are the two things that will
  quietly break later; both are cheap to pin now. The frontend accessibility test already walks the
  rendered dialog, so a new tab must be added to it or it will fail.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Editable Rewrite Instructions (6/7) Complete: Tests cover the override rule, the
  guard switches, persistence, and the rendered panel.

### Step 7: Documentation

- **Locations**: `docs/documentation.md` (Decision **D-068**, status table), `docs/structure.md`
  (the new frontend files, the renamed prompt constants), `docs/api-contract.md`
  (`prompt_defaults`), `docs/component-map.md` (the new panel and module),
  `docs/design-system.md` (the instruction field, if a new class was added),
  `docs/checklist.md`, `docs/plans/README.md` (index row), and this file's step statuses.
- **Rationale**: the repository contract makes documentation part of the change rather than a
  follow-up, and D-018 currently records the polish prompt as fixed — leaving that unamended would
  make the decision log wrong rather than merely incomplete.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: Editable Rewrite Instructions (7/7) Complete: The decision log, structure, API
  contract, and component map record the editable instructions.

---

## 4. Deliverables

| Deliverable | Description | Location |
| --- | --- | --- |
| Instruction and guard settings | `instructions` plus guard switches on both configs | `web/backend/app/config/schema.py` |
| Override resolution | Blank means the shipped text, in one place per pass | `web/backend/app/services/polish/prompts.py`, `web/backend/app/services/dictation/prompts.py` |
| Persistence rule | An authored instruction list reaches the config file without Save | `web/backend/app/config/store.py` |
| Shipped text over the API | `prompt_defaults` on the configuration responses | `web/backend/app/schemas/api.py`, `web/backend/app/routes/config.py` |
| Configured polish pass | Instructions and per-guard switches honoured | `web/backend/app/services/polish/worker.py`, `web/backend/app/services/polish/guard.py` |
| Configured dictation tidy | Instructions and word-count bounds honoured | `web/backend/app/services/dictation/pipeline.py` |
| Rewriting settings panel | Two editors, two Resets, the guard switches | `web/frontend/templates/partials/settings/rewriting.html`, `web/frontend/static/js/components/settings/rewriting.js` |
| Override-rule tests | Blank, whitespace, and stored text for both passes | `tests/assistant/test_rewrite_instructions.py` |
| Polish behaviour tests | The instruction list reaches the model; guards obey their switches | `tests/assistant/test_polish_worker.py`, `tests/assistant/test_polish_guard.py` |
| Dictation behaviour tests | Configured instructions and bounds | `tests/transcription/test_dictation.py` |
| API and persistence tests | `prompt_defaults`, and `polish.instructions` written straight through | `tests/api/test_config_persistence.py` |
| Rendered panel test | The new tab and panel pass the accessibility walk | `tests/frontend/test_rendered_accessibility.py` |
