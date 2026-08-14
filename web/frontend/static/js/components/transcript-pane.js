/**
 * The transcript pane.
 *
 * Renders the committed segment list and, separately, the single hypothesis tail. The separation
 * is structural, not stylistic: the tail is one element that always sits last and is replaced
 * wholly, and it is never inserted into the segment list (FE §4.1).
 *
 * Timestamps appear at intervals rather than on every segment — one per line is a wall of noise
 * (FE §4.2) — and the whole committed region is a polite live region so a screen reader announces
 * new text. **Only committed text is announced**; announcing the constantly-rewritten hypothesis
 * would be unusable (FE §13).
 */

import { on } from "../core/bus.js";
import { $, el, setText, toggle } from "../core/dom.js";
import { pluralise, timestamp, wallClock } from "../core/format.js";
import {
  HYPOTHESIS_CHANGED_TOPIC,
  TRANSCRIPT_CHANGED,
  transcript,
} from "../stores/transcript.js";
import { FOLLOW_CHANGED, ScrollController, UNREAD_CHANGED } from "./scroll-controller.js";

/** Show a timestamp when this many seconds have passed since the last one shown. */
const TIMESTAMP_INTERVAL_S = 30;

/**
 * Keep at most this many segments in the DOM.
 *
 * A 90-minute session produces hundreds of segments and rendering all of them degrades noticeably
 * (FE §4.3). Trimming from the top is the simplest form of virtualisation that preserves scroll
 * position correctly — and because trimming only ever happens while following (pinned to the
 * bottom), it cannot move text the user is reading. Anything older stays in the store and is
 * available over HTTP.
 */
const MAX_RENDERED_SEGMENTS = 300;

export class TranscriptPane {
  constructor(root) {
    this.root = root;
    this.scroller = $(".transcript__scroller", root);
    this.column = $(".transcript__column", root);
    this.list = $(".transcript__segments", root);
    this.emptyState = $(".transcript-empty", root);
    this.hypothesisWrap = $(".hypothesis", root);
    this.hypothesisText = $(".hypothesis__text", root);
    this.hypothesisNote = $(".hypothesis__note", root);
    this.jumpButton = $(".jump-to-live__button", root);
    this.jumpWrap = $(".jump-to-live", root);
    this.jumpCount = $(".jump-to-live__count", root);
    this.countLabel = $("[data-segment-count]", root);

    this.scroll = new ScrollController(this.scroller);
    this.lastTimestampShown = -Infinity;

    this.jumpButton?.addEventListener("click", () => this.scroll.jumpToLive());

    on(TRANSCRIPT_CHANGED, (payload) => this._onTranscriptChanged(payload));
    on(HYPOTHESIS_CHANGED_TOPIC, (payload) => this._renderHypothesis(payload));
    on(FOLLOW_CHANGED, ({ following }) => toggle(this.jumpWrap, !following));
    on(UNREAD_CHANGED, ({ unread }) => this._renderUnread(unread));

    this._renderEmptyState();
  }

  // -- rendering -------------------------------------------------------------------

  _onTranscriptChanged({ segments, batch, added, reset }) {
    if (reset) {
      this.list.replaceChildren();
      this.lastTimestampShown = -Infinity;
      this._renderEmptyState();
      return;
    }

    const incoming = batch ?? (added ? [added] : []);
    if (!incoming.length) return;

    const restore = this.scroll.beginUpdate();
    for (const segment of incoming) {
      this.list.append(this._buildSegment(segment));
    }
    this._trimRendered();
    restore();

    this.scroll.noteArrival(incoming.length);
    this._renderEmptyState();
    setText(this.countLabel, pluralise(segments.length, "segment"));
  }

  _buildSegment(segment) {
    const showTime = segment.start - this.lastTimestampShown >= TIMESTAMP_INTERVAL_S;
    if (showTime) this.lastTimestampShown = segment.start;

    const time = el("span", {
      className: "segment__time numeric",
      text: timestamp(segment.start),
      attrs: {
        // Wall clock on hover, so the user can correlate with their own notes.
        title: wallClock(segment.wall_clock),
        "data-start": segment.start,
      },
    });

    // textContent, never innerHTML: this is model output, and it must never be markup.
    const text = el("p", { className: "segment__text", text: segment.text });

    return el("div", {
      className: `segment${showTime ? " segment--timed" : ""}`,
      attrs: { "data-segment-id": segment.id, "data-start": segment.start },
      children: [time, text],
    });
  }

  /**
   * Replace the tentative tail wholly.
   *
   * Not animated: it updates about once a second for two hours, and any transition becomes visual
   * noise within minutes (FE §4.1).
   */
  _renderHypothesis({ text }) {
    const restore = this.scroll.beginUpdate();
    setText(this.hypothesisText, text);
    toggle(this.hypothesisNote, Boolean(text));
    restore();
  }

  _renderUnread(unread) {
    // "4 new", not "4 news" — the noun is the segments, and it is already implied.
    setText(this.jumpCount, unread ? `${unread} new` : "");
    toggle(this.jumpCount, unread > 0);
  }

  _renderEmptyState() {
    const hasContent = transcript.count > 0;
    toggle(this.emptyState, !hasContent);
    toggle(this.list, hasContent);
  }

  /** Drop the oldest rendered segments once the list grows past the render budget. */
  _trimRendered() {
    const excess = this.list.childElementCount - MAX_RENDERED_SEGMENTS;
    if (excess <= 0) return;
    for (let i = 0; i < excess; i += 1) {
      this.list.firstElementChild?.remove();
    }
  }

  // -- navigation ------------------------------------------------------------------

  /** Scroll to the segment covering a session-absolute time — used by citations and glossary. */
  scrollToTime(seconds) {
    const segment = transcript.segmentAt(seconds);
    if (!segment) return false;
    const node = this.list.querySelector(`[data-segment-id="${segment.id}"]`);
    if (!node) return false;
    this.scroll.scrollTo(node);
    node.classList.add("segment--current-match");
    setTimeout(() => node.classList.remove("segment--current-match"), 2000);
    return true;
  }

  /** Highlight matching segments in place, rather than filtering them out.
   *
   * Filtering would make the transcript jump around under the reader; highlighting keeps its
   * shape, and the live stream keeps accumulating behind it (FE §4.4).
   */
  highlight(segmentIds) {
    const wanted = new Set(segmentIds);
    for (const node of this.list.children) {
      const id = Number(node.dataset.segmentId);
      node.classList.toggle("segment--match", wanted.has(id));
    }
  }

  clearHighlight() {
    for (const node of this.list.children) {
      node.classList.remove("segment--match", "segment--current-match");
    }
  }
}
