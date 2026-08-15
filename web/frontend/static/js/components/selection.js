/**
 * "Ask about this" — selecting a passage in the transcript and asking about it.
 *
 * The highest-value interaction in the application (FE §4.5): the moment a listener is confused is
 * the moment they are looking at the confusing sentence, and making them retype it is enough
 * friction that they will not bother.
 *
 * The menu appears on `selectionchange` rather than `mouseup`, so it works for a keyboard selection
 * and for a touch selection handle as well as for a drag.
 */

import { $, setText, toggle } from "../core/dom.js";
import { timestamp } from "../core/format.js";

/** Ignore a selection shorter than this — usually a stray click, never a question. */
const MIN_SELECTION_CHARS = 3;

/** Keep the menu inside the pane by this margin. */
const EDGE_MARGIN_PX = 8;

export class TranscriptSelection {
  constructor(root, { onAsk } = {}) {
    this.root = root;
    this.onAsk = onAsk;

    this.menu = $("[data-selection-menu]", root);
    this.scroller = $(".transcript__scroller", root);
    this.current = null;

    $("[data-ask-about-selection]", root)?.addEventListener("click", () => this._ask());
    $("[data-copy-selection]", root)?.addEventListener("click", () => this._copy());

    document.addEventListener("selectionchange", () => this._onSelectionChange());

    // Scrolling moves the text out from under the menu, which would leave it pointing at nothing.
    this.scroller?.addEventListener("scroll", () => this._hide(), { passive: true });
  }

  _onSelectionChange() {
    const selection = document.getSelection();
    const text = selectedText(selection);

    if (
      !selection ||
      selection.isCollapsed ||
      text.length < MIN_SELECTION_CHARS ||
      !this.scroller?.contains(selection.anchorNode)
    ) {
      this._hide();
      return;
    }

    this.current = { text, start: this._startOf(selection) };
    this._show(selection);
  }

  /**
   * The session time the selection begins at.
   *
   * Read from the enclosing segment's `data-start` rather than interpolated within it: the segment
   * is the unit the transcript stores times for, and a guess at a word's offset inside it would be
   * a citation that lands in the wrong place.
   */
  _startOf(selection) {
    let node = selection.anchorNode;
    while (node && node !== this.scroller) {
      if (node.nodeType === Node.ELEMENT_NODE && node.dataset?.start !== undefined) {
        return Number(node.dataset.start);
      }
      node = node.parentNode;
    }
    return null;
  }

  _show(selection) {
    if (!this.menu) return;
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    const wrap = this.menu.offsetParent ?? this.root;
    const bounds = wrap.getBoundingClientRect();

    this.menu.hidden = false;

    // Positioned after unhiding, because a hidden element has no measurable size and the clamp
    // below would push it off the edge it is meant to stay inside.
    const left = Math.max(
      EDGE_MARGIN_PX,
      Math.min(
        rect.left - bounds.left + rect.width / 2 - this.menu.offsetWidth / 2,
        bounds.width - this.menu.offsetWidth - EDGE_MARGIN_PX
      )
    );
    const above = rect.top - bounds.top - this.menu.offsetHeight - EDGE_MARGIN_PX;

    this.menu.style.left = `${left}px`;
    // Below the selection when there is no room above it, rather than off the top of the pane.
    this.menu.style.top = `${above > 0 ? above : rect.bottom - bounds.top + EDGE_MARGIN_PX}px`;
  }

  _hide() {
    toggle(this.menu, false);
  }

  _ask() {
    if (this.current) this.onAsk?.(this.current);
    this._hide();
    document.getSelection()?.removeAllRanges();
  }

  /**
   * Copy with the timestamp attached.
   *
   * A quote pasted into notes without one is unverifiable later, which is the whole reason the
   * transcript carries times.
   */
  async _copy() {
    if (!this.current) return;
    const { text, start } = this.current;
    const payload = start === null ? text : `[${timestamp(start)}] ${text}`;
    try {
      await navigator.clipboard.writeText(payload);
    } catch {
      // Clipboard access can be refused. Nothing here is worth an error banner.
    }
    this._hide();
  }
}

/**
 * The selected text.
 *
 * `Selection.toString()` is the standard way and works for anything a user selects by hand. It
 * returns an empty string for a *programmatically* created range in some engines, though, so the
 * range itself is the fallback — equivalent for a single-range selection, which is all this handles.
 */
export function selectedText(selection) {
  if (!selection || selection.isCollapsed) return "";
  const direct = selection.toString().trim();
  if (direct) return direct;
  return selection.rangeCount ? selection.getRangeAt(0).toString().trim() : "";
}

/** Fill an element with the selected text, for the composer's quote chip. */
export function describeQuote(node, quote) {
  setText(node, quote?.text ?? "");
}
