# Assets

The banner and screenshots for the root `README.md`. Committed rather than hosted, because an image
host outlives nothing and a broken picture on the front page is worse than no picture at all.

**Every recording visible in these images is made up, and that is deliberate.** The talks are
short fictional scripts (a team standup, a seminar on kelp forests, a podcast about typewriters, a
garden-club call and a lecture on lighthouse lenses) read aloud by `espeak-ng` and transcribed by
the real speech model. No real audio or transcript appears anywhere in `docs/assets/`, and none
ever should: the recordings this application produces are of real people who did not agree to be
published.

The titles on the cards and the assistant's answer *are* real: a local language model wrote them
from those fictional transcripts.

| File | What it shows |
|---|---|
| `header.svg` | The banner the README opens with: the mark on its cream disc, the wordmark, and two rows of chips naming the languages and the stack. Generated — see below. |
| `main-screen.png` | The hero. A live session in three columns: details, the transcript with timestamps in the margin, and an assistant answer citing clickable timestamps. |
| `recordings.png` | The Recordings page: cards grouped by day, titled by the model. Cropped to the cards. |
| `recording-window.png` | The compact window a card opens: what was said, *Bring to main screen*, and the exports. |
| `record-sheet.png` | The record sheet behind *Start recording*: what to record, and when to transcribe it. |
| `settings-rewriting.png` | The settings dialog, all seven sections, open on Rewriting: the language model's instructions are an editable setting. |

The screenshots come from a 1440×900 viewport at device scale 2, so they stay crisp on a high-DPI
display while the README constrains the displayed width. `main-screen.png` and
`settings-rewriting.png` are full frames; the other three are cropped to their content.

## Regenerating the banner

`uv run docs/assets/make_header.py`. It reads `web/frontend/static/brand/radish.svg` and writes
`header.svg`, so the mark has one source and the banner cannot drift away from it. Re-run it after
any change to the mark, and after any change to the wordmark, the tagline, or the chip rows — all
three are constants at the top of the script. **The tagline is a two-line condensation of the
README's opening paragraph**, not a copy of it, which makes it the one thing in here that can go
quietly stale: rewrite that paragraph and this file needs re-reading as well as re-running.

It is a generated file and it is also committed, because GitHub renders the README from the tree
and there is nothing to run the generator at that point.

Two things about it are not arbitrary. The mark sits on a cream disc at the same proportions the
application header uses, because its outline and legs are near-black and vanish on a dark ground.
And the banner carries its own dark panel rather than sitting on the page, because GitHub renders
the same file on a light background and a dark one.

## Regenerating the screenshots

`capture.py` in this directory does the capture and its docstring carries the full recipe: voice
the scripts with `espeak-ng`, start a throwaway instance on another port with its own config,
session directory and recording directory, record the talks through it, ask the assistant one
question during the last one, then run the script.

The separate config and directories are not optional. The default session and recording
directories are inside this repository's `data/`, so a second instance left on the defaults would
write its made-up recordings next to real ones.

Chromium comes from the system rather than from `playwright install`, and playwright itself is
pulled in per-run with `uv run --with`, so nothing here appears in `pyproject.toml`. This directory
is documentation; no application code imports it.
