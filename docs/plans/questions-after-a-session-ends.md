# Questions After a Session Ends

*Status: in progress. Written 2026-08-29.*

## 1. Introduction

Reported: after stopping a transcription, asking the assistant to *"summarise the last 10 minutes"*
answers **"There is no transcript to ask about yet. Start recording, then ask again."** — while the
finished transcript is plainly visible in the pane on the left. The transcript on screen is the
client's own copy, accumulated over the WebSocket while the session ran; the server no longer has
one to read. `SessionManager._teardown` closes the transcript store and sets it to `None`, and
`ChatService` is wired to `lambda: manager.store`, so every question after a stop is refused before
it reaches a model.

The fix is the one this repository already identified and recorded in `docs/checklist.md`: **keep
the last finished session's store open for reading until the next session starts.** Nothing about
the assistant, the quick actions, or the context assembler needs to change — they already do the
right thing when handed a store. Two other faults close with it, because all three are the same
root cause: `GET /api/session` reports no stats after a stop, so a browser reload shows an empty
transcript; and `SessionManager.session_seconds` — whose own docstring promises it "falls back to
the stored transcript's duration once the engine is gone, so a question asked after a session ends
is still positioned correctly" — returns `0.0`, which would make *"the last 10 minutes"* select the
range `[0, 0]` and find nothing even if the store were present.

## 2. Gaps & Unanswered Questions

- **How long should a finished store stay open?** *Assumption*: until the next session starts, or
  until the application shuts down. This is what the checklist item proposes, it bounds the cost at
  exactly one extra SQLite connection, and it matches when the answer stops being interesting.

- **Should the assistant be able to *write* to a finished session's store?** Chat history and
  `clear_chat_history` write through the same handle. *Assumption*: yes, and deliberately — a
  conversation about a talk belongs with that talk's record, and the store is already the place
  chat history lives. This is retention for reading *and* for the chat that follows.

- **What happens when a post-transcription pass owns the store?** In `recorded` and `window` modes
  `_start_transcription` hands ownership to `TranscriptionRunner`, which closes the store itself and
  calls `_on_transcription_released`. The manager must not double-close it. *Assumption*: on
  release, reopen the same path as the retained store, since `TranscriptStore(path)` opens an
  existing database (this is exactly what `routes/sessions.py::_open` does for past sessions).

- **What if the file is deleted from the sessions page while still retained?** *Assumption*: accept
  it. SQLite holds the descriptor, reads keep working until the handle closes, and the sessions page
  already owns deletion. Not worth a coupling between the two.

- **Should the "no transcript" message change?** *Assumption*: no. It is correct for its real case —
  a fresh application that has never recorded — and that case must keep saying so.

## 3. Hierarchical Step-by-Step Instructions

### Step 1: Retain the finished store after an ordinary stop

- **Locations**: `web/backend/app/services/session/manager.py` — `SessionManager.__init__` (new
  `self._last_store` attribute), the `store` property, `_teardown`, and `start`.
- **Detail**: `_teardown` currently closes the store and clears it. Instead, when the store was not
  handed over, move it to `_last_store` rather than closing it. The `store` property returns the
  live store when a session is running and the retained one otherwise. `start` closes any retained
  store before opening the new session's, so exactly one is ever held.
- **Rationale**: this is the whole fix for the reported fault. Every consumer already reads through
  the `store` property — `ChatService`'s `store_provider`, both readers in `routes/transcript.py`,
  and `state()`'s stats — so one change closes the assistant fault, the empty-transcript-on-reload
  fault, and the `session_seconds` clock fault together.
- **Validation**: `uv run pytest tests/chat tests/api tests/transcription`, `uv run ruff check .`,
  `uv run ruff format --check .`. New tests named in the deliverables table.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Questions After a Session Ends (1 / 3) Complete: a finished session's transcript
  stays readable, so the assistant can still be asked about it.`

### Step 2: Restore retention after a post-transcription pass releases the store

- **Locations**: `web/backend/app/services/session/manager.py` — `_on_transcription_released`, plus
  the shutdown path that must close a retained store.
- **Detail**: `_on_transcription_released` fires when `TranscriptionRunner` has closed the store it
  was given. Reopen that path as the retained store instead of leaving `None`. Requires holding the
  path (`TranscriptStore.path`) before ownership is handed over, since the closed object is the only
  other place it lives. Close the retained store on application shutdown so no descriptor outlives
  the process.
- **Rationale**: without this, `recorded` and `window` sessions — the two modes that run a batch
  pass, and the ones this fault was reported against — would still answer "no transcript to ask
  about" once their pass finished, which is precisely the moment the transcript becomes complete and
  most worth asking about.
- **Validation**: `uv run pytest tests/api tests/transcription tests/chat`, plus a manual check
  driving a `recorded` session to completion and asking a question afterwards.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Questions After a Session Ends (2 / 3) Complete: a batch pass hands the store
  back rather than leaving nothing to read.`

### Step 3: Documentation, and the whole path end to end

- **Locations**: `docs/documentation.md` (Decision Log — a new entry recording why a finished store
  is retained and for how long), `docs/checklist.md` (close the two items this resolves and record
  anything discovered), `docs/architecture.md` (the store's lifetime is part of the pipeline's
  shape), `docs/data-flow.md` (a question after a stop now reaches the same store), and this plan's
  status line.
- **Rationale**: the repository's rule is that documentation ships with the code it describes, and
  the checklist currently carries the reload fault as open with a proposed remedy — that entry is
  the one being executed here and must not be left claiming the work is outstanding.
- **Validation**: full `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and a
  manual run: record briefly, stop, then ask the assistant to summarise the last ten minutes and
  confirm it answers from the transcript.
- **Action**: Undergo the verification/tests/validation process for this phase. Once validated,
  commit stating: `Questions After a Session Ends (3 / 3) Complete: the retained store is documented
  and verified end to end.`

## 4. Deliverables Table

| Deliverable | Description | Location (File/Path) |
| --- | --- | --- |
| Retained store | `_last_store`, the `store` property's fallback, retention in `_teardown`, and release in `start` | `web/backend/app/services/session/manager.py` |
| Post-pass restoration | Reopening the store when `TranscriptionRunner` releases it, and closing it on shutdown | `web/backend/app/services/session/manager.py` |
| Assistant-after-stop tests | The reported fault as a test: a stopped session still answers rather than raising `ChatError` | `tests/chat/test_questions_after_a_session.py` |
| Store-retention tests | Retention across teardown, release on the next start, one store held at a time, and the batch-pass path | `tests/transcription/test_store_retention.py` |
| Clock-after-stop test | `session_seconds` reports the finished transcript's duration, so "the last 10 minutes" selects a real range | `tests/transcription/test_store_retention.py` |
| Documentation | Decision Log entry, closed checklist items, architecture and data-flow notes | `docs/documentation.md`, `docs/checklist.md`, `docs/architecture.md`, `docs/data-flow.md` |
