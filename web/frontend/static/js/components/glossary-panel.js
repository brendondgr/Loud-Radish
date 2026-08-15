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
import * as prefs from "../core/storage.js";
import { api } from "../transport/api.js";
import { GLOSSARY_ADDED } from "../transport/events.js";

export class GlossaryPanel {
  constructor(root, { toggleButton, countBadge, onSeek } = {}) {
    this.root = root;
    this.toggleButton = toggleButton;
    this.countBadge = countBadge;
    this.onSeek = onSeek;

    this.list = $("[data-glossary-list]", root);
    this.empty = $("[data-glossary-empty]", root);
    /** Terms by casefolded name, so a redefinition replaces rather than duplicates. */
    this.terms = new Map();

    this.toggleButton?.addEventListener("click", () => this.toggle());
    $("[data-glossary-close]", root)?.addEventListener("click", () => this.setOpen(false));

    on(GLOSSARY_ADDED, (term) => this.add(term, { isNew: true }));

    this.setOpen(prefs.get("glossaryOpen"));
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
    setText(this.countBadge, String(terms.length));
    toggle(this.countBadge, true);
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

  toggle() {
    this.setOpen(this.root.hidden);
  }

  setOpen(open) {
    toggle(this.root, open);
    this.toggleButton?.setAttribute("aria-expanded", String(open));
    prefs.set("glossaryOpen", open);
  }
}
