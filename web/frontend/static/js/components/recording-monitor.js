/**
 * The recording monitor — the third pane's contents (D-022).
 *
 * **The preview is pulled, never pushed.** The socket's backpressure policy makes transcript events
 * undroppable, and video frames sharing that channel are the one thing that could delay a committed
 * segment. So this fetches a still image on a timer instead, and the timer stops whenever nobody is
 * looking: a hidden pane or a backgrounded tab encodes JPEGs for no one.
 *
 * When there is no preview — no `jpegenc`, a failed branch, or simply the first frame not written
 * yet — the pane shows a card saying so. A black rectangle and a broken capture look identical, and
 * telling them apart is the whole reason to look at this pane.
 */

import { on } from "../core/bus.js";
import { $, setText, toggle } from "../core/dom.js";
import { timestamp } from "../core/format.js";
import { CAPTURE_CHANGED, capture } from "../stores/capture.js";

/** One frame a second, matching what the pipeline writes. Any faster just re-fetches the same file. */
const REFRESH_MS = 1000;

export class RecordingMonitor {
  constructor(root, { isVisible } = {}) {
    this.root = root;
    if (!root) return;

    this.isVisible = isVisible ?? (() => !root.hidden);
    this.previewWrap = $("[data-monitor-preview]", root);
    this.empty = $("[data-monitor-empty]", root);
    this.emptyTitle = $("[data-monitor-empty-title]", root);
    this.emptyBody = $("[data-monitor-empty-body]", root);
    this.stats = $("[data-monitor-stats]", root);
    this.clock = $("[data-monitor-clock]", root);
    this.windowLabel = $("[data-monitor-window]", root);
    this.output = $("[data-monitor-output]", root);
    this.size = $("[data-monitor-size]", root);
    this.optionsLabel = $("[data-monitor-options]", root);

    this.image = null;
    this.timer = null;
    //: Whether a preview frame has ever decoded for *this* recording. An `<img>` with an
    //: unresolved src is a blank rectangle, indistinguishable from a dead capture.
    this.hasFrame = false;

    on(CAPTURE_CHANGED, () => this.render());

    // A backgrounded tab still runs timers, just slowly — and encoding a frame nobody can see is
    // pure waste on a CPU already sharing itself with a speech model.
    // **Refresh immediately on becoming visible, then resume the timer.** Background tabs throttle
    // `setInterval` to a second at best and can freeze it entirely, so a tab returning from the
    // background would otherwise show a frame from whenever it was last awake — a frozen picture,
    // which is the symptom this pane exists to distinguish from a dead capture.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) this._refresh();
      this._syncTimer();
    });

    this.render();
  }

  render() {
    if (!this.root) return;

    const active = capture.recording || capture.bytes > 0;
    toggle(this.stats, active);
    setText(this.clock, timestamp(capture.durationSeconds));
    setText(this.windowLabel, capture.recording ? "Shared window" : "—");
    setText(this.output, capture.fileName || "—");
    setText(this.size, capture.sizeLabel);
    setText(this.optionsLabel, capture.optionsLabel);

    this._renderPreview();
    this._syncTimer();
  }

  _renderPreview() {
    const wanted = capture.recording && capture.preview;

    if (!wanted) {
      this.image?.remove();
      this.image = null;
      // Reset, so the next recording waits for its own first frame rather than inheriting the
      // last one's answer.
      this.hasFrame = false;
    }

    // **Three states, not two.** Recording-with-frames, recording-but-nothing-has-arrived-yet, and
    // not-recording are genuinely different situations and the first two used to render
    // identically: an `<img>` whose src had not resolved, which is a blank rectangle — the exact
    // thing this pane exists not to show. The card stays up until a frame has actually decoded.
    const showImage = wanted && this.hasFrame;
    const { title, body } = this._emptyCopy();

    this.empty?.setAttribute("data-variant", capture.failed ? "failed" : "idle");
    toggle(this.empty, !showImage);
    setText(this.emptyTitle, title);
    if (this.emptyBody) this.emptyBody.textContent = body;

    if (wanted && !this.image) {
      this.image = new Image();
      this.image.alt = "The window being recorded";
      this.image.addEventListener("load", () => {
        if (this.hasFrame) return;
        this.hasFrame = true;
        this._renderPreview();
      });
      // **Retry rather than give up.** A frame that fails to load leaves the previous one on
      // screen rather than a broken-image icon — but the important part is that it tries again.
      // The route legitimately 404s before the first frame is written and while one is being
      // rewritten in place, and a pane that treated the first 404 as permanent stayed empty for
      // the rest of the recording. A pane that retries forever is strictly better.
      this.image.addEventListener("error", () => {
        if (!this.hasFrame) setTimeout(() => this._refresh(), REFRESH_MS);
      });
      this.previewWrap?.append(this.image);
      this._refresh();
    }

    toggle(this.image, showImage);
  }

  _emptyCopy() {
    if (capture.failed) {
      return {
        title: "Video recording stopped",
        body: capture.error || "The recorder stopped unexpectedly. Audio is unaffected.",
      };
    }
    if (capture.windowClosed) {
      return {
        title: "The window was closed",
        body:
          "Video ended when the window closed. Audio is still recording, and everything captured " +
          "up to that point is kept.",
      };
    }
    if (capture.recording && !capture.preview) {
      return {
        title: "Recording — no preview available",
        body:
          "The capture is running. This machine has no JPEG encoder for GStreamer, so there is " +
          "nothing to show here; the recording itself is unaffected.",
      };
    }
    if (capture.recording) {
      // Preview is on and no frame has decoded yet. The branch writes one a second, so this is
      // normally over in under two — but saying so is better than a blank rectangle that could
      // equally mean the capture is dead.
      return {
        title: "Recording — waiting for the first frame",
        body: "The capture has started. The preview updates once a second.",
      };
    }
    return {
      title: "Not recording a window",
      body:
        "Choose Window above and press Start recording. Your desktop will ask which window to " +
        "share — this application never sees the others.",
    };
  }

  // -- the refresh timer -----------------------------------------------------------

  _syncTimer() {
    const wanted =
      capture.recording && capture.preview && this.isVisible() && !document.hidden;

    if (wanted && this.timer === null) {
      this.timer = setInterval(() => this._refresh(), REFRESH_MS);
    } else if (!wanted && this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  _refresh() {
    if (!this.image) return;
    // A cache-busting parameter rather than trusting no-store: the file is rewritten in place at
    // the same URL roughly once a second, and a cached frame reads as a capture that has frozen.
    this.image.src = `/api/capture/preview.jpg?t=${Date.now()}`;
  }

  /** Called when the pane is shown or hidden, so the timer follows visibility. */
  visibilityChanged() {
    this._syncTimer();
  }
}
