/**
 * The export presets — the mirror of the backend's plans (D-037).
 *
 * The canonical definition lives in `web/backend/app/services/export/presets.py`.
 * `tests/utils/test_export_preset_vocabulary.py` parses *this file* and asserts the two agree, so
 * "Balanced" meaning 720p on one side and 1080p on the other fails the suite rather than the
 * interface. The same arrangement `core/modes.js` is under, for the same reason.
 *
 * What is mirrored is the **vocabulary**, not the arithmetic: ids, labels, hints and the ceilings
 * they promise. Every size and duration on screen comes from `GET /{key}/export/options`, which
 * measured the actual recording — a number computed in the browser from these constants would be
 * describing a nominal file rather than the one on disk, which is the whole fault being repaired.
 *
 * Keep the shape of these declarations simple. The test reads them with a regular expression
 * rather than a JavaScript engine, which is a deliberate trade: no test-time toolchain, at the cost
 * of the file staying easy to parse.
 */

/** No re-encode at all. Exact, instant, and the largest. */
export const ORIGINAL = "original";
/** 1080p at the recorded frame rate. For keeping. */
export const HIGH = "high";
/** 720p at 15 fps. The default, and the answer to "this is too large to share". */
export const BALANCED = "balanced";
/** 540p at 10 fps. For a slow connection or a phone. */
export const SMALL = "small";
/** No picture at all. The smallest thing that still carries the whole talk. */
export const AUDIO = "audio";

export const EXPORT_PRESETS = Object.freeze([ORIGINAL, HIGH, BALANCED, SMALL, AUDIO]);

export const DEFAULT_PRESET = BALANCED;

/** The stages an export runs through, in order. Mirrors `services/export/runner.py`. */
export const MEASURE = "measure";
export const ENCODE = "encode";
export const TRANSCRIPT = "transcript";
export const PACKAGE = "package";

export const EXPORT_STAGES = Object.freeze([MEASURE, ENCODE, TRANSCRIPT, PACKAGE]);

/** What a job can be. Mirrors `ExportState` in `services/export/job.py`. */
export const RUNNING = "running";
export const DONE = "done";
export const FAILED = "failed";
export const CANCELLED = "cancelled";

export const EXPORT_STATES = Object.freeze([RUNNING, DONE, FAILED, CANCELLED]);

/** What one stage can be. Mirrors `StageState`. */
export const STAGE_STATES = Object.freeze(["waiting", "running", "done", "failed", "skipped"]);
