/**
 * Capture mode, run state, and per-mode availability (D-020).
 *
 * The run state is derived from several sources — the session store, the socket, and a
 * post-processing pass that outlives capture — and every component that draws it needs the same
 * answer. One store computes it once; components subscribe. That is the same rule the rest of this
 * frontend follows, and the reason components never talk to each other.
 *
 * The vocabulary itself lives in `core/modes.js`, mirrored from the backend and kept identical by
 * `tests/utils/test_mode_vocabulary.py`. Nothing here invents a state name.
 */

import { emit } from "../core/bus.js";
import {
  ARMING,
  CAPTURE_MODES,
  ERROR,
  IDLE,
  LIVE,
  RECORDING,
  isBusy,
  isValidMode,
  statesFor,
} from "../core/modes.js";
import * as prefs from "../core/storage.js";

export const MODE_CHANGED = "store.mode.changed";

class ModeStore {
  constructor() {
    // Restored in `hydrateAvailability`, once we know whether the stored mode can still run.
    this.mode = LIVE;
    this.state = IDLE;
    this.message = "";
    /** Per mode: `{available, missing, reason}` as `GET /api/health` reports it. */
    this.availability = {};
    /** The options the pending run was armed with. Cleared when it ends. */
    this.options = null;
  }

  // -- mode selection ------------------------------------------------------------

  /**
   * Choose a capture mode.
   *
   * Refused while a session is doing anything: switching mid-run would mean rebuilding the
   * pipeline underneath a transcript that is still accumulating. Refused for a mode this machine
   * cannot run, which the selector should already have disabled — this is the second line.
   *
   * Returns whether the selection took.
   */
  select(mode) {
    if (!isValidMode(mode)) return false;
    if (isBusy(this.state)) return false;
    if (!this.isAvailable(mode)) return false;
    if (this.mode === mode) return true;

    this.mode = mode;
    // Selecting a mode does not clear the transcript. That happens on `session.started` and
    // nowhere else, which is an existing rule worth not breaking.
    this.state = IDLE;
    this.message = "";
    this.options = null;
    prefs.set("captureMode", mode);
    emit(MODE_CHANGED, this);
    return true;
  }

  /** Whether the mode selector may be operated right now. */
  get canSelectMode() {
    return !isBusy(this.state);
  }

  // -- availability --------------------------------------------------------------

  /**
   * Adopt the `modes` block from `GET /api/health`, then restore the remembered selection.
   *
   * The restore happens here rather than in the constructor because a stored mode is only worth
   * honouring if it can still run — a machine that has lost its capture backend since last time
   * should open on live, not on a greyed-out button.
   */
  hydrateAvailability(availability) {
    this.availability = availability ?? {};

    const remembered = prefs.get("captureMode");
    this.mode = isValidMode(remembered) && this.isAvailable(remembered) ? remembered : LIVE;

    emit(MODE_CHANGED, this);
  }

  isAvailable(mode) {
    // Unknown means not yet asked, not "no". Before health has answered, every mode is offered;
    // the server refuses anything it cannot do, so the worst case is one clear error instead of a
    // control that is inexplicably dead on load.
    const entry = this.availability[mode];
    return entry ? entry.available !== false : true;
  }

  /** Why a mode cannot run, for the disabled control's title. Empty when it can. */
  reasonFor(mode) {
    return this.isAvailable(mode) ? "" : (this.availability[mode]?.reason ?? "");
  }

  // -- run state -----------------------------------------------------------------

  /**
   * Move to a run state.
   *
   * A state the current mode cannot reach is refused rather than displayed: `live` has no
   * `processing` and only `window` arms, and a header showing a state its mode can never be in is
   * a bug that looks like a backend problem.
   */
  setState(state, message = "") {
    if (!statesFor(this.mode).includes(state)) return false;
    if (this.state === state && this.message === message) return true;

    this.state = state;
    this.message = state === ERROR ? message : "";
    if (state === IDLE) this.options = null;
    emit(MODE_CHANGED, this);
    return true;
  }

  /** Report a failure, whatever state the run was in. */
  fail(message) {
    this.state = ERROR;
    this.message = message || "Recording failed.";
    this.options = null;
    emit(MODE_CHANGED, this);
  }

  /** Adopt the state implied by the session store, without inventing one. */
  adoptSession({ running, mode }) {
    if (running && isValidMode(mode) && mode !== this.mode) {
      // The server is authoritative about a session that is already running — a reload during a
      // recording must show the mode being recorded, not the one this browser last selected.
      this.mode = mode;
    }
    if (running) {
      this.setState(RECORDING);
    } else if (this.state === RECORDING) {
      // Only from `recording`. A stop that leads into `processing` sets that itself, and
      // overwriting it here would hide the pass that is still running.
      this.setState(IDLE);
    }
    emit(MODE_CHANGED, this);
  }

  /** The options a window run was armed with. */
  arm(options) {
    this.options = options;
  }

  // -- what the record control does when pressed ---------------------------------

  /**
   * What pressing the primary control means right now: `start`, `arm`, `stop`, or `none`.
   *
   * The control asks this rather than checking a boolean, which is what lets one button serve six
   * states without each component working out the rules for itself.
   */
  get action() {
    switch (this.state) {
      case IDLE:
      case ERROR:
        return statesFor(this.mode).includes(ARMING) ? "arm" : "start";
      case ARMING:
      case RECORDING:
        return "stop";
      default:
        // `stopping` and `processing`. The control is disabled in both — a cancel here would
        // discard the only transcript of audio that is about to be deleted.
        return "none";
    }
  }

  /** Every mode, with what the selector needs to draw it. */
  get selectable() {
    return CAPTURE_MODES.map((mode) => ({
      mode,
      selected: mode === this.mode,
      available: this.isAvailable(mode),
      reason: this.reasonFor(mode),
    }));
  }
}

export const mode = new ModeStore();
