/**
 * The ⋯ menu on a card (D-082): whatever the card has no room for, one click away.
 *
 * One menu open at a time. It closes on a choice, on Escape, and on a click anywhere else.
 */

import { el } from "../core/dom.js";
import { TRANSCRIPT_FORMATS, transcriptUrl } from "./actions.js";
import { iconNode } from "./icons.js";

let open = null;

export function closeMenus() {
  if (!open) return;
  open.list.hidden = true;
  open.button.setAttribute("aria-expanded", "false");
  open = null;
}

document.addEventListener("click", closeMenus);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && open) {
    const { button } = open;
    closeMenus();
    button.focus();
  }
});

function item(action, { label = action.label, href = action.href, onAction }) {
  const tag = href && !action.disabled ? "a" : "button";
  const node = el(tag, {
    className: `lc-menu__item${action.kind === "danger" ? " lc-menu__item--danger" : ""}`,
    attrs: {
      role: "menuitem",
      type: tag === "button" ? "button" : null,
      href: tag === "a" ? href : null,
      download: tag === "a" ? "" : null,
      "aria-disabled": action.disabled ? "true" : null,
      title: action.disabled || null,
    },
    children: [iconNode(action.icon, { size: 14 }), el("span", { text: label })],
  });
  node.addEventListener("click", (event) => {
    event.stopPropagation();
    if (action.disabled) {
      event.preventDefault();
      return;
    }
    closeMenus();
    if (action.run) onAction(action);
  });
  return node;
}

export function actionMenu(actions, { onAction, session }) {
  const button = el("button", {
    className: "lc-btn lc-btn--icon lc-menu__toggle",
    attrs: { type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": "More", title: "More" },
    children: [iconNode("more", { size: 15 })],
  });
  const list = el("div", { className: "lc-menu", attrs: { role: "menu", hidden: true } });

  for (const action of actions) {
    if (action.id === "transcript") {
      list.append(el("p", { className: "lc-menu__heading", text: "Download transcript" }));
      for (const [fmt, label] of TRANSCRIPT_FORMATS) {
        list.append(item(action, { label, href: transcriptUrl(session.key, fmt), onAction }));
      }
      list.append(el("hr", { className: "lc-menu__rule" }));
      continue;
    }
    if (action.kind === "danger" && list.lastElementChild?.tagName !== "HR") {
      list.append(el("hr", { className: "lc-menu__rule" }));
    }
    list.append(item(action, { onAction }));
  }

  button.addEventListener("click", (event) => {
    event.stopPropagation();
    const wasOpen = open?.list === list;
    closeMenus();
    if (wasOpen) return;
    list.hidden = false;
    button.setAttribute("aria-expanded", "true");
    open = { list, button };
    // Open upward when there is no room below.
    const box = button.getBoundingClientRect();
    list.dataset.up = String(window.innerHeight - box.bottom < list.offsetHeight + 16);
    list.querySelector("a, button")?.focus();
  });
  list.addEventListener("click", (event) => event.stopPropagation());

  return el("span", { className: "lc-menu__wrap", children: [button, list] });
}
