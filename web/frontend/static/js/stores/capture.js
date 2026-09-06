/**
 * The window capture, as the monitor pane reads it (D-022).
 *
 * Authoritative from the server, like the transcription pass: the recorder is a subprocess whose
 * life the browser has no view of, and a client that inferred "still recording" from the absence of
 * bad news would keep showing a live preview of a capture that died.
 */

import { emit } from "../core/bus.js";

export const CAPTURE_CHANGED = "store.capture.changed";

class CaptureStore {
  constructor() {
    this.recording = false;
    this.windowClosed = false;
    this.failed = false;
    this.stalled = false;
    this.stalledSeconds = 0;
    this.error = "";
    this.videoPath = "";
    this.bytes = 0;
    this.durationSeconds = 0;
    this.preview = false;
    this.options = null;
  }

  /** Adopt a `capture.state` event, or the block on `GET /api/session`. */
  set(payload) {
    if (!payload) {
      this.reset();
      return;
    }
    this.recording = Boolean(payload.recording);
    this.windowClosed = Boolean(payload.window_closed);
    this.failed = Boolean(payload.failed);
    // Alive and writing nothing. Distinct from `failed` on purpose: the recording is still going
    // and there is still time to do something about the window it is pointed at.
    this.stalled = Boolean(payload.stalled);
    this.stalledSeconds = payload.stalled_seconds ?? 0;
    this.error = payload.error ?? "";
    this.videoPath = payload.video_path ?? "";
    this.bytes = payload.bytes ?? 0;
    this.durationSeconds = payload.duration_s ?? 0;
    this.preview = Boolean(payload.preview);
    this.options = payload.options ?? null;
    emit(CAPTURE_CHANGED, this);
  }

  reset() {
    this.recording = false;
    this.windowClosed = false;
    this.failed = false;
    this.stalled = false;
    this.stalledSeconds = 0;
    this.error = "";
    this.videoPath = "";
    this.bytes = 0;
    this.durationSeconds = 0;
    this.preview = false;
    this.options = null;
    emit(CAPTURE_CHANGED, this);
  }

  /** Just the file name — the full path is a server path and means nothing in a browser. */
  get fileName() {
    return this.videoPath ? this.videoPath.split("/").pop() : "";
  }

  get sizeLabel() {
    if (!this.bytes) return "—";
    if (this.bytes < 1024 * 1024) return `${(this.bytes / 1024).toFixed(0)} KB`;
    return `${(this.bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  /** Which of the three switches were on, as a sentence rather than three booleans. */
  get optionsLabel() {
    if (!this.options) return "—";
    const on = [];
    if (this.options.video) on.push("video");
    if (this.options.live_transcription) on.push("live transcript");
    if (this.options.post_transcription) on.push("second pass");
    return on.length ? on.join(", ") : "nothing";
  }
}

export const capture = new CaptureStore();
