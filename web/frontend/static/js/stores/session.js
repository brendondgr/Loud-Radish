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
    this.title = "";
    this.stats = null;
    this.config = null;
  }

  start(payload) {
    this.running = true;
    this.sessionId = payload.session_id ?? null;
    this.startedAt = payload.started_at ? new Date(payload.started_at) : new Date();
    this.config = payload.config ?? this.config;
    emit(SESSION_CHANGED, this);
  }

  stop(payload = {}) {
    this.running = false;
    this.stats = payload.stats ?? this.stats;
    emit(SESSION_CHANGED, this);
  }

  /** Adopt the state the server reported on connect. */
  hydrate(state) {
    const session = state?.session;
    this.running = Boolean(state?.running);
    this.sessionId = session?.session_id ?? null;
    this.startedAt = session?.started_at ? new Date(session.started_at) : null;
    this.title = session?.title ?? "";
    this.stats = state?.stats ?? null;
    emit(SESSION_CHANGED, this);
  }

  /** Seconds since recording began. Zero when idle. */
  get elapsedSeconds() {
    if (!this.startedAt) return 0;
    return Math.max(0, (Date.now() - this.startedAt.getTime()) / 1000);
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
