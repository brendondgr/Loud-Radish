/**
 * The Recordings page's one slim bar (D-085). The template holds the way back and the page's
 * heading; this draws the count, search, what to show, and the order — sticky, so it is there however far down the list goes.
 *
 * It replaces a header that took a third of the screen. "Name untitled…" appears only while there
 * are recordings with no title and a model that could name them (D-084).
 */

import { el } from "../core/dom.js";
import { iconNode } from "../core/icons.js";

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
 * @param {object} view - `{ query, filter, sort, shown, total, counts, directory, untitled }`
 * @param {object} hooks - `{ onQuery, onFilter, onSort, onName }`
 */
export function renderToolbar(root, view, hooks) {
  const active = document.activeElement;
  const hadSearch = active?.matches?.("[data-recordings-search]");
  const caret = hadSearch ? active.selectionStart : null;
  const hadFilter = active?.dataset?.filter;

  const count = el("span", {
    className: "rbar__count",
    text: String(view.shown),
    attrs: { title: `${view.shown} shown of ${view.total}${view.directory ? ` · stored in ${view.directory}` : ""}` },
  });

  const search = el("label", {
    className: "rbar__search",
    children: [
      iconNode("search", { size: 13 }),
      el("input", {
        attrs: {
          type: "search",
          placeholder: "Search titles and what was said",
          value: view.query,
          "data-recordings-search": true,
          "aria-label": "Search recordings",
        },
      }),
    ],
  });
  search.querySelector("input").addEventListener("input", (event) => hooks.onQuery(event.target.value));

  const filters = el("div", {
    className: "rbar__filters",
    attrs: { role: "radiogroup", "aria-label": "Show" },
    children: FILTERS.map(([id, label]) => {
      const button = el("button", {
        className: "rbar__filter",
        attrs: {
          type: "button",
          role: "radio",
          "aria-checked": String(view.filter === id),
          "data-filter": id,
        },
        children: [el("span", { text: label }), el("span", { className: "rbar__filter-count", text: String(view.counts[id] ?? 0) })],
      });
      button.addEventListener("click", () => hooks.onFilter(id));
      return button;
    }),
  });

  const sort = el("select", {
    className: "rbar__sort",
    attrs: { "aria-label": "Sort" },
    children: [
      ["newest", "Newest"],
      ["oldest", "Oldest"],
      ["longest", "Longest"],
    ].map(([id, label]) => el("option", { text: label, attrs: { value: id, selected: view.sort === id } })),
  });
  sort.addEventListener("change", () => hooks.onSort(sort.value));

  let name = null;
  if (view.untitled?.configured && view.untitled.count > 0 && !view.untitled.backfill?.running) {
    name = el("button", {
      className: "rbtn rbtn--compact rbar__name",
      attrs: { type: "button", title: `Ask ${view.untitled.model} to title them` },
      children: [iconNode("spark", { size: 13 }), el("span", { text: `Name ${view.untitled.count} untitled…` })],
    });
    name.addEventListener("click", hooks.onName);
  }

  const spacer = el("span", { className: "rbar__spacer" });
  root.replaceChildren(...[count, search, filters, spacer, name, sort].filter(Boolean));

  if (hadSearch) {
    const input = root.querySelector("[data-recordings-search]");
    input.focus();
    if (caret !== null) input.setSelectionRange(caret, caret);
  } else if (hadFilter) {
    root.querySelector(`[data-filter="${hadFilter}"]`)?.focus();
  }
}
