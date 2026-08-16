# Plans

Implementation and handoff plans live here, one file per topic:
`docs/plans/<short-kebab-topic>.md`.

Follow the format in [../skills/planner/planner.md](../skills/planner/planner.md) and the project
conventions in [../skills/planner/SKILL.md](../skills/planner/SKILL.md).

## Rules

- Any work spanning more than one session, or more than roughly three non-trivial steps, gets a plan
  **before** implementation.
- A plan is a handoff artifact. Another agent must be able to resume from it cold, without the
  conversation that produced it — so name concrete files, modules, and commands.
- Update phase status as work lands. Mark finished plans complete; do not delete them.
- Every phase names its verification step and which `docs/` files it updates.

## Index

| Plan | Topic | Status |
|---|---|---|
| [live-seminar-transcriber.md](live-seminar-transcriber.md) | Full vertical slice — live audio capture, streaming ASR, transcript store, LLM chat, and the Jinja2 + ES module frontend | Complete (14 / 14 phases) |
| [minute-based-transcript-polish.md](minute-based-transcript-polish.md) | A background pass that rewrites each finished minute of transcript into clean block text, without changing what was said | Complete (7 / 7 steps) |
| [transcript-polish-refinements.md](transcript-polish-refinements.md) | Dialogue accuracy: spoken code references written properly, timestamps retained for retrieval, and continuous prose instead of constant line breaks | Complete (5 / 5 steps) |
| [asr-hallucination-suppression.md](asr-hallucination-suppression.md) | Stop the speech model inventing "thank you", "bye", and stray words on room noise and silence | Complete (4 / 4 steps) |
| [window-capture-repair.md](window-capture-repair.md) | Window recording repaired against the machine rather than the test double: recorded resolution, repeated portal dialogs, the live preview, window audio instead of the microphone, immediate stop, and timestamps that correlate | **7 / 8 — Phase 5 (window audio) outstanding** |

## The multi-mode expansion

Five plans, written together and executed in order. The application grows from one thing it can do —
live transcription — to three capture modes plus a desktop presence. Plans 1 and 2 are the interface;
3 and 4 are the two new modes; 5 makes the whole thing resident on the machine.

| # | Plan | Topic | Status |
|---|---|---|---|
| 1 | [multi-mode-ui-design.md](multi-mode-ui-design.md) | The mode and run-state vocabulary, the header's two controls, the pre-flight sheet, and the monitor pane — specified, not built | **Complete (5 / 5)** |
| 2 | [multi-mode-ui-implementation.md](multi-mode-ui-implementation.md) | Building it: mode selector, six-state record control, pre-flight sheet, third pane, mode carried through the API | **Complete (6 / 6)** |
| 3 | [recorded-transcription.md](recorded-transcription.md) | Toggle on to capture audio with no inference; toggle off to transcribe the whole file in one pass | **Complete (6 / 6)** |
| 4 | [window-recording-transcription.md](window-recording-transcription.md) | Wayland portal window capture, optional video, live and post-process transcription, the monitor pane, transcript revisions | **Complete (7 / 7)** |
| 5 | [system-integration.md](system-integration.md) | Autostart, a tray companion process drawing the Aperture microphone, global keybinds, and a keybind settings tab | **Complete (6 / 6)**, with the tray's D-Bus export outstanding |

All five are complete. Plan 5's animation is specified by [../motion-spec.md](../motion-spec.md),
supplied 2026-08-15. Two things are recorded as outstanding rather than claimed: the tray's
StatusNotifierItem export, and the CPU-contention measurement, both in
[../checklist.md](../checklist.md).
