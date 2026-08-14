/**
 * Pipeline health — level, speech state, real-time factor, latency, connection.
 *
 * Health updates arrive several times a second. This store throttles the change notification so
 * the level meter does not drive the whole application's render loop (FE §8.1).
 */

import { emit } from "../core/bus.js";

export const HEALTH_CHANGED = "store.health.changed";

/** Roughly four repaints a second is plenty for anything in the status bar. */
const THROTTLE_MS = 250;

class HealthStore {
  constructor() {
    this.level = { rms: 0, peak: 0, clipping: false };
    this.speaking = false;
    this.rtf = 0;
    this.commitLatency = 0;
    this.queueDepth = 0;
    this.droppedFrames = 0;
    this.modelId = "";
    this.source = "";
    this.healthy = true;
    this.connection = "connecting";
    this.modelLoad = null;

    this._timer = null;
    this._dirty = false;
  }

  setLevel(level) {
    this.level = level;
    this._touch();
  }

  setSpeaking(speaking) {
    if (this.speaking === speaking) return;
    this.speaking = speaking;
    this._touch();
  }

  setStatus(status) {
    this.rtf = status.rtf ?? 0;
    this.commitLatency = status.commit_latency_s ?? 0;
    this.queueDepth = status.queue_depth ?? 0;
    this.droppedFrames = status.dropped_frames ?? 0;
    this.modelId = status.model_id ?? this.modelId;
    this.source = status.source ?? this.source;
    this.healthy = status.healthy ?? true;
    this._touch();
  }

  setConnection(state) {
    if (this.connection === state) return;
    this.connection = state;
    // Connection changes are rare and important, so they bypass the throttle.
    this._flush();
  }

  setModelLoad(progress) {
    this.modelLoad = progress;
    this._flush();
  }

  /**
   * Whether the pipeline is falling behind.
   *
   * A factor of zero means nothing has been transcribed yet — a session that has just started, or
   * one where nobody has spoken. Reporting that as a failure would put a red indicator on screen
   * before the talk begins.
   */
  get fallingBehind() {
    return this.rtf > 0 && this.rtf < 1;
  }

  _touch() {
    this._dirty = true;
    if (this._timer) return;
    this._timer = setTimeout(() => {
      this._timer = null;
      if (this._dirty) this._flush();
    }, THROTTLE_MS);
  }

  _flush() {
    this._dirty = false;
    emit(HEALTH_CHANGED, this);
  }
}

export const health = new HealthStore();
