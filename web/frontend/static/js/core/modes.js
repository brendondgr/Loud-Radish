/**
 * Capture modes and run states — the mirror of the backend's vocabulary (D-020).
 *
 * The canonical definition lives in `web/backend/app/services/session/modes.py`.
 * `tests/utils/test_mode_vocabulary.py` parses *this file* and asserts the two agree, so a name
 * added on one side and forgotten on the other fails the suite rather than the interface.
 *
 * Keep the shape of these declarations simple. The test reads them with a regular expression rather
 * than a JavaScript engine, which is a deliberate trade: no test-time toolchain, at the cost of the
 * file staying easy to parse. Add entries in the same style; do not compute them.
 */

/** Continuous capture, transcribed as it arrives. The original behaviour. */
export const LIVE = "live";
/** Capture to a file with no inference; transcribe the whole file once it stops. */
export const RECORDED = "recorded";
/** A chosen window, with live transcription, post-process transcription, and video each optional. */
export const WINDOW = "window";

export const CAPTURE_MODES = Object.freeze([LIVE, RECORDED, WINDOW]);

/** Nothing is running. The only state from which the mode may be changed. */
export const IDLE = "idle";
/** Collecting options, or waiting for the desktop's window picker. Nothing captured yet. */
export const ARMING = "arming";
/** Capture is running. */
export const RECORDING = "recording";
/** Capture is held. The recording does not advance and the clock does not move (D-044). */
export const PAUSED = "paused";
/** Asked to stop, closing files. Brief, and not interruptible. */
export const STOPPING = "stopping";
/** Capture finished; a transcription pass is running over what it produced. */
export const PROCESSING = "processing";
/** Something failed in a way the user has to see. Leaves by returning to idle. */
export const ERROR = "error";

export const RECORD_STATES = Object.freeze([
  IDLE,
  ARMING,
  RECORDING,
  PAUSED,
  STOPPING,
  PROCESSING,
  ERROR,
]);

/**
 * Which run states each mode can actually reach.
 *
 * `live` never processes — nothing is left to do when capture stops — and never arms, having no
 * options to collect. `recorded` always processes. `window` may, depending on whether post-process
 * transcription was switched on before capture began.
 */
export const MODE_STATES = Object.freeze({
  live: Object.freeze([IDLE, RECORDING, PAUSED, STOPPING, ERROR]),
  recorded: Object.freeze([IDLE, RECORDING, PAUSED, STOPPING, PROCESSING, ERROR]),
  window: Object.freeze([IDLE, ARMING, RECORDING, PAUSED, STOPPING, PROCESSING, ERROR]),
});

/** States in which a session is doing something and the mode may not be changed. */
export const BUSY_STATES = Object.freeze([ARMING, RECORDING, PAUSED, STOPPING, PROCESSING]);

/**
 * What each mode needs beyond a plain install, keyed as `GET /api/health` reports it.
 *
 * Empty is the common case and is deliberate — only `window` needs a capability the application
 * cannot substitute for. A missing capture device does *not* disable the audio modes: the file
 * source replaces one entirely, and a missing device already has its own remedy path in the audio
 * settings tab. See the canonical note in `services/session/modes.py`.
 */
export const MODE_REQUIREMENTS = Object.freeze({
  live: Object.freeze([]),
  recorded: Object.freeze([]),
  window: Object.freeze(["window_capture"]),
});

/** The run states `mode` can reach, or an empty list for a mode that does not exist. */
export function statesFor(mode) {
  return MODE_STATES[mode] ?? Object.freeze([]);
}

/** Whether `mode` names a capture mode. */
export function isValidMode(mode) {
  return Object.hasOwn(MODE_STATES, mode);
}

/** Whether a session in `state` is doing something that must not be interrupted. */
export function isBusy(state) {
  return BUSY_STATES.includes(state);
}
