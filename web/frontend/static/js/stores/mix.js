/**
 * The running recording's mix: each sound's volume, mute and level (D-081).
 *
 * **Authoritative from the server, with one exception.** A slider being dragged shows where the
 * hand is, not where the last reply said it was — waiting for a round trip per pixel would make the
 * slider lag the pointer. So a change is applied here at once and sent, throttled; the server's
 * `mix.changed` then confirms it, and every other open page follows that event.
 *
 * Two topics, because they change at different rates. `MIX_CHANGED` is the settings, and redraws
 * the controls. `MIX_LEVELS_CHANGED` is the meters, four times a second, and must not rebuild a
 * slider out from under a pointer that is dragging it.
 */

import { emit } from "../core/bus.js";
import { api } from "../transport/api.js";

export const MIX_CHANGED = "store.mix.changed";
export const MIX_LEVELS_CHANGED = "store.mix.levels";

/** The shortest interval between two requests for one input while a slider is dragged. */
const SEND_INTERVAL_MS = 100;

class MixStore {
  constructor() {
    this.reset();
    /** Per input: the timer for a throttled send, and the change still waiting to go. */
    this._pending = new Map();
  }

  reset() {
    /** Input names in the order the run declared them: `window`, `system`, `microphone`. */
    this.inputs = [];
    /** `{name: {volume, muted, level, alive}}`. */
    this.levels = {};
    this.error = "";
  }

  /** Whether a recording with a mix is running. */
  get active() {
    return this.inputs.length > 0;
  }

  /** Adopt the server's mix: `GET /api/session` `mix`, `session.started`, or `mix.changed`. */
  set(state) {
    if (!state?.inputs?.length) {
      this.reset();
      emit(MIX_CHANGED, this);
      return;
    }
    this.inputs = [...state.inputs];
    const next = {};
    for (const name of this.inputs) {
      const incoming = state.levels?.[name] ?? {};
      // A change still waiting to be sent wins over a reply that predates it, or a slider being
      // dragged would jump back to where it was a moment ago.
      const waiting = this._pending.get(name)?.change ?? {};
      next[name] = { volume: 100, muted: false, level: 0, alive: true, ...incoming, ...waiting };
    }
    this.levels = next;
    emit(MIX_CHANGED, this);
  }

  /** Adopt a `mix.levels` frame: the meters only. */
  setLevels(levels) {
    if (!levels) return;
    for (const [name, reading] of Object.entries(levels)) {
      const current = this.levels[name];
      if (!current) continue;
      current.level = reading.level ?? 0;
      current.alive = reading.alive ?? current.alive;
    }
    emit(MIX_LEVELS_CHANGED, this);
  }

  /**
   * Change one input's volume (0–100) or mute. Applied here at once, sent at most every
   * {@link SEND_INTERVAL_MS}, and the last value is always sent — a drag that ends between two
   * sends must not leave the server one step behind the slider.
   */
  change(name, change) {
    const current = this.levels[name];
    if (!current) return;
    Object.assign(current, change);
    emit(MIX_CHANGED, this);

    const pending = this._pending.get(name) ?? { change: {}, timer: null };
    pending.change = { ...pending.change, ...change };
    this._pending.set(name, pending);
    if (pending.timer) return;
    this._send(name);
    pending.timer = setTimeout(() => this._flush(name), SEND_INTERVAL_MS);
  }

  _flush(name) {
    const pending = this._pending.get(name);
    if (!pending) return;
    pending.timer = null;
    if (Object.keys(pending.change).length) {
      this._send(name);
      pending.timer = setTimeout(() => this._flush(name), SEND_INTERVAL_MS);
    } else {
      this._pending.delete(name);
    }
  }

  async _send(name) {
    const pending = this._pending.get(name);
    if (!pending) return;
    const change = pending.change;
    pending.change = {};
    try {
      this.set(await api.setMix({ [name]: change }));
      this.error = "";
    } catch (error) {
      // Said rather than swallowed: a mute that did not reach the server is a microphone still
      // being recorded by someone who believes it is not.
      this.error = error.message;
      emit(MIX_CHANGED, this);
    }
  }
}

export const mix = new MixStore();
