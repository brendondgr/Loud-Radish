# Repository audit — loud-radish

*Audited 2026-09-10 against [`repo-profile.yaml`](../../repo-profile.yaml). Archetype `web-app`, secondary `tool`. Reader:
hiring manager, plus someone who just wants to run the app.*

Every finding below carries an evidence tier. Tiers, and the rule about which ones may be emitted
at all, come from the audit skill's evidence ledger. Where a recommendation is judgement rather
than measurement, it says so.

---

## 1. The thirty-second verdict

A stranger opening this repository today sees a URL that says **TranscriberPrototype**, a page
titled **Loud Radish**, a radish logo, and 117 lines of prose with no picture of the software in
it. They can tell it transcribes audio. They cannot tell what it looks like, that it records
video, that it exports, that it dictates into other applications, or that it has a desktop tray
presence. The word *Prototype* in the URL and the words *prototype* nowhere in the README pull in
opposite directions.

What they wrongly conclude: that this is a modest single-purpose script. It is a 463-file
application with 2,163 passing tests, sixteen service packages, two pages, thirty-odd API
endpoints, a WebSocket event contract, and 58 documents describing it. **The internal
documentation is stronger than the front door by a wide margin**, which is an unusual failure and
a cheap one to fix.

The single largest gap is not polish. It is that the README describes roughly half the product.

---

## 2. Gates

### TRUTH — **FAIL** (three false statements, all in the README)

The architecture, the pipeline diagram, the privacy claims, the "no Node toolchain" claim, the
"configurable from inside the application" claim and the mock-model default were each checked
against the code and are **true**. `docs/structure.md` lists 160 filenames and 159 of them exist.
That is a better hit rate than most audited repositories achieve.

Three statements do not survive checking, all of them in the README's test-command paragraph, and
one stale block in the plan the README links to. They are itemised as F1–F4 below.

Because the failures are localised, contained in one paragraph, and do not touch the description
of what the software does, the rest of this report is **not** provisional. Stage 1 of the plan
fixes them before anything else moves.

### RUNNABLE — **PASS**, and unusually fast

Measured in a clean container from a fresh `git clone`, using only the commands the README
documents:

| Step | Command | Wall clock |
|---|---|---|
| Setup | `uv sync` | 15.4 s (cold cache, 67 packages) |
| Run | `uv run app.py` → serving on 8395 | 1.2 s |
| **Total** | 2 commands | **~17 s** |

Container image `ghcr.io/astral-sh/uv:python3.11-bookworm-slim`, started 22:05:58Z, application
startup complete 22:06:15Z. Two documented steps, which meets the one-to-two-step standard.

Two caveats that must travel with that number. **First**, the container was given `uv`
pre-installed. A run against a bare `python:3.11-slim` image stops immediately at
`uv: command not found`, because the README states the `uv` requirement in prose above the code
block but never gives a command that installs it. That is finding M3, not a gate failure — the
requirement is documented, just not copy-pasteable. **Second**, verification covers Linux in a
container only. It says nothing about macOS, Windows, GPU code paths, real microphone capture, the
D-Bus screen-cast portal, or the ROCm wheel repair, none of which a container can exercise.

The test command was also run for real on this machine: `uv run pytest` gives **2,163 passed, 11
skipped in 98 s**, and `uv run ruff check .` is clean.

---

## 3. Findings

Ordered by fix priority, which weights evidence tier alongside severity. A minor finding with legal
force outranks a major one resting on convention.

### Blockers

```
[BLOCKER · LEGAL] No LICENSE file anywhere in the repository
  where:     repository root
  observed:  No LICENSE / LICENCE / COPYING / COPYRIGHT in .github/, root, or docs/.
             pyproject.toml declares no `license` field either.
  why:       Without a licence the work is under exclusive copyright. Nobody may copy,
             modify or redistribute it; GitHub's terms grant viewing and forking and
             nothing more. A portfolio repository nobody may legally use undercuts the
             reason for publishing it. This is the one hygiene item that is never
             advisory. A licence cannot be inherited from an org-level .github repo.
  fix:       MIT at the repository root, chosen by the owner 2026-09-10, plus
             `license = "MIT"` and a classifier in pyproject.toml (PEP 639 SPDX string,
             not the older table form).
  effort:    5 min, mechanical.
  verify:    audit_structure.py . --json → hygiene.no-license absent
```

```
[BLOCKER · FUNCTIONAL] A governing rules file mandates a frontend toolchain that does not exist
  where:     docs/skills/global-project-rules/SKILL.md §2, and its three pointer copies
  observed:  §2 reads "The frontend lives in web/frontend/ and is managed with npm",
             "package-lock.json is committed. Never hand-edit it", and "Run frontend
             commands from web/frontend/, not from the repository root."
             There is no package.json, no package-lock.json and no node_modules
             anywhere in the tree. `find . -name package.json` returns nothing.
             The README states the opposite, correctly: "There is no Node toolchain
             and no build step."
  why:       This file is the first thing every contributor and every AI agent is told
             to read, and the repository's own rules make it canonical. A governing
             rules file that contradicts the tree is blocker severity, not a nit: it
             instructs the next contributor to run commands that cannot work, and it
             contradicts the README on the same question.
  fix:       Replace §2's frontend subsection with what is actually true — server-rendered
             Jinja templates plus hand-written ES modules served as static files, no
             build step, no package manager — and say why, since "no build step" is a
             design decision worth recording rather than an omission.
  effort:    15 min, mechanical, but must land in all four locations.
  verify:    grep -rn "npm" docs/skills .claude .agents .cursor
```

### Major

```
[MAJOR · SURVEY-DATA] The README documents about half of what the application does
  where:     README.md, whole file
  observed:  Described: microphone / loopback / file capture, live transcription,
             a growing timestamped transcript, an LLM assistant, in-app settings.
             Present in the code, tested, reachable from the running UI, and absent
             from the README: window and video capture with a monitor pane
             (web/backend/app/services/capture/, routes/capture.py); session export in
             Markdown, a standalone web app, and mp4 (services/export/, nine modules);
             push-to-talk dictation that types into the focused window
             (services/dictation/, desktop/keystroke.py, a global shortcut);
             the desktop tray companion (app/companion/, fourteen modules);
             a live glossary; two-pass transcription with Live/Final revisions;
             LLM transcript polish with user-editable instructions.
  why:       The reader's first question is what this is, and the answer they get is
             narrower than the truth. Under-claiming is not a truth violation, but it
             is the specific failure the owner reported ("hard to determine what the
             actual product is"), and on a portfolio repo it wastes the strongest
             evidence available — that the thing is much bigger than it looks.
  fix:       A capabilities section of 5-7 concrete items, each one a sentence, written
             from the running application rather than from the code. Not a feature grid.
  effort:    ~45 min. The list is mechanical; the emphasis is the owner's call.
  verify:    read the running app's UI against the README section, item by item
```

```
[MAJOR · PRACTITIONER-CONSENSUS] No visual anywhere, on a repository whose whole point is a UI
  where:     README.md — one image reference, the logo SVG
  observed:  audit_readme.py: image_refs 1, that one being web/frontend/static/brand/
             radish.svg. No docs/assets directory. No screenshot, GIF or diagram of the
             application in any of the 58 documents.
  why:       For a web-app archetype the hero asset carries the README; a web app with
             no visual is a web app the reader assumes does not work. This is the
             consistent position of every practitioner source in the ledger, and it is
             judgement rather than measurement — but it is judgement with no dissent.
             GitHub renders SVG unreliably in Firefox and never animates it, so the
             logo cannot serve as the hero.
  fix:       Committed PNGs under docs/assets/, captured against the scripted mock
             speech model so no real seminar content is published. Five are already
             captured and verified (§5).
  effort:    done for capture; ~20 min to place and caption.
  verify:    audit_assets.py . --json ; check the rendered page after pushing
```

```
[MAJOR · CONVENTION] Nothing runs the test suite except a human who remembers to
  where:     no .github/ directory exists
  observed:  2,163 tests pass in 98 s and ruff is clean, on this machine, today.
             There is no CI workflow, so no commit is checked and no reader can see
             that the suite passes without cloning and running it.
  why:       **Stated plainly: adding CI has zero hiring-side evidence behind it.** Not
             one source in the ledger — including a 20-manager study with binding
             commitments — mentions checking a portfolio repo's CI. The honest reasons
             to add it here are the real ones: a 98-second suite that nothing runs
             will drift, the documented setup commands rot silently because nothing
             re-executes them, and a reader currently has no way to see the suite's
             state. Recommended for those reasons only.
  fix:       One workflow: uv sync, pytest, ruff, on push and pull request. Optionally
             a second job that runs the README's own documented commands from a clean
             checkout, which is what keeps §2's RUNNABLE result true six months from now.
  effort:    ~30 min.
  verify:    the workflow's own first green run
```

### Minor

```
[MINOR · FUNCTIONAL] F1-F3: three false statements about the test suite
  where:     README.md:46-52
  observed:  (F1) "tests/assistant/test_llm_live.py skips itself when nothing answers
             at LLM_TEST_ENDPOINT, but runs for real against a server that does."
             False. tests/conftest.py:116-131 gates them behind an opt-in
             --run-live-llm flag, and the file's own docstring says it "was previously
             opt-in by environment". A local server does answer at localhost:9090 on
             this machine, and `uv run pytest tests/assistant/test_llm_live.py` still
             reports 6 skipped in 0.10 s.
             (F2) "a full model generation has no timeout, so it is excluded above."
             False. pyproject.toml sets a 120-second per-test timeout, and its comment
             says it was added for exactly this file.
             (F3) "Drop the flag to include it." False. Dropping --ignore collects the
             tests; they still skip without --run-live-llm.
  why:       The documented test command is the one command a reader is most likely to
             run, and all three sentences explaining it are wrong. The command also
             carries an --ignore flag it no longer needs: plain `uv run pytest` is safe
             and was measured at 2,163 passed / 11 skipped / 98 s.
  fix:       `uv run pytest` and `uv run ruff check .`, with one line saying the live
             model tests need --run-live-llm.
  effort:    10 min, mechanical, already verified.
  verify:    uv run pytest -q
```

```
[MINOR · FUNCTIONAL] F4: the finished build plan still says to resume at Phase 9
  where:     docs/plans/live-seminar-transcriber.md:6-16
  observed:  Line 3 says "Status: complete (14 / 14 phases)". Lines 8-9, four lines
             below, say "Phases 1-8 and 11 are done. What remains is the language model
             (9), chat and the context pipeline (10), the settings interface (12), the
             chat interface (13), and hardening (14)", and line 15 says "To resume:
             ... start at Phase 9 below." The phase table at line 514 marks all
             fourteen complete.
  why:       The README links this file as "the build plan, phase by phase". A reader
             who follows the link is told, twice and in bold, to resume work on five
             phases that shipped. The repository's own rules require a plan's step
             status to be updated as phases complete.
  fix:       Additive correction, not a rewrite: retain the resume block as the record
             of where the work stood, under a heading that dates it and points at the
             completion note above. A plan is a historical handoff artifact and editing
             it in place would convert a true record of August into a false claim
             about today.
  effort:    10 min.
  verify:    read the first 20 lines and the phase table together
```

```
[MINOR · FUNCTIONAL] Two environment variables the code reads are missing from .env.example
  where:     .env.example ; tests/assistant/test_llm_live.py:30-31
  observed:  LLM_TEST_ENDPOINT and LLM_TEST_MODEL are read via os.environ.get with
             defaults. Neither appears in .env.example, whose own header states
             "Every variable below is read by the code" and whose D-055 note records
             the reverse fault being fixed once already.
  why:       The repository's rules require every environment variable to be listed in
             .env.example in the change that introduces it. The file is otherwise
             exemplary — this is the only gap in it.
  fix:       Add both, commented, in the test section, with their real defaults.
  effort:    5 min.
```

```
[MINOR · CONVENTION] docs/structure.md names a file that was renamed
  where:     docs/structure.md:193
  observed:  The tree lists `notify.py` under web/backend/app/desktop/. The file is
             `notification.py`; only a stale .pyc carries the old name. 1 of 160
             filenames in that tree is wrong.
  fix:       One-word edit.
  verify:    audit_docs.py . --trees
```

```
[MINOR · CONVENTION] .gitignore carries a Node section for a project with no Node
  where:     .gitignore:17-21
  observed:  node_modules/, dist/, .vite/, *.tsbuildinfo ignored. No JS toolchain
             exists, and the README says so.
  why:       Convention only, and it costs nothing to leave. Worth removing in the same
             change as the governing-rules fix so that all three places agree the
             frontend has no build step. Flagged as internal inconsistency, not as
             best practice.
  fix:       Delete the section, or retitle it to say why it is there.
```

### Advisory

```
[ADVISORY] The repository URL and the product name disagree
  observed:  The GitHub repository is now `loud-radish` (confirmed by the owner and by
             `git ls-remote`, which resolves for both names because GitHub redirects the
             old one). The local git remote still reads
             git@github.com:brendondgr/TranscriberPrototype.git, and the working
             directory, this session's docs, and several plan filenames still use the
             old name.
  fix:       Repoint the local remote. The directory name and historical plan text are
             cosmetic and are left alone — docs/plans is a historical directory.
```

```
[ADVISORY] The social preview image is unset, and cannot be set from a file
  observed:  Not detectable from the repository; it is a GitHub setting. Unset means a
             link shared in Slack or a DM renders as generic grey.
  fix:       Upload one by hand at Settings → Social preview. The hero screenshot works.
             The most commonly skipped front-page item.
```

**Explicitly not recommended**, because the evidence does not support it and the owner has probably
read advice that says otherwise: a badge row (GitHub's own docs never mention badges; decorative
ones are discounted on sight); profile stats cards, trophies or streak widgets (measured as poorly
correlated with developer quality, and part of a documented fake-profile signature); `CONTRIBUTING.md`,
`CODE_OF_CONDUCT.md`, `SECURITY.md`, issue templates or `CODEOWNERS` on a solo repository, where
they read as checkbox theatre; a `CHANGELOG.md`, which the repository has already considered and
declined in writing with a stated condition for revisiting; type hints or tests added for
appearance, both of which this repository already has for real reasons.

---

## 4. What was checked, and what was not

**Checked by execution.** Clean-clone setup and launch in a container. The full test suite, twice,
locally. Ruff. Both pages and the settings dialog driven in a real browser against an isolated
instance. Every internal documentation link (115, all resolve). The one external URL (200). The
`docs/structure.md` tree, filename by filename, against the real tree. Secret-shaped strings in the
working tree and in all 208 commits — none found. Repository weight: `.git` is 8 MB, and the 1.2 GB
of audio and video in `data/` is untracked and correctly gitignored.

**Checked by reading.** The README's architectural and privacy claims against the code. The
pipeline diagram. The mock-model default. The env-var inventory.

**Not checked, and not claimable.** Whether the software works on macOS or Windows. Whether real
microphone or system-loopback capture works, or the D-Bus screen-cast portal, or the ROCm wheel
repair, or GPU inference — a container cannot exercise any of them, and `docs/checklist.md` is the
honest record of which have been confirmed on real hardware. Whether the 58 documents' prose
matches current behaviour beyond the specific claims listed above; prose is not machine-checkable
and 58 files were not read line by line. Whether the code is good.

One tool artifact worth recording: the runnability script crashed during its own temp cleanup on a
root-owned `__pycache__` left by the container, so its report was lost. The numbers in §2 come from
the container's own timestamped logs instead, which is stronger evidence, not weaker. A root-owned
directory under `/tmp/repo-audit-*` remains; removing it needs privileges this audit did not take.

---

## 5. Measurements

Recorded so a re-audit is a diff rather than a re-argument.

| Measure | Value |
|---|---|
| Tracked files | 463 |
| `.git` size | 8 MB |
| Untracked working-tree media | 1.2 GB, 131 files, all under `data/` |
| Commits · span | 208 · 2026-08-14 to 2026-09-09 |
| Python / JS source files | 288 / 50 |
| Documentation files | 58 |
| Tests passing · duration | 2,163 passed, 11 skipped · 98 s |
| Steps to first run · wall clock | 2 · ~17 s in a clean container |
| README lines · sections | 117 · 7 |
| README funnel: what · visual · status · why | line 5 · none · line 82 · absent |
| Internal links checked · resolving | 115 · 115 |
| External URLs checked · 200 | 1 · 1 |
| `docs/structure.md` tree accuracy | 159 / 160 filenames |
| Screenshots captured, mock data, 2880×1800 | 5 |
| Secrets in tree or history | 0 |

The funnel row is the one to watch. "What" is answered at line 5, which is good. "Status" waits
until line 82 and "why this exists" is never answered at all — the two sections that surveys of 393
repositories find in 21% and 26% of READMEs respectively, and the two a hiring manager most needs.
Both must be written by the owner; neither can be inferred from the code, and an invented
motivation is a claim that has to be defended in an interview.
