# Assets

Screenshots for the root `README.md`. Committed rather than hosted, because an image host outlives
nothing and a broken picture on the front page is worse than no picture at all.

**Every transcript visible in these images is fake, and that is deliberate.** They were captured
against the scripted mock speech model, whose output is a fictional lecture about self-adjoint
operators — see `DEFAULT_SCRIPT_TEXT` in `web/backend/app/services/asr/mock.py`. No real audio,
transcript, session date or file size appears anywhere in `docs/assets/`, and none ever should:
the recordings this application produces are talks given by real people who did not agree to be
published.

The assistant answers *are* real. A local language model was asked one question about the mock
transcript, and what it said is what the screenshot shows.

| File | What it shows |
|---|---|
| `live-transcript.png` | A live session: committed paragraphs on the left, an assistant answer citing clickable transcript timestamps on the right. The hero image. |
| `settings-audio.png` | The settings dialog, all seven sections, open on Audio. Evidence for the claim that there is no config file to edit. |
| `settings-transcription.png` | Speech-model selection — engine, model, device, precision, language. |
| `recordings.png` | The archive, cropped to one session card: what it holds, and the export controls. |
| `window-mode.png` | Window capture, with the monitor pane and the per-run options. |

All five come from a 1440×900 viewport at device scale 2, so they stay crisp on a high-DPI display
while the README constrains the displayed width. Three are full frames at 2880×1800; `recordings.png`
and `window-mode.png` are cropped, because below the content each one is meant to show there is only
a tall column of empty dark space, and that makes the README's image grid lopsided. About 1.6 MB in
total.

## Regenerating them

`capture.py` in this directory does the capture and its docstring carries the full recipe: clone
the repository to a throwaway directory, write a config that selects the mock backend and turns the
silence gate **off**, start on port 8396, record for a minute, ask the assistant one question, then
run the script.

The throwaway clone is not optional. `paths.DATA_DIR` is derived from the repository root, so a
second instance started inside this tree would write its mock sessions into the real `data/`
directory alongside actual recordings.

Chromium comes from the system rather than from `playwright install`, and playwright itself is
pulled in per-run with `uv run --with`, so nothing here appears in `pyproject.toml`. This directory
is documentation; no application code imports it.
