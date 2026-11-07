/**
 * The glossary panel.
 *
 * Terms the speaker introduced, defined as they used them. Each one is a button rather than a
 * static entry: the question a reader has about a term is almost always "when did they say that?",
 * and clicking it scrolls the transcript to where it was introduced.
 */

import { on } from "../core/bus.js";
import { $, el, setText, toggle } from "../core/dom.js";
import { timestamp } from "../core/format.js";
import { api } from "../transport/api.js";
import { GLOSSARY_ADDED } from "../transport/events.js";

export class GlossaryPanel {
  /**
   * `toggleButtons` and `countBadges` are lists: the transcript's toolbar and the assistant's
   * header each have a Glossary button with its count, and the section its own. Given one element
   * each, only the first button worked and only the first badge counted (found in redesign Step 6).
   *
   * The glossary is a section of the details column (D-086), always there; a button scrolls to it
   * rather than opening it. `onReveal` is called first, so a narrow screen can show the tab.
   */
  constructor(root, { toggleButtons = [], countBadges = [], onSeek, onReveal } = {}) {
    this.root = root;
    this.toggleButtons = [...toggleButtons];
    this.countBadges = [...countBadges];
    this.onSeek = onSeek;
    this.onReveal = onReveal;

    this.list = $("[data-glossary-list]", root);
    this.empty = $("[data-glossary-empty]", root);
    /** Terms by casefolded name, so a redefinition replaces rather than duplicates. */
    this.terms = new Map();

    for (const button of this.toggleButtons) button.addEventListener("click", () => this.reveal());

    on(GLOSSARY_ADDED, (term) => this.add(term, { isNew: true }));
  }

  /** Fetch the terms already collected this session. */
  async load() {
    try {
      const { terms } = await api.glossary();
      for (const term of terms) this.add(term);
    } catch {
      // No session yet. The empty state already explains that terms appear as they are introduced.
    }
  }

  /** Forget every term — the recording they came from is no longer the one on screen (D-076). */
  clear() {
    this.terms.clear();
    this.render();
  }

  add(term, { isNew = false } = {}) {
    this.terms.set(term.term.toLowerCase(), term);
    this.render();

    if (!isNew) return;
    // Marked briefly rather than permanently: "new" stops meaning anything on a panel that has
    // been open for two hours.
    const node = this.list.querySelector(`[data-term="${CSS.escape(term.term.toLowerCase())}"]`);
    node?.classList.add("glossary-term--new");
    setTimeout(() => node?.classList.remove("glossary-term--new"), 4000);
  }

  render() {
    const terms = [...this.terms.values()].sort((a, b) => a.first_seen - b.first_seen);

    this.list.replaceChildren(...terms.map((term) => this._build(term)));
    toggle(this.empty, terms.length === 0);
    for (const badge of this.countBadges) {
      setText(badge, String(terms.length));
      toggle(badge, true);
    }
  }

  _build(term) {
    const button = el("button", {
      className: "glossary-term",
      attrs: { type: "button", "data-term": term.term.toLowerCase() },
      children: [
        el("span", { className: "glossary-term__name", text: term.term }),
        el("span", { className: "glossary-term__definition", text: term.definition }),
        el("span", {
          className: "glossary-term__time numeric",
          text: `first used at ${timestamp(term.first_seen)}`,
        }),
      ],
    });
    button.addEventListener("click", () => this.onSeek?.(term.first_seen));
    return button;
  }

  /** Bring the glossary into view and say it is the thing that was asked for. */
  reveal() {
    this.onReveal?.();
    // A jump, not a glide: motion is for state changes only, and the outline below already says
    // where the eye should land (design-system.md, Motion).
    this.root.scrollIntoView({ block: "start" });
    this.root.focus({ preventScroll: true });
    this.root.setAttribute("data-revealed", "");
    clearTimeout(this.revealTimer);
    this.revealTimer = setTimeout(() => this.root.removeAttribute("data-revealed"), 1600);
  }
}
