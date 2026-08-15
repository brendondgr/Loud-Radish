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
    this.stats = payload.stats ?? this.stats;
    emit(SESSION_CHANGED, this);
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
    this.stoppedAt = this.running ? null : this.stoppedAt;
    this.title = session?.title ?? "";
    this.mode = session?.mode ?? this.mode;
    this.stats = state?.stats ?? null;
    emit(SESSION_CHANGED, this);
  }

  /** Seconds the session ran for — still climbing while recording, frozen once it stops. */
  get elapsedSeconds() {
    if (!this.startedAt) return 0;
    const end = this.running ? Date.now() : (this.stoppedAt?.getTime() ?? Date.now());
    return Math.max(0, (end - this.startedAt.getTime()) / 1000);
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

export const session = new SessionStore();
