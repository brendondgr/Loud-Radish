/**
 * The conversation.
 *
 * A message is either settled or streaming. The streaming one is always the last, is mutated in
 * place as deltas arrive, and settles when `chat.done` lands. That mirrors the transcript's
 * committed/hypothesis split for the same reason: appending each delta as its own entry produces a
 * list of fragments, and re-rendering the whole conversation on every delta is a repaint several
 * times a second for the length of an answer.
 *
 * Reasoning is kept beside the answer, never merged into it. A model that shows its working writes
 * far more of it than of the reply, and a reader given the concatenation is reading a monologue.
 */

import { emit } from "../core/bus.js";

export const CHAT_CHANGED = "store.chat.changed";
export const CHAT_STREAM_CHANGED = "store.chat.stream";

class ChatStore {
  constructor() {
    /** Settled turns, oldest first. */
    this.messages = [];
    /** The answer currently arriving, or null. */
    this.streaming = null;
    /** Messages the user has not seen because the pane is hidden behind a tab. */
    this.unread = 0;
  }

  get isStreaming() {
    return this.streaming !== null;
  }

  /** Replace everything from the server's history. */
  hydrate(messages) {
    this.messages = messages.map((message) => ({
      id: message.id,
      role: message.role,
      text: message.text,
      cites: message.meta?.cites ?? [],
      contextTimestamp: message.context_timestamp ?? null,
      reasoning: "",
    }));
    this.streaming = null;
    emit(CHAT_CHANGED, this);
  }

  /** Append the user's question immediately, before the answer starts. */
  ask(text) {
    this.messages.push({ id: null, role: "user", text, cites: [], reasoning: "" });
    emit(CHAT_CHANGED, this);
  }

  /** Open a streaming answer for `requestId`. */
  begin(requestId, contextTimestamp) {
    this.streaming = {
      requestId,
      role: "assistant",
      text: "",
      reasoning: "",
      cites: [],
      contextTimestamp,
    };
    emit(CHAT_CHANGED, this);
  }

  /**
   * Append a fragment.
   *
   * Ignores a delta for a request that is not the current one. Two answers cannot legitimately
   * overlap, but a late frame from a cancelled request can arrive after the next has started, and
   * appending it would splice one answer into another.
   */
  delta(requestId, { text = "", reasoning = "" }) {
    if (!this.streaming || this.streaming.requestId !== requestId) return;
    if (text) this.streaming.text += text;
    if (reasoning) this.streaming.reasoning += reasoning;
    emit(CHAT_STREAM_CHANGED, this.streaming);
  }

  /** Settle the streaming answer. */
  finish(requestId, { cites = [], finish_reason: finishReason = "" } = {}) {
    if (!this.streaming || this.streaming.requestId !== requestId) return;

    const message = this.streaming;
    this.streaming = null;

    // An answer that produced no text — a failure, or a cancel before the first token — leaves
    // nothing worth keeping in the list. The error banner has already said why.
    if (message.text.trim()) {
      this.messages.push({ ...message, cites, finishReason });
      this.unread += 1;
    }
    emit(CHAT_CHANGED, this);
  }

  /** Everything the pane should render, streaming answer last. */
  get visible() {
    return this.streaming ? [...this.messages, this.streaming] : this.messages;
  }

  clear() {
    this.messages = [];
    this.streaming = null;
    this.unread = 0;
    emit(CHAT_CHANGED, this);
  }

  markRead() {
    if (!this.unread) return;
    this.unread = 0;
    emit(CHAT_CHANGED, this);
  }
}

export const chat = new ChatStore();
