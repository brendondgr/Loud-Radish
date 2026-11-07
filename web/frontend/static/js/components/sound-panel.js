/**
 * The sound panel: each sound's volume, mute and meter while recording (D-081).
 *
 * Reads and changes the mix only through `stores/mix.js`, which applies a slider at once and sends
 * it throttled. Rows are rebuilt only when the set of inputs changes; a settings change updates the
 * rows in place, so a slider being dragged is never replaced under the pointer.
 *
 * Shown while a recording is running or held and has a mix — which, since every run goes through
 * the mixer, is every recording. Closing it changes nothing; it is a view of the recording, not a
 * setting.
 */

import { on } from "../core/bus.js";
import { $, setAttr, setText, toggle } from "../core/dom.js";
import { PAUSED, RECORDING } from "../core/modes.js";
import * as prefs from "../core/storage.js";
import { MIX_CHANGED, MIX_LEVELS_CHANGED, mix } from "../stores/mix.js";
import { MODE_CHANGED, mode } from "../stores/mode.js";

/** What each input is called on screen. The first word is capitalised for a row's title. */
const NAMES = {
  window: "The window's sound",
  system: "This computer's sound",
  microphone: "Your microphone",
};

/** A meter reaches full at this RMS — loud speech. Linear below it, so quiet is visibly quiet. */
const METER_FULL_RMS = 0.3;

export class SoundPanel {
  constructor(root) {
    this.root = root;
    if (!root) return;

    this.toggleButton = $("[data-sound-toggle]", root);
    this.popover = $("[data-sound-popover]", root);
    this.list = $("[data-sound-rows]", root);
    this.error = $("[data-sound-error]", root);
    this.template = $("[data-sound-row-template]", root);
    /** name → the row's elements. */
    this.rows = new Map();

    this.toggleButton?.addEventListener("click", () => (this.isOpen ? this.close() : this.open()));
    this.popover?.addEventListener("keydown", (event) => this._onKey(event));
    // A click anywhere else closes it, as a popover does. Captured on the document so a click on
    // another control both closes this and does what it was for.
    document.addEventListener("pointerdown", (event) => {
      if (this.isOpen && !root.contains(event.target)) this.close({ restoreFocus: false });
    });

    on(MIX_CHANGED, () => this.render());
    on(MIX_LEVELS_CHANGED, () => this.renderMeters());
    on(MODE_CHANGED, () => this.render());
    this.render();
  }

  get isOpen() {
    return Boolean(this.popover) && !this.popover.hidden;
  }

  open() {
    if (!this.popover) return;
    this.popover.hidden = false;
    setAttr(this.toggleButton, "aria-expanded", "true");
    $("[data-sound-slider]", this.popover)?.focus();
  }

  close({ restoreFocus = true } = {}) {
    if (!this.popover || this.popover.hidden) return;
    this.popover.hidden = true;
    setAttr(this.toggleButton, "aria-expanded", "false");
    if (restoreFocus) this.toggleButton?.focus();
  }

  // -- drawing -----------------------------------------------------------------------

  render() {
    if (!this.root) return;
    const live = (mode.state === RECORDING || mode.state === PAUSED) && mix.active;
    toggle(this.root, live);
    if (!live) {
      this.close({ restoreFocus: false });
      return;
    }

    const names = mix.inputs.join(",");
    if (names !== [...this.rows.keys()].join(",")) this._buildRows();
    for (const [name, row] of this.rows) this._renderRow(name, row);

    setText(this.error, mix.error ? `Not changed: ${mix.error}` : "");
    if (this.error) this.error.hidden = !mix.error;
    this.renderMeters();
  }

  renderMeters() {
    for (const [name, row] of this.rows) {
      const level = mix.levels[name];
      const fraction = level ? Math.min(1, (level.level ?? 0) / METER_FULL_RMS) : 0;
      row.meter.style.transform = `scaleX(${fraction.toFixed(3)})`;
    }
  }

  _buildRows() {
    this.list.replaceChildren();
    this.rows.clear();
    for (const name of mix.inputs) {
      const node = this.template.content.firstElementChild.cloneNode(true);
      const row = {
        node,
        name: $("[data-sound-name]", node),
        value: $("[data-sound-value]", node),
        mute: $("[data-sound-mute]", node),
        slider: $("[data-sound-slider]", node),
        meter: $("[data-sound-meter]", node),
        status: $("[data-sound-status]", node),
      };
      node.dataset.soundInput = name;
      const label = NAMES[name] ?? name;
      setText(row.name, label);
      setAttr(row.slider, "aria-label", `${label} volume`);

      row.slider.addEventListener("input", () => this._setVolume(name, Number(row.slider.value)));
      row.mute.addEventListener("click", () => this._toggleMute(name));
      this.rows.set(name, row);
      this.list.append(node);
    }
  }

  _renderRow(name, row) {
    const level = mix.levels[name] ?? { volume: 100, muted: false, alive: true };
    const label = NAMES[name] ?? name;
    // Not while it is being dragged: the store already holds the hand's value, and writing it back
    // would fight the pointer for the thumb.
    if (document.activeElement !== row.slider || !row.slider.matches(":active")) {
      row.slider.value = String(level.volume);
    }
    setAttr(row.slider, "aria-valuetext", level.muted ? "muted" : `${level.volume} percent`);
    setText(row.value, level.muted ? "Muted" : `${level.volume} %`);
    setAttr(row.mute, "aria-pressed", String(Boolean(level.muted)));
    setAttr(row.mute, "aria-label", `${level.muted ? "Unmute" : "Mute"} ${label.toLowerCase()}`);
    row.node.classList.toggle("is-muted", Boolean(level.muted));
    row.node.classList.toggle("is-lost", level.alive === false);
    row.slider.disabled = level.alive === false;
    row.mute.disabled = level.alive === false;
    // The one thing a muted row must say plainly: nothing of it is being kept.
    setText(
      row.status,
      level.alive === false
        ? "Stopped — this sound is no longer arriving."
        : level.muted
          ? "Muted — not being recorded."
          : ""
    );
  }

  // -- changing it -------------------------------------------------------------------

  _setVolume(name, volume) {
    mix.change(name, { volume });
    this._remember(name, volume);
  }

  _toggleMute(name) {
    const muted = !mix.levels[name]?.muted;
    mix.change(name, { muted });
  }

  /** The next recording starts this sound where it was left (read by the record sheet). */
  _remember(name, volume) {
    prefs.set("lastMixLevels", { ...(prefs.get("lastMixLevels") ?? {}), [name]: volume });
  }

  _onKey(event) {
    if (event.key === "Escape") {
      event.stopPropagation();
      this.close();
      return;
    }
    // M mutes the row the focus is in — the slider or its button.
    if (event.key === "m" || event.key === "M") {
      const row = event.target.closest?.("[data-sound-row]");
      const name = row?.dataset.soundInput;
      // Not for a sound that has stopped arriving: its button is disabled, and the key must
      // not do what the button cannot.
      const alive = mix.levels[name]?.alive !== false;
      if (name && alive && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        this._toggleMute(name);
      }
    }
  }
}
