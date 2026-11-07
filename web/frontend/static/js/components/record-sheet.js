/**
 * The record sheet (D-075). Every recording starts here.
 *
 * Asks what to record, from which input, when to transcribe it, and — for a window — whether to
 * keep video. Resolves to the options a start request carries, or to `null` if the user cancelled.
 * The caller starts the session; this component never does, which keeps "what was chosen" separate
 * from "what happens next". The server derives the mode from these answers.
 *
 * `show()` returns a promise deliberately. Starting is a sequence — ask, then start, then perhaps
 * let the desktop pick a window — and a promise reads as a sequence at the call site.
 *
 * The sheet opens pre-filled from the last run. D-020 made window options per-run so that nobody
 * silently got no video; that fault came from options applied without being shown, and every
 * choice here is on screen at the moment of confirming.
 */

import { FocusTrap } from "../a11y/focus-trap.js";
import { $, $$, el, setText, toggle } from "../core/dom.js";
import { WINDOW } from "../core/modes.js";
import * as prefs from "../core/storage.js";
import { config } from "../stores/config.js";
import { mode as modeStore } from "../stores/mode.js";
import { api } from "../transport/api.js";

/** What a first-ever recording is offered: the original behaviour, live from the microphone. */
const DEFAULTS = Object.freeze({
  source: "microphone",
  timing: "live",
  // The window's own sound, not the person watching it. A window recording that captures the
  // viewer's microphone is the fault this default exists to prevent.
  audio_source: "system",
  video: true,
});

const TIMINGS = {
  live: { live_transcription: true, post_transcription: false },
  after: { live_transcription: false, post_transcription: true },
  both: { live_transcription: true, post_transcription: true },
  none: { live_transcription: false, post_transcription: false },
};

export class RecordSheet {
  constructor(root, { onOpenSettings } = {}) {
    this.root = root;
    if (!root) return;

    this.dialog = $("[data-record-dialog]", root);
    this.confirm = $("[data-record-confirm]", root);
    this.refusal = $("[data-record-refusal]", root);
    this.sources = $$("[data-record-source]", root);
    this.timings = $$("[data-record-timing]", root);
    this.audios = $$("[data-record-audio]", root);
    this.video = $("[data-record-video]", root);
    this.device = $("[data-record-device]", root);
    this.deviceNote = $("[data-record-device-note]", root);
    this.deviceStep = $("[data-record-device-step]", root);
    this.fileNote = $("[data-record-file-note]", root);
    this.fileName = $("[data-record-file-name]", root);
    this.timingNone = $("[data-record-timing-none]", root);
    this.timingStep = $("[data-record-timing-step]", root);
    this.windowReason = $("[data-record-window-reason]", root);
    this.sections = new Map($$("[data-record-section]", root).map((n) => [n.dataset.recordSection, n]));

    for (const input of [...this.sources, ...this.timings, ...this.audios, this.video]) {
      input?.addEventListener("change", () => this._render());
    }

    this.trap = new FocusTrap(this.dialog ?? root);
    this._resolve = null;

    for (const button of $$("[data-record-cancel]", root)) {
      button.addEventListener("click", () => this._close(null));
    }
    this.confirm?.addEventListener("click", () => this._submit());

    $("[data-record-settings]", root)?.addEventListener("click", () => {
      // Cancels the run rather than stacking a second dialog over this one: changing the input is
      // something you go and do, and a half-filled sheet behind a modal is worse than reopening.
      this._close(null);
      onOpenSettings?.("audio");
    });

    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        this._close(null);
      }
    });
  }

  /** Whether the sheet is open, so a caller does not stack two. */
  get isOpen() {
    return Boolean(this.root) && !this.root.hidden;
  }

  /**
   * Open the sheet. Resolves to the start request's `options`, or `null` if cancelled.
   *
   * `preset.source` preselects a source — a global shortcut for "record a window" opens the sheet
   * with a window already chosen.
   */
  show(preset = {}) {
    if (!this.root) return Promise.resolve(this._toOptions(DEFAULTS));

    const remembered = { ...DEFAULTS, ...(prefs.get("lastRecordOptions") ?? {}), ...preset };
    // A remembered source this machine can no longer use falls back to the microphone rather than
    // opening on a disabled choice.
    if (remembered.source === WINDOW && !modeStore.isAvailable(WINDOW)) {
      remembered.source = DEFAULTS.source;
    }
    this._check(this.sources, remembered.source);
    this._check(this.timings, remembered.timing);
    this._check(this.audios, remembered.audio_source);
    if (this.video) this.video.checked = Boolean(remembered.video);

    this._renderAvailability();
    this._render();
    void this._loadDevices();

    this.root.hidden = false;
    // Confirm takes focus, so Enter starts a recording the same way as last time.
    this.trap.activate(this.confirm);

    return new Promise((resolve) => {
      this._resolve = resolve;
    });
  }

  // -- reading the form --------------------------------------------------------------

  get _choice() {
    return {
      source: this._value(this.sources) ?? DEFAULTS.source,
      timing: this._value(this.timings) ?? DEFAULTS.timing,
      audio_source: this._value(this.audios) ?? DEFAULTS.audio_source,
      video: Boolean(this.video?.checked),
    };
  }

  /** The start request's options for a choice. Video only ever goes out for a window. */
  _toOptions(choice) {
    const isWindow = choice.source === WINDOW;
    const timing = TIMINGS[choice.timing] ?? TIMINGS.live;
    return {
      source: choice.source,
      ...timing,
      video: isWindow && choice.video,
      audio_source: isWindow ? choice.audio_source : "system",
    };
  }

  /** Why this choice cannot be recorded, or an empty string. */
  _refusalFor(options) {
    if (options.live_transcription || options.post_transcription || options.video) return "";
    return options.source === WINDOW
      ? "With no transcription and no video there is nothing to record."
      : "Choose when to transcribe.";
  }

  // -- drawing -----------------------------------------------------------------------

  _render() {
    const choice = this._choice;
    const isWindow = choice.source === WINDOW;
    const fileSource = config.get("audio.source_type") === "file";

    // The microphone list answers two questions: "which microphone" for a microphone recording,
    // and the follow-up to a window recorded with "My microphone" as its sound. Without the second,
    // a window narrated over could only ever use whatever microphone was last configured.
    const windowMic = isWindow && choice.audio_source === "microphone";
    const asksMicrophone = choice.source === "microphone" || windowMic;
    toggle(this.sections.get("microphone"), asksMicrophone && !fileSource);
    toggle(this.deviceStep, !windowMic);
    toggle(this.sections.get("window-audio"), isWindow && !fileSource);
    toggle(this.sections.get("video"), isWindow);
    toggle(this.sections.get("window-note"), isWindow && !fileSource);
    toggle(this.fileNote, fileSource);
    if (fileSource) {
      const path = config.get("audio.file_path") ?? "";
      setText(this.fileName, path.split("/").pop() || "an audio file");
    }

    // "Don't transcribe" is only meaningful with a picture to keep.
    toggle(this.timingNone, isWindow);
    if (!isWindow && choice.timing === "none") this._check(this.timings, "live");

    // The step numbers follow the sections actually showing, so the sequence never skips one.
    const hasInput = (choice.source === "microphone" || isWindow) && !fileSource;
    setText(this.timingStep, hasInput ? "3" : "2");
    const videoStep = $("[data-record-section='video'] .record-sheet__step", this.root);
    setText(videoStep, hasInput ? "4" : "3");

    const options = this._toOptions(this._choice);
    const refusal = this._refusalFor(options);
    if (this.refusal) {
      setText(this.refusal, refusal);
      this.refusal.hidden = !refusal;
    }
    if (this.confirm) {
      this.confirm.disabled = Boolean(refusal);
      // Says what happens next: with video on, the desktop's window picker comes first.
      setText(this.confirm, options.video ? "Choose a window…" : "Start recording");
    }
  }

  /**
   * A source this machine cannot run is disabled and says why — never hidden. The reason goes on
   * a line under the cards rather than inside one: an install command does not fit a card, and a
   * card that grows to hold it throws the row out of line.
   */
  _renderAvailability() {
    const windowInput = this.sources.find((input) => input.value === WINDOW);
    if (!windowInput) return;
    const available = modeStore.isAvailable(WINDOW);
    windowInput.disabled = !available;
    const reason = available
      ? ""
      : modeStore.reasonFor(WINDOW) || "Window recording is not available on this machine.";
    setText(this.windowReason, reason ? `A window: ${reason}` : "");
    if (this.windowReason) this.windowReason.hidden = !reason;
  }

  /** The microphone list, fetched on open rather than on load: devices come and go. */
  async _loadDevices() {
    if (!this.device) return;
    const current = config.get("audio.device_id") ?? "";
    try {
      const { devices, device_support: supported, note } = await api.devices();
      const inputs = devices.filter((device) => device.kind !== "file");
      this.device.replaceChildren(
        el("option", { text: "System default", attrs: { value: "" } }),
        ...inputs.map((device) =>
          el("option", {
            text: device.is_default ? `${device.name} — default` : device.name,
            attrs: { value: device.id },
          })
        )
      );
      this.device.value = current;
      this.device.disabled = !supported;
      setText(this.deviceNote, note || (inputs.length ? "" : "No microphones were found."));
    } catch (error) {
      setText(this.deviceNote, error.message);
    }
  }

  // -- closing -----------------------------------------------------------------------

  async _submit() {
    const choice = this._choice;
    const options = this._toOptions(choice);
    if (this._refusalFor(options)) return;

    // The microphone is configuration, not a per-run override: the Audio tab and the tray read the
    // same value, and two answers to "which microphone" would disagree (D-075).
    if (this.device && !this.sections.get("microphone")?.hidden) {
      const wanted = this.device.value || null;
      if (wanted !== (config.get("audio.device_id") ?? null)) {
        try {
          await config.patch({ "audio.device_id": wanted });
        } catch (error) {
          setText(this.refusal, error.message);
          this.refusal.hidden = false;
          return;
        }
      }
    }

    prefs.set("lastRecordOptions", choice);
    this._close(options);
  }

  _close(result) {
    if (!this.root || this.root.hidden) return;
    this.root.hidden = true;
    this.trap.release();

    const resolve = this._resolve;
    this._resolve = null;
    resolve?.(result);
  }

  _value(inputs) {
    return inputs.find((input) => input.checked)?.value;
  }

  _check(inputs, value) {
    for (const input of inputs) input.checked = input.value === value;
  }
}
