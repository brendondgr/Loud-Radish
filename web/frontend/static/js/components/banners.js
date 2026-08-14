/**
 * Error banners — three tiers, never a modal.
 *
 * Info goes to the status bar and never appears here. Warnings are dismissible and transcription
 * continues. Critical carries an explicit recovery action — but still as a banner, because a modal
 * during a live talk blocks the transcript (FE §6.3).
 */

import { on } from "../core/bus.js";
import { el } from "../core/dom.js";
import { ERROR } from "../transport/events.js";

export class Banners {
  constructor(root, { onRemedy } = {}) {
    this.root = root;
    this.onRemedy = onRemedy;
    /** One banner per code: a repeating condition must not stack up copies of itself. */
    this.shown = new Map();

    on(ERROR, (payload) => this.show(payload));
  }

  show({ code, message, severity = "warning", remedy, remedy_label: remedyLabel }) {
    if (severity === "info") return; // status-bar territory, not a banner
    if (!message) return;

    this.dismiss(code);

    const children = [el("span", { className: "banner__message", text: message })];

    if (remedy && remedyLabel) {
      const action = el("button", {
        className: "banner__action",
        text: remedyLabel,
        attrs: { type: "button" },
      });
      action.addEventListener("click", async () => {
        await this.onRemedy?.(remedy);
        this.dismiss(code);
      });
      children.push(action);
    }

    const dismiss = el("button", {
      className: "banner__dismiss",
      text: "Dismiss",
      attrs: { type: "button", "aria-label": "Dismiss this message" },
    });
    dismiss.addEventListener("click", () => this.dismiss(code));
    children.push(dismiss);

    const banner = el("div", {
      className: "banner",
      attrs: { "data-severity": severity, "data-code": code },
      children,
    });

    this.root.append(banner);
    this.shown.set(code, banner);
  }

  dismiss(code) {
    this.shown.get(code)?.remove();
    this.shown.delete(code);
  }

  clear() {
    for (const code of [...this.shown.keys()]) this.dismiss(code);
  }
}
