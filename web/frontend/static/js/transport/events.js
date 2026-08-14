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
