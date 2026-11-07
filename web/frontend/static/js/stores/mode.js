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
  ERROR,
  IDLE,
  LIVE,
  PAUSED,
  PROCESSING,
  RECORDING,
  deriveMode,
  isBusy,
  isValidMode,
  statesFor,
} from "../core/modes.js";

export const MODE_CHANGED = "store.mode.changed";

class ModeStore {
  constructor() {
    // The mode of the run in progress, or of the last one. Never chosen directly (D-075).
    this.mode = LIVE;
    this.state = IDLE;
    this.message = "";
    /** Per mode: `{available, missing, reason}` as `GET /api/health` reports it. */
    this.availability = {};
    /** What the record sheet asked for: the pending run's, the running one's, or the last one's. */
    this.options = null;
  }

  // -- availability --------------------------------------------------------------

  /**
   * Adopt the `modes` block from `GET /api/health`.
   *
   * There is no selected mode to restore any more (D-075): the record sheet asks what to record
   * every time, and remembers its own last answers. Availability is what the sheet needs, to
   * disable a source this machine cannot run and say why.
   */
  hydrateAvailability(availability) {
    this.availability = availability ?? {};
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
  adoptSession({ running, mode, paused = false, options = null }) {
    if (isValidMode(mode) && mode !== this.mode && (running || !isBusy(this.state))) {
      // The server is authoritative about the session it is running — a reload during a recording
      // must show the mode being recorded. When idle, the last session's mode decides which panes
      // are offered (the monitor follows a window recording) until the next one starts.
      this.mode = mode;
    }
    if (options) this.options = options;
    if (running) {
      // A reload during a pause must come back paused. `running` stays true while a session is
      // held — it owns its device, its file and its store — so the server's `paused` flag is what
      // separates the two, and without it a reload would silently show a recording that is not
      // advancing as one that is.
      this.setState(paused ? PAUSED : RECORDING);
    } else if (this.state !== PROCESSING && this.state !== ERROR && this.state !== ARMING) {
      // Everything except those three returns to idle, `stopping` very much included — that is the
      // state a stop *passes through*, and an earlier version of this guard listed only
      // `recording`, which left the header reading "Stopping…" with a disabled control for the
      // rest of the session. `processing` is excluded because the post-capture pass owns it and
      // outlives the session; `error` because the user has not seen it yet; `arming` because the
      // record sheet is open, and a status message arriving meanwhile must not close the run.
      this.setState(IDLE);
    }
    emit(MODE_CHANGED, this);
  }

  /** The record sheet is open. Nothing is captured yet, and cancelling costs nothing. */
  beginArming() {
    this.options = null;
    this.state = ARMING;
    this.message = "";
    emit(MODE_CHANGED, this);
  }

  /**
   * The record sheet was confirmed with `options`.
   *
   * The mode is derived here as well as on the server, so the interface can show what is about to
   * run while the start request is in flight; `session.started` then carries the server's answer,
   * which wins.
   */
  arm(options) {
    this.options = options;
    this.mode = deriveMode(options.source, options.post_transcription);
    emit(MODE_CHANGED, this);
  }

  // -- what the record control does when pressed ---------------------------------

  /**
   * What pressing the primary control means right now: `arm`, `stop`, or `none`.
   *
   * The control asks this rather than checking a boolean, which is what lets one button serve every
   * state without each component working out the rules for itself. There is no `start` any more:
   * every recording begins at the record sheet (D-075).
   */
  get action() {
    switch (this.state) {
      case IDLE:
      case ERROR:
        return "arm";
      case ARMING:
      case RECORDING:
      case PAUSED:
        // A paused session still stops. Pausing is a hold, not a way out of one.
        return "stop";
      default:
        // `stopping` and `processing`. The control is disabled in both — a cancel here would
        // discard the only transcript of audio that is about to be deleted.
        return "none";
    }
  }
}

export const mode = new ModeStore();
