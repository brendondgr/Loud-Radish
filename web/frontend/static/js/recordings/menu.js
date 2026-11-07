/**
 * A card's ⋯ menu (D-085): whatever the card has no room for, one click away.
 *
 * One menu open at a time. It closes on a choice, on Escape (returning focus to its button), and
 * on a click anywhere else. Arrow keys move between items.
 */

import { el } from "../core/dom.js";
import { iconNode } from "../core/icons.js";
import { TRANSCRIPT_FORMATS, transcriptUrl } from "./actions.js";

let open = null;

export function closeMenus() {
  if (!open) return;
  open.list.hidden = true;
  open.button.setAttribute("aria-expanded", "false");
  open = null;
}

/** Whether a menu is open — so Escape closes it before it closes a window. */
export const menuOpen = () => Boolean(open);

document.addEventListener("click", closeMenus);
document.addEventListener("keydown", (event) => {
  if (!open) return;
  if (event.key === "Escape") {
    event.stopPropagation();
    const { button } = open;
    closeMenus();
    button.focus();
    return;
  }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const items = [...open.list.querySelectorAll("[role=menuitem]")];
    const at = items.indexOf(document.activeElement);
    const next = event.key === "ArrowDown" ? at + 1 : at - 1;
    items[(next + items.length) % items.length]?.focus();
  }
});

function item(action, { label = action.label, href = action.href, onAction }) {
  const tag = href && !action.disabled ? "a" : "button";
  const node = el(tag, {
    className: `rmenu__item${action.kind === "danger" ? " rmenu__item--danger" : ""}`,
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
    className: "rbtn rbtn--icon rmenu__toggle",
    attrs: {
      type: "button",
      "aria-haspopup": "menu",
      "aria-expanded": "false",
      "aria-label": "More actions",
      title: "More",
    },
    children: [iconNode("more", { size: 15 })],
  });
  const list = el("div", { className: "rmenu", attrs: { role: "menu", hidden: true } });

  for (const action of actions) {
    if (action.id === "transcript") {
      list.append(el("p", { className: "rmenu__heading", text: "Download transcript" }));
      for (const [fmt, label] of TRANSCRIPT_FORMATS) {
        list.append(item(action, { label, href: transcriptUrl(session.key, fmt), onAction }));
      }
      list.append(el("hr", { className: "rmenu__rule" }));
      continue;
    }
    if (action.kind === "danger" && list.lastElementChild?.tagName !== "HR") {
      list.append(el("hr", { className: "rmenu__rule" }));
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
    // Upward when there is no room below.
    const box = button.getBoundingClientRect();
    list.dataset.up = String(window.innerHeight - box.bottom < list.offsetHeight + 16);
    list.querySelector("[role=menuitem]")?.focus({ preventScroll: true });
  });
  list.addEventListener("click", (event) => event.stopPropagation());

  return el("span", { className: "rmenu__wrap", children: [button, list] });
}
