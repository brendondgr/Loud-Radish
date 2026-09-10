# Restructure plan — loud-radish

*Written 2026-09-10 from [`audit-report.md`](audit-report.md). **Nothing in this file has been applied.** Each stage is
a separate commit and independently revertable. Approval can be partial: per stage, or per line.*

**No file is moved, renamed or deleted by this plan.** The layout is already correct for a flat
Python application with a `web/` tree, `docs/structure.md` documents it accurately, and moving
anything would break `docs/workflow.md`, `.claude/launch.json`, the autostart unit and 25 plan
documents for no gain. That removes the entire class of risk a restructure normally carries. What
follows is corrections, additions and a README rewrite.

---

## Stage 1 — Truth. Merges alone.

Four false statements. No files move. This stage is a pure improvement and could ship on its own.

```
EDIT  README.md:44-52          Replace the test paragraph.
                               `uv run pytest` and `uv run ruff check .` — measured at
                               2,163 passed / 11 skipped / 98 s, no --ignore needed.
                               One line: the live-model tests need --run-live-llm.
                               Removes F1 (the endpoint claim), F2 (the no-timeout
                               claim) and F3 ("drop the flag").

EDIT  docs/skills/global-project-rules/SKILL.md §2
                               ← GOVERNING RULES, blocker severity
                               Replace the npm/web-frontend subsection with what is
                               true: server-rendered Jinja plus hand-written ES modules,
                               served static, no build step, no package manager, and the
                               reason that is a decision rather than an omission.
EDIT  .claude/skills/global-project-rules/SKILL.md    same correction, pointer copy
EDIT  .agents/skills/global-project-rules/SKILL.md    same correction, pointer copy
EDIT  .cursor/rules/global-project-rules.mdc          same correction, pointer copy
                               All four in this commit. The repo's own rule requires a
                               canonical skill change to land in all three tool
                               locations together.

EDIT  docs/plans/live-seminar-transcriber.md:6-16
                               ADDITIVE correction of F4. The "Where this stands" and
                               "To resume" blocks are kept verbatim under a dated
                               heading marking them as the state on 2026-08-28, with a
                               pointer to the completion note already on line 3.
                               ⚠ Not rewritten in place. docs/plans is a historical
                                 directory; editing a resume banner to match today
                                 would turn a true record of August into a false
                                 claim about now.

EDIT  docs/structure.md:193     notify.py → notification.py
EDIT  .env.example              Add LLM_TEST_ENDPOINT and LLM_TEST_MODEL with their
                                real defaults, in the test section.
EDIT  docs/workflow.md          Test command matches README's corrected one.
```

---

## Stage 2 — Legal and onboarding. Merges alone.

```
NEW   LICENSE                   MIT, 2026, Brendon D. G. R. Chosen by the owner.
                                ⚠ Irreversible in effect, not in file terms: once
                                  published under MIT, that grant cannot be withdrawn
                                  from anyone who received the code under it. Named
                                  separately rather than bundled, so the decision is
                                  explicit.
EDIT  pyproject.toml            license = "MIT" as a PEP 639 SPDX string, plus the
                                matching classifier.

EDIT  README.md                 One copy-pasteable line that installs uv, before
                                `uv sync`. Closes M3: the requirement is currently
                                stated in prose only, and a bare container stops at
                                `uv: command not found`.

EDIT  .gitignore                Remove the dead Node section (node_modules/, dist/,
                                .vite/, *.tsbuildinfo), so .gitignore, the README and
                                the governing rules all agree there is no build step.
```

---

## Stage 3 — The visual layer. Depends on nothing; can precede Stage 4.

Five screenshots are **already captured and verified**, at 2880×1800 for 2× rendering, against the
scripted mock speech model in an isolated clone on port 8396. No real seminar audio, transcript
text or session metadata appears in any of them. The transcript visible in them is the mock
backend's own default script, a fictional talk about self-adjoint operators.

```
NEW   docs/assets/live-transcript.png    Hero. Live session, eight committed segments,
                                         and an assistant answer citing five clickable
                                         transcript timestamps. The one image that
                                         shows what the product is.
NEW   docs/assets/settings-audio.png     The settings dialog, all seven sections
                                         visible, showing there is no config file.
NEW   docs/assets/settings-transcription.png
NEW   docs/assets/recordings.png         The archive, cropped to the session card:
                                         VIDEO / AUDIO / TRANSCRIPT badges and the
                                         export controls.
NEW   docs/assets/window-mode.png        Window capture, with the monitor pane.
NEW   docs/assets/README.md              How each was captured and how to regenerate:
                                         the mock config, the isolated clone, the
                                         playwright script. Makes the assets
                                         reproducible rather than mysterious.

EDIT  docs/structure.md                  Document docs/assets/ and why it exists.
```

Every image gets alt text describing what the reader is looking at, not what the file is. Paths are
relative, which is correct for GitHub and for clones. Nothing is hosted externally.

---

## Stage 4 — The README rewrite. Depends on Stages 1-3.

Reordered so the most disqualifying question is answered first, and so a reader who only wants to
run the app is not made to read a portfolio pitch to find `uv sync`. Target ~200 lines.

```
Title + one-liner                     ← the one-liner is yours, see "Blocked on you"
Hero screenshot                       ← the highest-value item on the page
Status, honestly, in one line         ← yours
Why it exists, two short paragraphs   ← yours; will not be invented
Run it — install and open, two commands
What it does — 5-7 concrete capabilities, the full product     ← fixes the main gap
Two or three further screenshots, captioned
How it fits together — the existing pipeline diagram, kept
Privacy — the existing section, kept
<details> Test, lint, upgrading from a pre-rename install </details>
Documentation table — kept, it is genuinely useful
Working in this repository — kept
Licence
```

Kept verbatim where it is already good, and it often is: the pipeline diagram, the two swap points,
the immutable-committed-text paragraph, the Privacy section, the documentation table, the
pre-rename upgrade note. **Rewriting accurate prose into smoother neutral phrasing is a downgrade,
so it will not happen.** The three things being added are the hero, the capabilities list, and the
Why and status lines.

No emoji headings, no badge row, no centred badge constellation, no decorative dividers, no
"✨ Features ✨". Those are individually harmless and collectively the recognisable signature of a
generated README, which on a portfolio repo is a negative signal.

---

## Stage 5 — CI. Optional, and honestly justified.

```
NEW   .github/workflows/ci.yml   uv sync → pytest → ruff, on push and pull request.
                                 Optionally a second job that runs the README's own
                                 documented commands from a clean checkout, which is
                                 what keeps the ~17 s figure in the audit report true
                                 six months from now.
EDIT  docs/workflow.md           Record the workflow.
EDIT  docs/documentation.md      Decision-log entry: why CI, and explicitly that it is
                                 not for appearance.
```

Say plainly in the decision entry that CI has no hiring-side evidence behind it. It is here because
a 98-second suite that nothing runs will drift and because documented commands rot silently. **No
CI badge in the README.**

---

## Stage 6 — Front page. Manual, by you, in the GitHub UI. Nothing to commit.

```
Repository description   One sentence, matching the README's one-liner.
Topics                   transcription, whisper, fastapi, local-first, speech-to-text,
                         llm, self-hosted — whichever are true.
Social preview           Upload docs/assets/live-transcript.png. Cannot be set from a
                         file; unset means shared links render as generic grey.
Pin the repository       On your profile, if you want it seen.
```

```
EDIT  .git/config        Repoint origin to git@github.com:brendondgr/loud-radish.git.
                         Local only, affects nothing published. The old URL still
                         redirects, so this is tidiness, not a fix.
```

---

## Documentation duties this plan carries

The repository's rules make these part of the change rather than a follow-up. Each is a line item
above, listed here so none is missed:

- `docs/structure.md` — `docs/assets/`, and the `notification.py` correction.
- `docs/documentation.md` — a decision entry for the audit itself, the licence choice, the CI
  decision and its stated reason, and the correction of the frontend rule.
- `docs/workflow.md` — the corrected test command, and CI if Stage 5 is taken.
- `docs/checklist.md` — the remaining items, and anything this audit found and did not fix.

`audit_docs.py . --governing --strict` runs as the final step and must exit zero. A non-zero exit
means the change is incomplete, not that the tool is wrong.

---

## Blocked on you — will not be invented

Three pieces of writing require knowing things the code does not contain. A plausible invented
version of any of them is a claim you would have to defend to someone who read it, so the plan
stops rather than filling them in. Drafts are offered for you to accept, edit or reject; nothing
goes in unapproved.

1. **The one-liner.** What it does, what it operates on, and the non-obvious part. The profile's
   current draft is mine and is a placeholder: *"Transcribes a talk on your own machine as it is
   spoken, then answers questions about what was said with timestamps you can click back to."*
2. **Why it exists.** What problem, for whom, and why not Otter or a Zoom transcript or plain
   Whisper afterwards. Two short paragraphs. This is the section 74% of READMEs skip and the one a
   hiring manager most needs, and it is the hardest line in the repository to write.
3. **The honest status line.** My reading of the evidence: solo, 208 commits over four weeks,
   fourteen planned phases complete, suite passing, Linux only, single user, loopback only, no
   authentication, some behaviours unverified on real hardware. Whether that reads as *active*,
   *prototype* or something else is your call, and `active` on a repository that goes quiet is a
   false claim on the front page.

---

## What this plan will not do

- **Move, rename or delete any source file.** The layout is right and 25 plan documents,
  `docs/workflow.md`, `.claude/launch.json` and the autostart unit all reference current paths.
- **Rewrite `docs/plans/` to match today's tree.** Historical directory, corrections appended only.
- **Touch `data/` or `logs/`.** Untracked, correctly gitignored, and containing real seminar
  recordings that must not be published.
- **Rewrite git history.** Not needed; no secrets were found in 208 commits and `.git` is 8 MB.
- **Publish any screenshot containing real transcript content.** Mock data only.
- **Add a badge row, stats cards, trophies, streak widgets, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue templates, `CODEOWNERS`, or a `CHANGELOG.md`.** Each
  is either unsupported by evidence or checkbox theatre on a solo repository, and the changelog
  question this repository has already decided in writing with a condition for revisiting.
- **Rewrite prose that is already accurate** into a smoother house voice.

---

## Verification after applying

```bash
uv run pytest && uv run ruff check .
python3 <skill>/scripts/audit_docs.py . --governing --strict
python3 <skill>/scripts/audit_readme.py .
python3 <skill>/scripts/check_links.py . --all
python3 <skill>/scripts/audit_assets.py .
```

All must pass. Then the rendered README is checked on the published page, because local previews
render things GitHub does not.
