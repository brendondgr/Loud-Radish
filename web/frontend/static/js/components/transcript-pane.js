/**
 * The transcript pane.
 *
 * Renders three regions, in reading order:
 *
 * 1. **Polished blocks** — finished minutes rewritten for reading, one paragraph each, flowing into
 *    one another as continuous prose. Timestamps live inside the text rather than in a heading
 *    above it: a rule and a header line every minute is exactly the constant breaking this region
 *    exists to stop. Empty, and therefore invisible, whenever no language model is available,
 *    which is why the pane below it still has to work on its own.
 * 2. **Committed segments** — the raw tail: the minute currently accumulating, exactly as the
 *    speech model produced it.
 * 3. **The hypothesis** — a single element, replaced wholly, never a list entry (FE §4.1).
 *
 * The separation is structural rather than stylistic. Polished text always covers older material
 * than the raw tail, so two sibling containers keep them in the right order with no interleaving
 * logic — and it lets only the middle one be a live region. A polished block restates what a
 * screen reader has already announced, and announcing a whole minute twice would make the page
 * unusable (FE §13).
 *
 * A segment covered by a block is removed from this region but **not** from the store: the record
 * is intact, and a block that turns out to be wrong costs nothing but its own row.
 *
 * Timestamps appear at intervals rather than on every segment — one per line is a wall of noise
 * (FE §4.2).
 */

import { on } from "../core/bus.js";
import { parseTimestamp } from "../core/citations.js";
import { $, $$, el, setText, toggle } from "../core/dom.js";
import { pluralise, timestamp, wallClock } from "../core/format.js";
import { IDLE, LIVE, PROCESSING, RECORDED, RECORDING, WINDOW } from "../core/modes.js";
import { MODE_CHANGED, mode as modeStore } from "../stores/mode.js";
import { RECORDING_CHANGED, recording } from "../stores/recording.js";
import { POLISH_CHANGED, polish } from "../stores/polish.js";
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

/**
 * Keep at most this many polished blocks in the DOM.
 *
 * One block a minute, so this is four hours — longer than any talk the application is for, and a
 * bound rather than a limit anyone will meet. It exists because the blocks region is not trimmed
 * by the segment budget above and would otherwise grow without one.
 */
const MAX_RENDERED_BLOCKS = 240;

/**
 * What the empty pane says, keyed `mode:state` (D-020).
 *
 * A mode falls back to its own idle copy, and then to live's, so a state with nothing written for
 * it degrades to something true rather than to a blank pane.
 */
const EMPTY_COPY = {
  [`${LIVE}:${IDLE}`]: {
    variant: "idle",
    title: "Nothing recorded yet",
    body:
      "Press <strong>Start recording</strong> and the talk will appear here as it is spoken, a " +
      "paragraph at a time. Text still being decided shows in italics until it settles.",
    hint: "Check your input device first — it is the one check worth doing every time.",
  },
  [`${RECORDED}:${IDLE}`]: {
    variant: "idle",
    title: "Record now, transcribe after",
    body:
      "Press <strong>Start recording</strong> to capture audio without transcribing it. Nothing " +
      "is decoded while you record, so this costs almost nothing to run — the whole recording is " +
      "transcribed in one pass when you stop.",
    hint: "Better accuracy than live, because the model sees the entire talk at once.",
  },
  [`${RECORDED}:${RECORDING}`]: {
    variant: "recording",
    title: "Recording",
    body:
      "Nothing is being transcribed yet. The whole recording is transcribed in one pass when you " +
      "stop, which is what makes this mode cheap to run.",
    hint: "",
  },
  [`${RECORDED}:${PROCESSING}`]: {
    variant: "processing",
    title: "Transcribing the recording",
    body: "The transcript appears here when the pass finishes. It is safe to leave this page.",
    hint: "",
  },
  [`${WINDOW}:${IDLE}`]: {
    variant: "idle",
    title: "Record a window",
    body:
      "Press <strong>Start recording</strong> to choose what to capture, then pick a window. " +
      "Live transcription, a second pass afterwards, and video are each optional.",
    hint: "Audio comes from your current input, not from the window itself.",
  },
  [`${WINDOW}:${RECORDING}`]: {
    variant: "recording",
    title: "Recording a window",
    body:
      "Live transcription is off for this run, so nothing appears here while it records. The " +
      "monitor shows the capture in progress.",
    hint: "",
  },
  [`${WINDOW}:${PROCESSING}`]: {
    variant: "processing",
    title: "Transcribing the recording",
    body: "The transcript appears here when the pass finishes. It is safe to leave this page.",
    hint: "",
  },
};

export class TranscriptPane {
  constructor(root) {
    this.root = root;
    this.scroller = $(".transcript__scroller", root);
    this.column = $(".transcript__column", root);
    this.list = $(".transcript__segments", root);
    this.polishedList = $("[data-transcript-polished]", root);
    this.emptyState = $(".transcript-empty", root);
    this.emptyTitle = $("[data-empty-title]", root);
    this.emptyBody = $("[data-empty-body]", root);
    this.emptyHint = $("[data-empty-hint]", root);
    this.emptyProgress = $("[data-empty-progress]", root);
    this.progressFill = $("[data-progress-fill]", root);
    this.progressLabel = $("[data-progress-label]", root);
    this.hypothesisWrap = $(".hypothesis", root);
    this.hypothesisText = $(".hypothesis__text", root);
    this.hypothesisNote = $(".hypothesis__note", root);
    this.jumpButton = $(".jump-to-live__button", root);
    this.jumpWrap = $(".jump-to-live", root);
    this.jumpCount = $(".jump-to-live__count", root);
    this.countLabel = $("[data-segment-count]", root);
    this.revisionSwitch = $("[data-revision-switch]", root);

    this.scroll = new ScrollController(this.scroller);
    this.lastTimestampShown = -Infinity;

    this.jumpButton?.addEventListener("click", () => this.scroll.jumpToLive());

    for (const button of root?.querySelectorAll("[data-revision]") ?? []) {
      button.addEventListener("click", () => this.showRevision(Number(button.dataset.revision)));
    }

    on(TRANSCRIPT_CHANGED, (payload) => this._onTranscriptChanged(payload));
    on(POLISH_CHANGED, (payload) => this._onPolishChanged(payload));
    on(HYPOTHESIS_CHANGED_TOPIC, (payload) => this._renderHypothesis(payload));
    on(FOLLOW_CHANGED, ({ following }) => toggle(this.jumpWrap, !following));
    on(UNREAD_CHANGED, ({ unread }) => this._renderUnread(unread));
    // The empty copy depends on the mode and run state as well as on whether there is content, so
    // it has to re-render when those change and not only when a segment arrives.
    on(MODE_CHANGED, () => this._renderEmptyState());
    on(RECORDING_CHANGED, () => this._renderEmptyState());

    this._renderEmptyState();
  }

  // -- transcript revisions (D-022) --------------------------------------------------

  /**
   * Offer the Live/Final switch, but only when the session actually holds two passes.
   *
   * A switch between one thing and itself is chrome that explains nothing, and it would appear on
   * every ordinary session — where there is exactly one transcript and nothing to choose.
   */
  setRevisions(available, current) {
    if (!this.revisionSwitch) return;
    this.revisionSwitch.hidden = (available?.length ?? 0) < 2;
    for (const button of this.revisionSwitch.querySelectorAll("[data-revision]")) {
      button.setAttribute("aria-pressed", String(Number(button.dataset.revision) === current));
    }
  }

  /** Replace the transcript with one pass's segments. Set by the entry point. */
  async showRevision(revision) {
    await this.onRevisionChange?.(revision);
  }

  // -- rendering -------------------------------------------------------------------

  _onTranscriptChanged({ segments, batch, added, reset }) {
    if (reset) {
      this.list.replaceChildren();
      this.polishedList?.replaceChildren();
      this.lastTimestampShown = -Infinity;
      this._renderEmptyState();
      return;
    }

    // A segment already covered by a polished block is not rendered at all. This is what makes the
    // two regions independent of arrival order: on a page reload the blocks and the segments are
    // fetched separately, and either can land first without duplicating a minute of text.
    const incoming = (batch ?? (added ? [added] : [])).filter(
      (segment) => !polish.coversSegment(segment.id)
    );
    setText(this.countLabel, pluralise(segments.length, "segment"));
    if (!incoming.length) return;

    const restore = this.scroll.beginUpdate();
    for (const segment of incoming) {
      this.list.append(this._buildSegment(segment));
    }
    this._trimRendered();
    restore();

    this.scroll.noteArrival(incoming.length);
    this._renderEmptyState();
  }

  /**
   * A finished minute has been rewritten. Show it, and drop the raw segments it replaces.
   *
   * Dropped from the DOM only — the store keeps every segment, so search, citation navigation, and
   * export are unaffected by whether a stretch happens to have been polished.
   */
  _onPolishChanged({ batch, added, reset }) {
    if (!this.polishedList) return;
    if (reset) {
      this.polishedList.replaceChildren();
      return;
    }

    const incoming = batch ?? (added ? [added] : []);
    if (!incoming.length) return;

    const restore = this.scroll.beginUpdate();
    for (const block of incoming) {
      this._insertBlock(block);
      for (const id of block.source_ids ?? []) {
        this.list.querySelector(`[data-segment-id="${id}"]`)?.remove();
      }
    }
    this._trimBlocks();
    restore();
    this._renderEmptyState();
  }

  /** Insert a block in time order, which the replay can deliver out of. */
  _insertBlock(block) {
    const node = this._buildBlock(block);
    const later = [...this.polishedList.children].find(
      (child) => Number(child.dataset.start) > block.start
    );
    this.polishedList.insertBefore(node, later ?? null);
  }

  /**
   * One block. No timestamp header above it: the times are inside the prose now, and a heading
   * line over every minute is one of the breaks this pane exists to stop making.
   */
  _buildBlock(block) {
    return el("article", {
      className: "polished",
      attrs: {
        "data-block-id": block.id,
        "data-start": block.start,
        "data-end": block.end,
        // Comma-separated rather than an array, because search highlighting matches against it and
        // a dataset value is a string either way.
        "data-segment-ids": (block.source_ids ?? []).join(","),
      },
      children: renderProse(block.text),
    });
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
    if (!hasContent) this._renderEmptyCopy();
  }

  /**
   * Say the right thing for the mode and run state (D-020).
   *
   * `recorded` mode produces no transcript at all while it records — that is what makes it cheap —
   * and an empty pane offering to start a recording that is already running is worse than useless.
   * Each mode explains its own silence.
   */
  _renderEmptyCopy() {
    const key = `${modeStore.mode}:${modeStore.state}`;
    const copy =
      EMPTY_COPY[key] ?? EMPTY_COPY[`${modeStore.mode}:${IDLE}`] ?? EMPTY_COPY[`${LIVE}:${IDLE}`];

    this.emptyState?.setAttribute("data-variant", copy.variant);
    setText(this.emptyTitle, copy.title);
    // `innerHTML` because the idle copy carries a <strong>. The strings are literals in this
    // module, never anything a user or a model produced.
    if (this.emptyBody) this.emptyBody.innerHTML = copy.body;
    setText(this.emptyHint, copy.hint ?? "");
    toggle(this.emptyHint, Boolean(copy.hint));

    // The progress bar belongs to the pass, not to the mode: it shows whenever one is running,
    // whichever mode produced the recording.
    const transcribing = modeStore.state === PROCESSING && recording.totalSeconds > 0;
    toggle(this.emptyProgress, transcribing);
    if (transcribing) {
      if (this.progressFill) this.progressFill.style.width = `${recording.percent}%`;
      setText(
        this.progressLabel,
        `Transcribed ${timestamp(recording.transcribedSeconds)} of ` +
          `${timestamp(recording.totalSeconds)} — ${recording.percent}%`
      );
    }
  }

  /** Drop the oldest rendered segments once the list grows past the render budget. */
  _trimRendered() {
    const excess = this.list.childElementCount - MAX_RENDERED_SEGMENTS;
    if (excess <= 0) return;
    for (let i = 0; i < excess; i += 1) {
      this.list.firstElementChild?.remove();
    }
  }

  _trimBlocks() {
    const excess = this.polishedList.childElementCount - MAX_RENDERED_BLOCKS;
    for (let i = 0; i < excess; i += 1) {
      this.polishedList.firstElementChild?.remove();
    }
  }

  // -- navigation ------------------------------------------------------------------

  /** Scroll to the moment a citation or glossary term points at.
   *
   * Tries the raw segment first and falls back to the polished block covering that time, because
   * whether a given moment is still on screen as a segment depends on whether its minute has been
   * polished yet — and a citation that silently does nothing is worse than an approximate one.
   */
  scrollToTime(seconds) {
    const segment = transcript.segmentAt(seconds);
    const node =
      (segment && this.list.querySelector(`[data-segment-id="${segment.id}"]`)) ||
      this._blockNodeAt(seconds);
    if (!node) return false;

    this.scroll.scrollTo(node);
    node.classList.add("segment--current-match");
    setTimeout(() => node.classList.remove("segment--current-match"), 2000);
    return true;
  }

  _blockNodeAt(seconds) {
    const block = polish.blockAt(seconds);
    if (!block) return null;
    return this.polishedList?.querySelector(`[data-block-id="${block.id}"]`) ?? null;
  }

  /** Highlight matching segments in place, rather than filtering them out.
   *
   * Filtering would make the transcript jump around under the reader; highlighting keeps its
   * shape, and the live stream keeps accumulating behind it (FE §4.4).
   */
  highlight(segmentIds) {
    const wanted = new Set(segmentIds);
    for (const node of this.list.children) {
      node.classList.toggle("segment--match", wanted.has(Number(node.dataset.segmentId)));
    }

    // Search runs against the segments, which are what the full-text index holds. A block is a
    // match when any segment it was built from is — otherwise searching a polished session would
    // find nothing, since the matching segments are no longer on screen.
    for (const node of this.polishedList?.children ?? []) {
      const covered = (node.dataset.segmentIds || "").split(",").filter(Boolean).map(Number);
      node.classList.toggle("segment--match", covered.some((id) => wanted.has(id)));
    }
  }

  clearHighlight() {
    for (const node of $$(".segment--match, .segment--current-match", this.root)) {
      node.classList.remove("segment--match", "segment--current-match");
    }
  }
}

/** `[MM:SS]` and `[H:MM:SS]`, the markers the polish pass carries through the prose. */
const INLINE_TIME = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g;

/**
 * Turn polished text into paragraphs and lists.
 *
 * The only two structures the prompt permits, and the backend has already stripped everything
 * else. Built as elements rather than parsed as markup: every string here is model output, so it
 * goes through `textContent` and can never become HTML no matter what the model wrote.
 */
function renderProse(text) {
  return (text ?? "")
    .split(/\n{2,}/)
    .map((chunk) => chunk.trim())
    .filter(Boolean)
    .map((chunk) => {
      const lines = chunk.split("\n").map((line) => line.trim());
      const items = lines.filter((line) => line.startsWith("- "));

      // A block is a list only if it is entirely a list. A stray dash inside a paragraph — which a
      // speaker's aside routinely produces — must not fragment the paragraph into bullets.
      if (items.length === lines.length) {
        return el("ul", {
          className: "polished__list",
          children: items.map((line) => el("li", { children: withTimes(line.slice(2).trim()) })),
        });
      }
      return el("p", { className: "polished__paragraph", children: withTimes(lines.join(" ")) });
    });
}

/**
 * Split a run of prose into text and the timestamps carried through it.
 *
 * The times are **not** buttons, unlike the identical-looking citations in a chat answer. A
 * citation points somewhere else and needs a click to get there; a timestamp sitting in the
 * transcript is already at the moment it names, so a control that seeks to itself would be an
 * affordance that does nothing. They are here to be read, and to be exported and searched with the
 * text — that is what makes a polished passage traceable back to when it was said.
 */
function withTimes(text) {
  const nodes = [];
  let cursor = 0;

  for (const match of text.matchAll(INLINE_TIME)) {
    if (match.index > cursor) {
      nodes.push(document.createTextNode(text.slice(cursor, match.index)));
    }
    nodes.push(
      el("span", {
        className: "polished__at numeric",
        text: match[1],
        attrs: { "data-start": parseTimestamp(match[1]) ?? 0 },
      })
    );
    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) nodes.push(document.createTextNode(text.slice(cursor)));
  return nodes;
}
