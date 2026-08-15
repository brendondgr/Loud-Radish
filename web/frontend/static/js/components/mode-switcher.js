/**
 * The capture-mode selector (D-020).
 *
 * Its own component rather than part of `Header`, so the header stays a renderer and this stays a
 * controller — the ownership rule `docs/component-map.md` already states.
 *
 * It is a `radiogroup` of three rather than a `<select>`: three options that change what the
 * primary control does deserve to be visible without opening anything. That choice brings the
 * radiogroup keyboard contract with it — arrows move between options, and the group holds one tab
 * stop, not three.
 */

import { on } from "../core/bus.js";
import { setAttr } from "../core/dom.js";
import { CAPTURE_MODES } from "../core/modes.js";
import { MODE_CHANGED, mode as modeStore } from "../stores/mode.js";

const LABELS = {
  live: "Live",
  recorded: "Recorded",
  window: "Window",
};

const DESCRIPTIONS = {
  live: "Transcribe continuously while recording",
  recorded: "Record now, transcribe the whole thing when you stop",
  window: "Record a window, with transcription and video optional",
};

export class ModeSwitcher {
  constructor(root, { onChange } = {}) {
    this.root = root;
    this.onChange = onChange;
    if (!root) return;

    this.buttons = new Map();
    for (const button of root.querySelectorAll("[data-mode-option]")) {
      this.buttons.set(button.dataset.modeOption, button);
      button.addEventListener("click", () => this._select(button.dataset.modeOption));
    }

    root.addEventListener("keydown", (event) => this._onKeyDown(event));
    on(MODE_CHANGED, () => this.render());
    this.render();
  }

  render() {
    if (!this.root) return;

    const busy = !modeStore.canSelectMode;
    // The group is described as disabled as a whole *and* each option is disabled individually.
    // `aria-disabled` alone still lets a click through; `disabled` alone drops the buttons out of
    // the accessibility tree mid-recording, so the user loses the answer to "which mode is this?".
    setAttr(this.root, "aria-disabled", String(busy));

    for (const entry of modeStore.selectable) {
      const button = this.buttons.get(entry.mode);
      if (!button) continue;

      const blocked = busy || !entry.available;
      button.setAttribute("aria-checked", String(entry.selected));
      button.disabled = blocked && !entry.selected;
      // One tab stop for the group: only the checked option is reachable by Tab, and arrows move
      // within. This is the radiogroup contract, and getting it wrong makes the header three tab
      // stops deeper for no gain.
      button.tabIndex = entry.selected ? 0 : -1;
      button.title = entry.available
        ? DESCRIPTIONS[entry.mode] || LABELS[entry.mode]
        : entry.reason || `${LABELS[entry.mode]} is not available on this machine`;
      button.classList.toggle("mode-switcher__option--unavailable", !entry.available);
    }
  }

  _select(name) {
    if (!modeStore.select(name)) return;
    this.buttons.get(name)?.focus();
    this.onChange?.(name);
  }

  /** Arrows move between options; Home and End jump to the ends. Space and Enter are the click. */
  _onKeyDown(event) {
    const offsets = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const candidates = event.key === "Home" ? CAPTURE_MODES : [...CAPTURE_MODES].reverse();
      const target = candidates.find((name) => modeStore.isAvailable(name));
      if (target) this._select(target);
      return;
    }

    const step = offsets[event.key];
    if (!step) return;
    event.preventDefault();

    // Skip past unavailable options rather than landing on one and refusing — an arrow key that
    // appears to do nothing reads as a broken control.
    const count = CAPTURE_MODES.length;
    const start = CAPTURE_MODES.indexOf(modeStore.mode);
    for (let hop = 1; hop <= count; hop += 1) {
      const candidate = CAPTURE_MODES[(((start + step * hop) % count) + count) % count];
      if (modeStore.isAvailable(candidate)) {
        this._select(candidate);
        return;
      }
    }
  }
}
