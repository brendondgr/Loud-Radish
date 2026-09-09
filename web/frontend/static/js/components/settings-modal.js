/**
 * The settings dialog.
 *
 * This file is orchestration: open, close, switch tabs, trap focus, and hand each tab its own
 * module. It holds no configuration values of its own — the binding layer reads and writes the
 * store, and the store is a cache of what the backend said (FE §8.1).
 *
 * **Why the dialog re-reads on every open.** Configuration can change without this dialog:
 * a preset, a device selection made elsewhere, a value the backend clamped, a change from another
 * browser tab. Rendering from a cache filled minutes ago is how a settings panel comes to disagree
 * with the pipeline it is configuring.
 */

import { emit, on } from "../core/bus.js";
import { $, $$, el, setText } from "../core/dom.js";
import { FocusTrap } from "../a11y/focus-trap.js";
import { CONFIG_CHANGED, config } from "../stores/config.js";
import { session } from "../stores/session.js";
import { AudioSettings } from "./settings/audio.js";
import { AsrSettings } from "./settings/asr.js";
import { LlmSettings } from "./settings/llm.js";
import { ContextSettings, StorageSettings } from "./settings/misc.js";
import { RewritingSettings } from "./settings/rewriting.js";
import { ShortcutSettings } from "./settings/shortcuts.js";
import { attach, hydrate, refreshDependants } from "./settings/bindings.js";

/** Published when the dialog closes, so the header can re-read the assistant's state. */
export const SETTINGS_CLOSED = "settings.closed";

/** How long a footer message stays before clearing itself. */
const STATUS_TIMEOUT_MS = 6000;

export class SettingsModal {
  constructor(root) {
    this.root = root;
    this.dialog = $("[data-settings-dialog]", root);
    this.panels = $("[data-settings-panels]", root);
    this.nav = $("[data-settings-nav]", root);
    this.statusLine = $("[data-settings-status]", root);
    this.sourceLine = $("[data-settings-source]", root);
    this.presetSelect = $("[data-preset-select]", root);
    this.presetHint = $("[data-preset-hint]", root);

    this.trap = new FocusTrap(this.dialog ?? root);
    this.open = false;
    this._statusTimer = null;

    const handlers = {
      onStatus: (message, tone) => this.setStatus(message, tone),
      onChanged: () => this.syncTabs(),
    };
    this.tabs = {
      audio: new AudioSettings(this.panels, handlers),
      asr: new AsrSettings(this.panels, handlers),
      llm: new LlmSettings(this.panels, handlers),
      context: new ContextSettings(this.panels),
      rewriting: new RewritingSettings(this.panels, handlers),
      storage: new StorageSettings(this.panels, handlers),
      shortcuts: new ShortcutSettings(this.panels),
    };

    this._wire();

    // Every write goes through the store, so one subscription keeps every dependent field, note,
    // and cross-tab readout correct without each of them subscribing separately.
    on(CONFIG_CHANGED, () => {
      session.setConfig(config.data);
      if (this.open) this.syncTabs();
    });
  }

  _wire() {
    for (const button of $$("[data-settings-close]", this.root)) {
      button.addEventListener("click", () => this.close());
    }

    for (const tab of $$("[data-settings-tab]", this.root)) {
      tab.addEventListener("click", () => this.show(tab.dataset.settingsTab));
    }
    this.nav?.addEventListener("keydown", (event) => this._onNavKey(event));

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this.open) {
        event.preventDefault();
        this.close();
      }
    });

    this.presetSelect?.addEventListener("change", () => this.applyPreset());
    $("[data-settings-save]", this.root)?.addEventListener("click", () => this.save());

    attach(this.panels, {
      onApplied: (response) => {
        if (response.hot_swap !== "live") this.setStatus(response.consequence);
        else this.setStatus("Saved.", "ok");
      },
      onError: (error) => this.setStatus(error.message, "error"),
      confirm: (consequence) => this._confirmDisruption(consequence),
    });
  }

  /**
   * Ask before a change that would interrupt a live session — and only then.
   *
   * The same change costs nothing when nothing is recording, and a dialog that asks anyway teaches
   * the user to dismiss it without reading, which is worse than not asking.
   */
  _confirmDisruption(consequence) {
    if (!session.running) return true;
    return window.confirm(`${consequence}\n\nApply it now?`);
  }

  // -- opening and closing ---------------------------------------------------------

  /** Open the dialog, optionally on a named tab. */
  async show(tab = null) {
    const wasOpen = this.open;
    if (tab) this._selectTab(tab);

    if (wasOpen) return;
    this.open = true;
    this.root.hidden = false;
    // The page behind must not scroll under the dialog, which on a long transcript is otherwise
    // very easy to do by accident.
    document.body.style.overflow = "hidden";

    this.trap.activate($(`[data-settings-tab="${tab ?? "audio"}"]`, this.root));
    await this.refresh();
  }

  close() {
    if (!this.open) return;
    this.open = false;
    this.root.hidden = true;
    document.body.style.overflow = "";
    this.trap.release();
    emit(SETTINGS_CLOSED, null);
  }

  toggle(tab = null) {
    return this.open ? this.close() : this.show(tab);
  }

  // -- content ---------------------------------------------------------------------

  /** Re-read everything from the backend and re-render. */
  async refresh() {
    try {
      await config.load();
    } catch (error) {
      this.setStatus(error.message, "error");
      return;
    }

    setText(this.sourceLine, "Every setting here is stored by the recorder, not by this browser.");
    hydrate(this.panels);
    this.renderPresets();

    // Tab modules load their own lists concurrently. One failing must not stop the others: a
    // dropdown that could not be filled is a smaller problem than a dialog that renders nothing.
    await Promise.allSettled([
      this.tabs.audio.load(),
      this.tabs.asr.load(),
      this.tabs.llm.load(),
      this.tabs.storage.load(),
      this.tabs.shortcuts.load(),
    ]);
    this.syncTabs();
  }

  /** Re-render everything that follows a value rather than owning one. */
  syncTabs() {
    refreshDependants(this.panels);
    for (const tab of Object.values(this.tabs)) tab.sync?.();
  }

  _selectTab(name) {
    for (const tab of $$("[data-settings-tab]", this.root)) {
      const selected = tab.dataset.settingsTab === name;
      tab.setAttribute("aria-selected", String(selected));
      // Roving tabindex: one stop for the whole tab list, arrow keys to move within it.
      tab.tabIndex = selected ? 0 : -1;
    }
    for (const panel of $$("[data-settings-panel]", this.root)) {
      panel.hidden = panel.dataset.settingsPanel !== name;
    }
  }

  _onNavKey(event) {
    const keys = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 };
    const step = keys[event.key];
    if (!step) return;

    const tabs = $$("[data-settings-tab]", this.nav);
    const index = tabs.indexOf(document.activeElement);
    if (index === -1) return;

    event.preventDefault();
    const next = tabs[(index + step + tabs.length) % tabs.length];
    this.show(next.dataset.settingsTab);
    next.focus();
  }

  // -- presets and persistence ------------------------------------------------------

  renderPresets() {
    if (!this.presetSelect) return;
    this.presetSelect.replaceChildren(
      el("option", { text: "Custom", attrs: { value: "" } }),
      ...config.presets.map((preset) =>
        el("option", { text: this._presetLabel(preset.name), attrs: { value: preset.name } })
      )
    );
    this.presetSelect.value = "";
    setText(this.presetHint, "A profile sets several values at once. Adjust anything afterwards.");
  }

  _presetLabel(name) {
    return name
      .split("-")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ");
  }

  async applyPreset() {
    const name = this.presetSelect.value;
    if (!name) return;

    try {
      const response = await config.applyPreset(name);
      hydrate(this.panels);
      this.syncTabs();
      setText(this.presetHint, config.presets.find((p) => p.name === name)?.description ?? "");
      this.setStatus(
        response.hot_swap === "live" ? "Profile applied." : response.consequence,
        "ok"
      );
    } catch (error) {
      this.setStatus(error.message, "error");
    } finally {
      this.presetSelect.value = "";
    }
  }

  /**
   * Persist to the config file.
   *
   * Changes apply the moment they are made; this is what makes them survive a restart. Both are
   * offered because they are genuinely different intentions — trying something for this talk, and
   * deciding it is how the application should behave.
   */
  async save() {
    try {
      const { path } = await config.save();
      this.setStatus(`Saved to ${path}.`, "ok");
    } catch (error) {
      this.setStatus(error.message, "error");
    }
  }

  setStatus(message, tone = "") {
    setText(this.statusLine, message);
    this.statusLine?.setAttribute("data-tone", tone);

    clearTimeout(this._statusTimer);
    if (!message) return;
    this._statusTimer = setTimeout(() => setText(this.statusLine, ""), STATUS_TIMEOUT_MS);
  }
}
