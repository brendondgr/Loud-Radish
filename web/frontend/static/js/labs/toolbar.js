/**
 * The lab's header (D-080): title, search, filters, sort, layout, verdict, customize.
 *
 * Two rows of controls in the DOM. "One slim bar" lays both rows out as one line, "Two thin rows"
 * keeps them apart, and "Floating dock" moves the whole thing to a pill at the bottom of the
 * window — the same controls each time, so the header styles are compared on space alone.
 */

import { el } from "../core/dom.js";
import { verdictButtons } from "./customizer.js";
import { iconNode } from "./icons.js";
import { LAYOUTS } from "./options.js";
import { lab, review, setSetting } from "./state.js";

export const FILTERS = [
  ["all", "All"],
  ["video", "Video"],
  ["transcript", "Text only"],
  ["needs", "To transcribe"],
  ["empty", "Empty"],
];

/** Which filter a recording belongs to, besides "All". */
export function matchesFilter(session, filter) {
  const media = session.media ?? {};
  const empty = !session.segments && !media.video && !media.audio && !media.audio_file;
  if (filter === "empty") return empty;
  if (empty) return false;
  if (filter === "video") return Boolean(media.video);
  if (filter === "transcript") return !media.video && session.segments > 0;
  if (filter === "needs") return !media.openable && Boolean(media.transcribable);
  return true;
}

/**
 * @param {HTMLElement} root
 * @param {object} view - `{ query, filter, sort, sessions, shown, panelOpen }`
 * @param {object} hooks - `{ onQuery, onFilter, onSort, onTogglePanel }`
 */
export function renderToolbar(root, view, hooks) {
  const active = document.activeElement;
  const hadSearchFocus = active?.matches?.("[data-lab-search]");
  const caret = hadSearchFocus ? active.selectionStart : null;

  const brand = el("a", {
    className: "lt-brand",
    attrs: { href: "/", title: "Back to the main screen" },
    children: [
      el("img", { attrs: { src: root.dataset.logo, alt: "", width: "20", height: "20" } }),
      el("span", { className: "lt-brand__title", text: "Recordings" }),
      el("span", { className: "lt-count", text: String(view.shown), attrs: { title: `${view.shown} shown of ${view.total}` } }),
      el("span", { className: "lt-lab", text: "Lab" }),
    ],
  });

  const search = el("label", {
    className: "lt-search",
    children: [
      iconNode("search", { size: 13 }),
      el("input", {
        attrs: { type: "search", placeholder: "Search titles and what was said", value: view.query, "data-lab-search": true, "aria-label": "Search recordings" },
      }),
    ],
  });
  search.querySelector("input").addEventListener("input", (event) => hooks.onQuery(event.target.value));

  const filters = el("div", {
    className: "lt-filters",
    attrs: { role: "radiogroup", "aria-label": "Show" },
    children: FILTERS.map(([id, label]) => {
      const count = id === "all" ? view.counts.all : view.counts[id];
      const button = el("button", {
        className: "lt-filter",
        attrs: { type: "button", role: "radio", "aria-checked": String(view.filter === id) },
        children: [el("span", { text: label }), el("span", { className: "lt-filter__count", text: String(count ?? 0) })],
      });
      button.addEventListener("click", () => hooks.onFilter(id));
      return button;
    }),
  });

  const sort = el("select", {
    className: "lt-select",
    attrs: { "aria-label": "Sort" },
    children: [
      ["newest", "Newest"],
      ["oldest", "Oldest"],
      ["longest", "Longest"],
    ].map(([id, label]) => el("option", { text: label, attrs: { value: id, selected: view.sort === id } })),
  });
  sort.addEventListener("change", () => hooks.onSort(sort.value));

  const layouts = el("div", {
    className: "lt-layouts",
    attrs: { role: "radiogroup", "aria-label": "Layout" },
    children: LAYOUTS.map((layout, index) => {
      const on = lab.settings.layout === layout.id;
      const button = el("button", {
        className: "lt-layout",
        attrs: { type: "button", role: "radio", "aria-checked": String(on), title: `${layout.label} (${index + 1})`, "aria-label": layout.label },
        children: [iconNode(`layout-${layout.id}`, { size: 15 }), on ? el("span", { text: layout.label }) : null],
      });
      button.addEventListener("click", () => setSetting("layout", layout.id));
      return button;
    }),
  });

  const customize = el("button", {
    className: "lt-customize",
    attrs: { type: "button", "aria-pressed": String(view.panelOpen), title: "Customize (C)" },
    children: [iconNode("sliders", { size: 14 }), el("span", { text: "Customize" })],
  });
  customize.addEventListener("click", hooks.onTogglePanel);

  root.replaceChildren(
    el("div", {
      className: "lt-row lt-row--primary",
      children: [
        brand,
        el("span", { className: "lt-spacer" }),
        layouts,
        el("span", { className: "lt-rate", attrs: { title: "Your verdict on this layout" }, children: [verdictButtons(review, lab.settings.layout)] }),
        customize,
      ],
    }),
    el("div", { className: "lt-row lt-row--secondary", children: [search, filters, sort] }),
  );

  if (hadSearchFocus) {
    const input = root.querySelector("[data-lab-search]");
    input.focus();
    if (caret !== null) input.setSelectionRange(caret, caret);
  }
}
