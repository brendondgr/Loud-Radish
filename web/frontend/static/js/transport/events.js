/**
 * The event vocabulary, mirroring `web/shared/contracts/ws-events.json`.
 *
 * **The one rule that matters:** `transcript.committed` appends and is never modified;
 * `transcript.hypothesis` replaces the tentative tail wholly. Treating the hypothesis as the last
 * entry in the segment list duplicates text on screen.
 */

export const SESSION_STARTED = "session.started";
export const SESSION_STOPPED = "session.stopped";
export const SESSION_STATE = "session.state";

export const TRANSCRIPT_COMMITTED = "transcript.committed";
export const TRANSCRIPT_HYPOTHESIS = "transcript.hypothesis";
/** A finished minute rewritten for reading. Hides the raw segments in `source_ids`, never deletes
 *  them — a client that ignores this event keeps showing raw text, which is the right fallback. */
export const TRANSCRIPT_POLISHED = "transcript.polished";

/** How much audio the current recording has captured (D-021). Coalescing. */
export const RECORDING_PROGRESS = "recording.progress";
/** How far the post-capture transcription pass has got. Coalescing — a figure from ten seconds ago
 *  is worse than useless on a progress bar. */
export const TRANSCRIPTION_PROGRESS = "transcription.progress";
/** The pass finished. Never dropped: losing it leaves a progress bar running for a pass that ended. */
export const TRANSCRIPTION_DONE = "transcription.done";
/** The pass failed, and the recording is still on disk. The payload names the file it survives in. */
export const TRANSCRIPTION_FAILED = "transcription.failed";

export const AUDIO_LEVEL = "audio.level";
export const VAD_STATE = "vad.state";
export const STATUS = "status";
export const ASR_PROGRESS = "asr.progress";

export const SUMMARY_ADDED = "summary.added";
export const GLOSSARY_ADDED = "glossary.added";

export const CHAT_DELTA = "chat.delta";
export const CHAT_DONE = "chat.done";

export const ERROR = "error";

/** Connection-state topics, published on the bus rather than arriving from the server. */
export const CONNECTION_CHANGED = "connection.changed";

export const CONNECTED = "connected";
export const CONNECTING = "connecting";
export const DISCONNECTED = "disconnected";
