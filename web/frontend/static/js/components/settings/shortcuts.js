/**
 * The Shortcuts tab (Plan 5).
 *
 * Two things beyond what the binding layer already does. **Capturing a key combination** rather
 * than making the user type `Meta+Alt+L`, and **saying plainly when the companion is not running**,
 * because the bindings are then inert — a settings page that stores keys nothing listens for is
 * worse than one that admits it.
 */

import { $, setText } from "../../core/dom.js";
import { api } from "../../transport/api.js";

/** Modifiers in the order KDE writes them, so a captured key matches a hand-written one. */
const MODIFIER_ORDER = ["Meta", "Ctrl", "Alt", "Shift"];

export class ShortcutSettings {
  constructor(root) {
    this.root = root;
    this.status = $("[data-shortcuts-status]", root);

    for (const input of root?.querySelectorAll("[data-shortcut-capture]") ?? []) {
      input.addEventListener("keydown", (event) => this._capture(event, input));
      input.setAttribute("readonly", "");
      input.placeholder = "Press a key combination";
    }
  }

  async load() {
    if (!this.status) return;
    try {
      const health = await api.health();
      this._renderStatus(health);
    } catch {
      setText(this.status, "Could not check whether the shortcuts are registered.");
    }
  }

  sync() {
    /* The binding layer writes every field; there is nothing derived to recompute. */
  }

  _renderStatus(health) {
    // The companion is a separate process, so the honest report is about *it* rather than about
    // the configuration — the keys can be perfectly well set and still do nothing.
    const companion = health?.companion;
    if (companion?.running) {
      setText(
        this.status,
        `${companion.registered ?? 0} of ${companion.total ?? 0} shortcuts are registered.`
      );
      this.status.classList.remove("settings__hint--warning");
      return;
    }

    this.status.classList.add("settings__hint--warning");
    setText(
      this.status,
      "The tray companion is not running, so these keys do nothing yet. Start it with " +
        "uv run scripts/install_autostart.py, or bind the commands by hand in your desktop's " +
        "keyboard settings."
    );
  }

  /**
   * Turn a keypress into the sequence string the desktop expects.
   *
   * Modifiers alone are ignored: every combination begins with one, and recording `Meta` the
   * instant it is pressed would make it impossible to type `Meta+Alt+L` at all.
   */
  _capture(event, input) {
    event.preventDefault();

    const key = event.key;
    if (["Meta", "Control", "Alt", "Shift", "OS"].includes(key)) return;

    if (key === "Escape") {
      input.value = "";
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    const held = {
      Meta: event.metaKey,
      Ctrl: event.ctrlKey,
      Alt: event.altKey,
      Shift: event.shiftKey,
    };
    const parts = MODIFIER_ORDER.filter((name) => held[name]);

    // A bare letter would be caught the moment it was typed anywhere, so a modifier is required.
    if (parts.length === 0) {
      setText(this.status, "Use at least one modifier — Meta, Ctrl, or Alt.");
      return;
    }

    parts.push(key.length === 1 ? key.toUpperCase() : key);
    input.value = parts.join("+");
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
}
