/**
 * The assistant pane.
 *
 * Two things here are worth reading before the code.
 *
 * **A streaming answer is mutated, not re-rendered.** Deltas arrive several times a second for the
 * length of an answer. Rebuilding the message list on each one repaints the whole pane; instead the
 * streaming message keeps a reference to its own node and only that node's text is rewritten.
 *
 * **The pane follows the answer, but stops following the moment the user scrolls.** Reading the
 * first half of an answer while the second half arrives is a normal thing to do, and yanking the
 * view back to the bottom on every delta makes it impossible.
 */

import { on } from "../core/bus.js";
import { $, el, setText, toggle } from "../core/dom.js";
import { renderWithCitations } from "../core/citations.js";
import * as prefs from "../core/storage.js";
import { CHAT_CHANGED, CHAT_STREAM_CHANGED, chat } from "../stores/chat.js";
import { config } from "../stores/config.js";
import { session } from "../stores/session.js";
import { api } from "../transport/api.js";
import { CHAT_DELTA, CHAT_DONE } from "../transport/events.js";

/** Treat the pane as "at the bottom" within this many pixels, so a rounding error is not a scroll. */
const BOTTOM_SLACK_PX = 40;

export class ChatPane {
  constructor(root, { onSeek, onCollapsedChange } = {}) {
    this.root = root;
    this.onSeek = onSeek;
    this.onCollapsedChange = onCollapsedChange;

    this.scroller = $("[data-chat-scroller]", root);
    this.list = $("[data-chat-messages]", root);
    this.empty = $("[data-chat-empty]", root);
    this.emptyNote = $("[data-chat-empty-note]", root);
    this.actions = $("[data-quick-actions]", root);
    this.form = $("[data-chat-form]", root);
    this.input = $("[data-chat-input]", root);
    this.send = $("[data-chat-send]", root);
    this.note = $("[data-chat-note]", root);
    this.modelLabel = $("[data-chat-model]", root);
    this.quoteWrap = $("[data-chat-quote]", root);
    this.quoteText = $("[data-chat-quote-text]", root);
    this.rail = document.querySelector("[data-chat-expand]");

    /** The node the streaming answer is being written into. */
    this.streamNode = null;
    /** The transcript passage carried into the next question, if any. */
    this.quote = null;
    this.following = true;

    this._wire();
    this._applyCollapsed(prefs.get("chatCollapsed"));
    this.render();
  }

  _wire() {
    this.form?.addEventListener("submit", (event) => {
      event.preventDefault();
      this.submit();
    });

    // Enter sends, Shift+Enter is a newline — the convention every chat interface uses. Getting it
    // backwards costs the user a sentence every time.
    this.input?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        this.submit();
      }
    });
    this.input?.addEventListener("input", () => this._autosize());

    $("[data-chat-clear]", this.root)?.addEventListener("click", () => this.clear());
    $("[data-chat-quote-clear]", this.root)?.addEventListener("click", () => this.setQuote(null));
    $("[data-chat-collapse]", this.root)?.addEventListener("click", () => this._applyCollapsed(true));
    this.rail?.addEventListener("click", () => this._applyCollapsed(false));

    this.scroller?.addEventListener("scroll", () => {
      const { scrollTop, scrollHeight, clientHeight } = this.scroller;
      this.following = scrollHeight - (scrollTop + clientHeight) <= BOTTOM_SLACK_PX;
    });

    on(CHAT_DELTA, (payload) => this._onDelta(payload));
    on(CHAT_DONE, (payload) => this._onDone(payload));
    on(CHAT_CHANGED, () => this.render());
    on(CHAT_STREAM_CHANGED, (message) => this._renderStreaming(message));
  }

  /** Load quick actions and any existing conversation. */
  async load() {
    try {
      const { actions } = await api.quickActions();
      this.renderActions(actions);
    } catch {
      /* The composer still works without them. */
    }

    try {
      const { messages } = await api.chatHistory();
      chat.hydrate(messages);
    } catch {
      /* No session yet — the empty state already says so. */
    }

    this.renderIdentity();
  }

  // -- asking ------------------------------------------------------------------------

  async submit() {
    const message = this.input.value.trim();
    if (chat.isStreaming) {
      await this.stop();
      return;
    }
    if (!message && !this.quote) return;

    this.input.value = "";
    this._autosize();
    this._setNote("");

    const quote = this.quote;
    this.setQuote(null);
    chat.ask(quote ? `“${quote.text}” — ${message || "What does this mean?"}` : message);

    try {
      const { request_id: requestId, context_timestamp: at } = await api.chatSend({
        message,
        quote: quote?.text ?? null,
        quote_start: quote?.start ?? null,
      });
      chat.begin(requestId, at);
      this._setBusy(true);
    } catch (error) {
      // The question stays on screen with the reason underneath, rather than vanishing — retyping
      // it is the last thing anyone wants mid-talk.
      this._setNote(error.message, "error");
      this.input.value = message;
      this._autosize();
    }
  }

  async runAction(action) {
    if (chat.isStreaming) return;
    chat.ask(action.label);
    this._setNote("");
    try {
      const { request_id: requestId, context_timestamp: at } = await api.chatSend({
        action: action.id,
      });
      chat.begin(requestId, at);
      this._setBusy(true);
    } catch (error) {
      this._setNote(error.message, "error");
    }
  }

  async stop() {
    try {
      await api.chatCancel();
    } catch {
      /* Already finished. */
    }
  }

  async clear() {
    if (!window.confirm("Clear the conversation? The transcript is not affected.")) return;
    try {
      await api.clearChatHistory();
    } catch {
      /* Nothing stored yet. */
    }
    chat.clear();
  }

  /** Carry a transcript selection into the next question. */
  setQuote(quote) {
    this.quote = quote;
    toggle(this.quoteWrap, Boolean(quote));
    if (quote) {
      setText(this.quoteText, quote.text);
      this.input?.focus();
    }
  }

  // -- events ------------------------------------------------------------------------

  _onDelta({ request_id: requestId, text, reasoning }) {
    chat.delta(requestId, { text, reasoning });
  }

  _onDone({ request_id: requestId, cites, finish_reason: finishReason }) {
    chat.finish(requestId, { cites, finish_reason: finishReason });
    this._setBusy(false);
    this.streamNode = null;

    if (finishReason === "cancelled") this._setNote("Stopped.");
    else if (finishReason === "error") this._setNote("");
  }

  // -- rendering ---------------------------------------------------------------------

  render() {
    const messages = chat.visible;
    toggle(this.empty, messages.length === 0);

    this.list.replaceChildren(...messages.map((message) => this._buildMessage(message)));
    // The streaming node is the last one, and is kept so deltas can rewrite it in place.
    this.streamNode = chat.isStreaming ? this.list.lastElementChild : null;

    this.renderIdentity();
    this._scrollIfFollowing();
  }

  _renderStreaming(message) {
    if (!this.streamNode) {
      this.render();
      return;
    }
    this._fillMessage(this.streamNode, message);
    this._scrollIfFollowing();
  }

  _buildMessage(message) {
    const node = el("div", {
      className: `message message--${message.role}`,
      attrs: { "data-role": message.role },
      children: [
        el("span", {
          className: "message__role",
          text: message.role === "user" ? "You" : "Assistant",
        }),
      ],
    });
    this._fillMessage(node, message);
    return node;
  }

  /** Write a message's body into `node`, replacing whatever was there. */
  _fillMessage(node, message) {
    for (const stale of node.querySelectorAll(".message__text, .message__reasoning")) {
      stale.remove();
    }

    // Reasoning first and collapsed: it arrives before the answer, and a reader who cannot tell
    // the two apart is reading a monologue rather than a reply.
    if (message.reasoning) {
      const details = el("details", { className: "message__reasoning" });
      details.append(
        el("summary", { text: message.text ? "Reasoning" : "Thinking…" }),
        el("p", { className: "message__reasoning-text", text: message.reasoning })
      );
      node.append(details);
    }

    const body = el("p", { className: "message__text" });
    if (message.role === "assistant") {
      renderWithCitations(body, message.text, (seconds) => this.onSeek?.(seconds));
    } else {
      body.textContent = message.text;
    }
    node.append(body);
  }

  renderActions(actions) {
    if (!this.actions) return;
    this.actions.replaceChildren(
      ...actions.map((action) => {
        const button = el("button", {
          className: "quick-action",
          attrs: { type: "button", title: action.hint || action.prompt },
          children: [
            el("span", { text: action.label }),
            action.hint ? el("span", { className: "quick-action__hint", text: action.hint }) : null,
          ],
        });
        button.addEventListener("click", () => this.runAction(action));
        return button;
      })
    );
  }

  renderIdentity() {
    const llm = config.get("llm");
    const model = llm?.mode === "api" ? llm?.api?.model : llm?.local?.model;
    setText(this.modelLabel, model || "");

    // The empty state's second line changes with what is actually missing, so it is never a
    // generic "get started" that does not apply.
    if (!model) {
      setText(
        this.emptyNote,
        "No language model is set up yet. Choose one in Settings → Assistant."
      );
    } else if (!session.running && !session.sessionId) {
      setText(this.emptyNote, "Start recording, and questions about the talk become answerable.");
    } else {
      setText(this.emptyNote, "");
    }
  }

  // -- plumbing ----------------------------------------------------------------------

  _setBusy(busy) {
    setText(this.send, busy ? "Stop" : "Ask");
    this.send?.classList.toggle("button--danger", busy);
    for (const button of this.actions?.children ?? []) button.disabled = busy;
  }

  _setNote(message, tone = "") {
    setText(this.note, message);
    this.note?.setAttribute("data-tone", tone);
  }

  _scrollIfFollowing() {
    if (this.following && this.scroller) this.scroller.scrollTop = this.scroller.scrollHeight;
  }

  /** Grow the composer with its content, up to the CSS max-height. */
  _autosize() {
    if (!this.input) return;
    this.input.style.height = "auto";
    this.input.style.height = `${this.input.scrollHeight}px`;
  }

  _applyCollapsed(collapsed) {
    toggle(this.root, !collapsed);
    toggle(this.rail, collapsed);
    prefs.set("chatCollapsed", collapsed);
    this.onCollapsedChange?.(collapsed);
    if (!collapsed) {
      chat.markRead();
      this._scrollIfFollowing();
    }
  }
}
