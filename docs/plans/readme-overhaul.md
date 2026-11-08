# README Overhaul: The Front Page After the Redesign

*Written 2026-11-08. Status: **✅ Complete (4 / 4)**. Takes decision **D-087**.*

## 1. Introduction

The README was last rewritten on 2026-09-10 (D-069, D-070). Since then, everything on
2026-11-07 changed what the application looks like and what it can do: the record sheet (D-075),
past recordings on the main screen (D-076 to D-078), steady window video and mixed audio (D-080,
D-081), and the redesign of the Recordings page and the main screen (D-083 to D-086). All five
screenshots showed the old interface, and the feature list had grown to twelve long paragraphs.
The owner asked for a README that shows the whole application, with current pictures, and says
less.

## 2. Steps

1. **Rewrite `README.md` shorter.** ✅ Opening paragraph, hero image, a factual status line, the
   features grouped as Record / Read and ask / Keep and revisit / Beyond the browser, a 2×2 image
   grid, run, architecture, privacy, documentation, licence. Removed: the drafted *Why* section
   (an inferred motivation, see checklist Part 7), the upgrade notes from the rename (D-038 keeps
   them), the long documentation table, and the repository-conventions section (the global rules
   carry it). 248 lines to about 140.
2. **Make made-up talks to photograph.** ✅ Five fictional scripts in `docs/assets/talks/`, voiced
   with `espeak-ng` and transcribed by faster-whisper `base` on the CPU in a throwaway instance on
   port 8412, with its config, sessions and recordings in a scratch directory. The language model
   titled the recordings and answered one question.
3. **Recapture the screenshots.** ✅ `main-screen.png`, `recordings.png`, `recording-window.png`,
   `record-sheet.png`, `settings-rewriting.png`; the four September images that are no longer
   used are deleted. `docs/assets/capture.py` shoots the new set and its docstring carries the
   recipe.
4. **Bring the documents along.** ✅ `docs/assets/README.md`, `docs/structure.md`,
   `docs/documentation.md` (D-087), `docs/checklist.md` (Part 13; the recapture item and the
   drafted-paragraphs item closed), and this plan in `docs/plans/README.md`.

## 3. Verification

- Every image the README references exists in `docs/assets/`, and no file there is unreferenced.
- `uv run pytest` and `uv run ruff check .` — no application code changed, but `capture.py` is
  linted.
- The instance on port 8395 and the repository's `data/` were not touched.
