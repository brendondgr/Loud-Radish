#!/usr/bin/env python
"""Recapture the README screenshots from a running instance.

**These images must never contain a real transcript.** A talk is exactly the kind of thing that
should not be published by accident, so the screenshots are taken against the scripted mock speech
model, whose output is a fictional lecture about self-adjoint operators
(``web/backend/app/services/asr/mock.py``, ``DEFAULT_SCRIPT_TEXT``). That is the whole reason this
script exists rather than a note saying "take some screenshots".

The recipe, which is deliberately run against a **throwaway clone** so nothing touches the real
``data/`` directory — ``paths.DATA_DIR`` is derived from the repository root, so a second instance
in the same tree would write its mock sessions next to real ones::

    git clone --local --no-hardlinks . /tmp/shots && cd /tmp/shots
    cat > data/loud-radish-config.json <<'JSON'
    {"asr": {"backend": "mock"},
     "vad": {"enabled": false},
     "llm": {"mode": "local",
             "local": {"endpoint": "http://localhost:9090/v1", "model": "default-model"}},
     "streaming": {"agreement_count": 2}}
    JSON
    uv sync && API_PORT=8396 uv run python app.py

``vad.enabled: false`` matters: with the silence gate on, a quiet room sends nothing to the model
and the transcript stays empty. With it off, every buffer reaches the mock and the mock replays its
script regardless of what the microphone heard.

Then press Start recording, ask the assistant one question so the pane has an answer in it, and::

    uv run --no-project --with playwright python docs/assets/capture.py docs/assets

Chromium comes from the system rather than from ``playwright install``, so this adds no dependency
to the project. Nothing here is imported by the application.
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/assets"
BASE = "http://127.0.0.1:8396"
CHROMIUM = "/usr/bin/chromium-browser"

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
        page.screenshot(path=f"{OUT}/live-transcript.png")

        page.get_by_role("button", name="Settings").first.click()
        page.wait_for_timeout(2500)
        page.screenshot(path=f"{OUT}/settings-audio.png")
        page.get_by_role("tab", name="Transcription").click()
        page.wait_for_timeout(1800)
        page.screenshot(path=f"{OUT}/settings-transcription.png")

        # Cropped: the archive is mostly empty space below the newest card, and a full-height
        # screenshot of a dark void communicates nothing.
        page.goto(f"{BASE}/sessions", wait_until="networkidle")
        page.wait_for_timeout(5000)
        page.screenshot(
            path=f"{OUT}/recordings.png", clip={"x": 0, "y": 0, "width": 1440, "height": 330}
        )

        page.goto(f"{BASE}/", wait_until="networkidle")
        page.wait_for_timeout(4000)
        page.get_by_text("Window", exact=True).first.click()
        page.wait_for_timeout(2500)
        # Cropped for the same reason as the archive: below the monitor pane the mode's empty
        # state is a tall column of nothing, which makes the README's image grid lopsided.
        page.screenshot(
            path=f"{OUT}/window-mode.png", clip={"x": 0, "y": 0, "width": 1440, "height": 590}
        )

        browser.close()
    print(f"wrote 5 screenshots to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
