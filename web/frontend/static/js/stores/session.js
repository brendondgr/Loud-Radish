/**
 * Session state — whether recording is running, and for how long.
 *
 * Elapsed time is derived from the start timestamp rather than counted, so it stays correct across
 * a tab being backgrounded, a reconnect, or the machine sleeping.
 */

import { emit } from "../core/bus.js";

export const SESSION_CHANGED = "store.session.changed";

class SessionStore {
  constructor() {
    this.running = false;
    this.sessionId = null;
    this.startedAt = null;
    this.stoppedAt = null;
    //: When the current hold began, or null. Held time is subtracted from the clock, because a
    //: pause removes time from the recording rather than adding silence to it (D-044).
    this.pausedAt = null;
    //: Seconds this session has spent held, across every pause so far.
    this.heldSeconds = 0;
    this.title = "";
    this.stats = null;
    this.config = null;
    //: The capture mode this session is running in (D-020). The server is authoritative: a reload
    //: mid-recording must show what is being recorded, not what this browser last selected.
    this.mode = "live";
  }

  start(payload) {
    this.running = true;
    this.sessionId = payload.session_id ?? null;
    this.startedAt = payload.started_at ? new Date(payload.started_at) : new Date();
    this.stoppedAt = null;
    this.pausedAt = null;
    this.heldSeconds = 0;
    this.mode = payload.mode ?? this.mode;
    this.config = payload.config ?? this.config;
    emit(SESSION_CHANGED, this);
  }

  stop(payload = {}) {
    this.running = false;
    // Recorded so the clock freezes at the session's length. Leaving it unset kept the clock
    // ticking after the recording ended, which reads as "still recording" next to a button that
    // says Start.
    this.stoppedAt = new Date();
    // A session stopped while held keeps the hold it was in, or the clock would jump forward by
    // the length of the pause at the moment it froze.
    this._closeHold();
    this.stats = payload.stats ?? this.stats;
    emit(SESSION_CHANGED, this);
  }

  /** Capture is held. The clock stops here and resumes where it stopped. */
  pause() {
    if (!this.running || this.pausedAt) return;
    this.pausedAt = new Date();
    emit(SESSION_CHANGED, this);
  }

  /** Capture continues. Whatever the hold cost is taken out of the clock for good. */
  resume() {
    this._closeHold();
    emit(SESSION_CHANGED, this);
  }

  _closeHold() {
    if (!this.pausedAt) return;
    this.heldSeconds += Math.max(0, (Date.now() - this.pausedAt.getTime()) / 1000);
    this.pausedAt = null;
  }

  /**
   * Adopt the resolved configuration, so the header is right before any session starts.
   *
   * Without this the only source of `config` is a `session.started` payload, and the header reads
   * "Assistant not set up" and "Fully local" from a null configuration until the first recording —
   * which is exactly when a user checks it.
   */
  setConfig(config) {
    this.config = config;
    emit(SESSION_CHANGED, this);
  }

  /** Adopt the state the server reported on connect. */
  hydrate(state) {
    const session = state?.session;
    this.running = Boolean(state?.running);
    this.sessionId = session?.session_id ?? null;
    this.startedAt = session?.started_at ? new Date(session.started_at) : null;
    // **`ended_at`, not the local stop.** A fresh page has never seen a stop, so on a reload after
    // a session ended `stoppedAt` was null and `elapsedSeconds` measured from the old start time
    // to *now* — a clock climbing forever under a button that says "Start recording", which is
    // precisely what was reported. The server knows when it ended; take that.
    this.stoppedAt = this.running ? null : (_endedAt(session) ?? this.stoppedAt);
    this.pausedAt = state?.paused ? new Date() : null;
    // **Reconciled against the server, not accumulated locally.** A page that has just loaded has
    // seen no pauses at all, so it cannot know how long this session was held; the server reports
    // the audio it has actually consumed, and the difference from wall-clock elapsed is exactly
    // that. Without this a reload during a paused session shows a clock ahead of its transcript.
    this.heldSeconds = _heldFrom(state, this.startedAt, this.stoppedAt);
    this.title = session?.title ?? "";
    this.mode = session?.mode ?? this.mode;
    this.stats = state?.stats ?? null;
    emit(SESSION_CHANGED, this);
  }

  /** Seconds the session ran for — still climbing while recording, frozen once it stops. */
  get elapsedSeconds() {
    if (!this.startedAt) return 0;
    const end = this.pausedAt
      ? this.pausedAt.getTime()
      : this.running
        ? Date.now()
        : (this.stoppedAt?.getTime() ?? Date.now());
    return Math.max(0, (end - this.startedAt.getTime()) / 1000 - this.heldSeconds);
  }

  /** Whether capture is currently held. */
  get paused() {
    return Boolean(this.pausedAt);
  }

  /** Whether the language model is configured — drives the chat pane's empty state. */
  get llmConfigured() {
    const llm = this.config?.llm;
    if (!llm) return false;
    return llm.mode === "local" ? Boolean(llm.local?.model) : Boolean(llm.api?.model);
  }

  /**
   * Whether anything leaves this machine.
   *
   * True only when both the speech model and the language model are local. Shown in the header
   * because it is what a user needs to confirm before pressing record in a room full of people.
   */
  get fullyLocal() {
    const config = this.config;
    if (!config) return true;
    const remoteAsr = config.asr?.backend === "api";
    const remoteLlm = config.llm?.mode === "api";
    return !remoteAsr && !remoteLlm;
  }
}

/** How long a session has been held, from the server's own count of the audio it has consumed. */
function _heldFrom(state, startedAt, stoppedAt) {
  const recorded = Number(state?.recorded_seconds);
  if (!startedAt || !Number.isFinite(recorded) || recorded <= 0) return 0;
  const end = state?.running ? Date.now() : (stoppedAt?.getTime() ?? Date.now());
  const wall = (end - startedAt.getTime()) / 1000;
  return Math.max(0, wall - recorded);
}

/** When a session ended, as the server reports it. Null while one is running or has never run. */
function _endedAt(session) {
  if (!session?.ended_at) return null;
  const ended = new Date(session.ended_at);
  return Number.isNaN(ended.getTime()) ? null : ended;
}

export const session = new SessionStore();
