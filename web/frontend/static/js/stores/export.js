/**
 * One export, as the window reads it (D-037).
 *
 * Authoritative from the server, like the transcription pass and the window capture: an encode is a
 * subprocess the browser has no view of, and a client that inferred "still going" from the absence
 * of bad news would sit watching a job that failed ten minutes ago.
 *
 * The payload is `ExportJob.as_event()` and is adopted whole. Every event carries the same shape —
 * `export.progress`, `export.done` and `export.failed` differ only in which one arrived — so there
 * is one place that reads it and a separate question about what it means, which is what keeps the
 * two from getting out of step.
 */

import { emit } from "../core/bus.js";
import { DONE, FAILED, RUNNING } from "../core/export-presets.js";

export const EXPORT_CHANGED = "store.export.changed";

class ExportStore {
  constructor() {
    this.reset();
  }

  reset() {
    this.id = "";
    this.key = "";
    this.preset = "";
    this.state = "";
    this.progress = 0;
    this.elapsedSeconds = 0;
    this.remainingSeconds = 0;
    this.stages = [];
    this.estimatedBytes = 0;
    this.outputBytes = 0;
    this.error = "";
    emit(EXPORT_CHANGED, this);
  }

  /** Adopt an `export.*` event, or the `job` block from `GET /{key}/export/status`. */
  set(payload) {
    if (!payload) {
      this.reset();
      return;
    }
    this.id = payload.id ?? "";
    this.key = payload.key ?? "";
    this.preset = payload.preset ?? "";
    this.state = payload.state ?? "";
    this.progress = payload.progress ?? 0;
    this.elapsedSeconds = payload.elapsed_s ?? 0;
    this.remainingSeconds = payload.remaining_s ?? 0;
    this.stages = payload.stages ?? [];
    this.estimatedBytes = payload.estimated_bytes ?? 0;
    this.outputBytes = payload.output_bytes ?? 0;
    this.error = payload.error ?? "";
    emit(EXPORT_CHANGED, this);
  }

  get isRunning() {
    return this.state === RUNNING;
  }

  get isDone() {
    return this.state === DONE;
  }

  get hasFailed() {
    return this.state === FAILED;
  }

  /** Whether this job is the one a given session's window should be drawing. */
  covers(key) {
    return Boolean(this.state) && this.key === key;
  }
}

export const exportJob = new ExportStore();
