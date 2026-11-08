#!/usr/bin/env python
"""Recapture the README screenshots from a running instance.

**These images must never contain a real recording.** A talk is exactly the kind of thing that
should not be published by accident, so the screenshots are taken of made-up talks: the scripts in
``docs/assets/talks/``, read aloud by ``espeak-ng`` and transcribed by the real speech model. That
is the whole reason this script exists rather than a note saying "take some screenshots".

The recipe. Voice each script as a 16 kHz mono WAV::

    espeak-ng -v en-gb -s 165 -f talks/5-lighthouse.txt -w raw.wav
    ffmpeg -i raw.wav -af "adelay=1000,apad=pad_dur=2" -ar 16000 -ac 1 5-lighthouse.wav

Start a throwaway instance whose config, sessions and recordings all live in a scratch directory,
because the defaults are inside this repository's ``data/``::

    cat > $SCRATCH/config.json <<JSON
    {"asr": {"backend": "faster-whisper", "model": "base", "device": "cpu", "precision": "int8"},
     "audio": {"source_type": "file", "file_path": "$SCRATCH/1-standup.wav"},
     "llm": {"mode": "local", "local": {"endpoint": "http://localhost:4000/v1", "model": "auto"}},
     "storage": {"session_dir": "$SCRATCH/sessions"},
     "recording": {"recording_dir": "$SCRATCH/recordings"}}
    JSON
    LOUD_RADISH_CONFIG_PATH=$SCRATCH/config.json uv run app.py --port 8412 --no-takeover

Record each talk in turn: ``PATCH /api/config`` with ``audio.file_path`` (and ``audio.source_type:
file``), ``POST /api/session/start`` with ``{"options": {"source": "microphone",
"live_transcription": true, "post_transcription": false}}``, and ``POST /api/session/stop`` when
the file has played. Record the lighthouse lecture last, and when its final pass is done ask the
assistant a question that points at moments, such as *Why did the Fresnel lens replace the
parabolic reflector? Answer in a few plain sentences without formatting, and cite where each
reason was said.* The pane shows the answer as plain text, so Markdown would show its asterisks.
Then::

    CAPTURE_BASE=http://127.0.0.1:8412 \
        uv run --no-project --with playwright python docs/assets/capture.py docs/assets

Run the instance on the host, not in a container without GStreamer or PortAudio, or the record
sheet shows install errors instead of its choices. Chromium comes from the system rather than from
``playwright install``, so this adds no dependency to the project. Nothing here is imported by the
application.
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import Page, sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/assets"
BASE = os.environ.get("CAPTURE_BASE", "http://127.0.0.1:8396")
#: The system's Chromium by default. `CAPTURE_CHROMIUM` points at another — a Playwright cache
#: build, say — on a machine without one.
CHROMIUM = os.environ.get("CAPTURE_CHROMIUM", "/usr/bin/chromium-browser")

#: 1440x900 at 2x. GitHub renders a README at roughly 900px, so a 2880px source stays crisp on a
#: high-DPI display while the markup constrains the displayed width.
VIEWPORT = {"width": 1440, "height": 900}
SCALE = 2


def main() -> int:
    with sync_playwright() as play:
        browser = play.chromium.launch(
            executable_path=CHROMIUM, args=["--no-sandbox", "--disable-gpu"]
        )
        page = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE).new_page()

        page.goto(f"{BASE}/", wait_until="networkidle")
        page.wait_for_timeout(6000)
        # A transient banner (a hard-trim warning, say) would otherwise land in the hero shot.
        try:
            page.get_by_role("button", name="Dismiss this message").first.click(timeout=1500)
        except Exception:  # noqa: BLE001 - absence of the banner is the normal case
            pass
        page.wait_for_timeout(1500)
        page.screenshot(path=f"{OUT}/main-screen.png")

        # Everything after the hero needs the recording to have ended: the record sheet is behind
        # Start recording, and the newest card appears once its transcript is final.
        wait_until_idle(page)

        page.get_by_role("button", name="Settings").first.click()
        page.wait_for_timeout(2500)
        page.get_by_role("tab", name="Rewriting").click()
        page.wait_for_timeout(1800)
        page.screenshot(path=f"{OUT}/settings-rewriting.png")
        page.keyboard.press("Escape")

        recordings(page)
        record_sheet(page)

        browser.close()
    print(f"wrote 5 screenshots to {OUT}")
    return 0


def wait_until_idle(page: Page, timeout_s: float = 600) -> None:
    """Wait for the running session, and any pass after it, to finish."""
    for _ in range(int(timeout_s / 5)):
        state = page.request.get(f"{BASE}/api/session").json()
        if not state.get("running") and not state.get("transcribing"):
            return
        page.wait_for_timeout(5000)


def recordings(page: Page) -> None:
    """The Recordings page (D-085), and the compact window a card opens.

    Cropped to the first rows of cards: below them there is only empty page, which makes the
    README's image grid lopsided.
    """
    page.goto(f"{BASE}/sessions", wait_until="networkidle")
    page.wait_for_timeout(4000)
    page.screenshot(
        path=f"{OUT}/recordings.png", clip={"x": 0, "y": 0, "width": 1440, "height": 545}
    )
    page.locator(".rcard").first.click()
    page.wait_for_timeout(2500)
    page.locator(".rdetail").first.screenshot(path=f"{OUT}/recording-window.png")
    page.keyboard.press("Escape")


def record_sheet(page: Page) -> None:
    """The record sheet (D-075), which replaced the header's Live / Recorded / Window tabs.

    Shot with this computer's sound chosen and "Both" ticked: the window choice makes the sheet
    taller than its dialog, so part of it would be scrolled out of the picture. Only the dialog is
    captured: the page behind it is the hero shot again, under a scrim. The configured source must
    not be the file source, or the sheet says it is replaying a file instead of showing its
    choices.
    """
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_timeout(3000)
    page.get_by_role("button", name="Start recording").first.click()
    page.wait_for_timeout(800)
    page.locator("[data-record-source][value=system]").check(force=True)
    page.locator("[data-record-timing][value=both]").check(force=True)
    page.wait_for_timeout(500)
    page.locator("[data-record-dialog]").screenshot(path=f"{OUT}/record-sheet.png")
    page.keyboard.press("Escape")


if __name__ == "__main__":
    raise SystemExit(main())
