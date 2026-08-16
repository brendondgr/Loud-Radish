/**
 * The pre-flight options sheet (D-020).
 *
 * Opens when a mode is armed, collects the options for that one run, and resolves to them — or to
 * `null` if the user cancelled. The caller starts the session; this component never does, which is
 * what keeps "what was chosen" separate from "what happens next".
 *
 * `show()` returns a promise deliberately. Arming is a sequence — ask, then start, then let the
 * desktop pick a window — and a promise is the shape that reads as a sequence at the call site
 * instead of a callback that has to reach back into the caller's state.
 */

import { FocusTrap } from "../a11y/focus-trap.js";
import { $ } from "../core/dom.js";

const DEFAULTS = {
  live_transcription: true,
  post_transcription: true,
  video: true,
  // The window's own sound, not the person watching it. A window recording that captures the
  // viewer's microphone is the fault this default exists to prevent.
  audio_source: "system",
};

export class Preflight {
  constructor(root, { onOpenSettings } = {}) {
    this.root = root;
    if (!root) return;

    this.dialog = $("[data-preflight-dialog]", root);
    this.confirm = $("[data-preflight-confirm]", root);
    this.refusal = $("[data-preflight-refusal]", root);
    this.inputs = new Map();
    for (const input of root.querySelectorAll("[data-preflight-option]")) {
      this.inputs.set(input.dataset.preflightOption, input);
      input.addEventListener("change", () => this._validate());
    }

    // The audio choice is a radio group rather than a checkbox, because the three are exclusive
    // and one of them is always true — there is no "record no audio at all" option, and there
    // should not be: a recording with no sound has no transcript, which is the point of the tool.
    this.audioInputs = [...root.querySelectorAll("[data-preflight-audio]")];

    this.trap = new FocusTrap(this.dialog ?? root);
    this._resolve = null;

    for (const button of root.querySelectorAll("[data-preflight-cancel]")) {
      button.addEventListener("click", () => this._close(null));
    }
    this.confirm?.addEventListener("click", () => this._close(this.options));

    $("[data-preflight-settings]", root)?.addEventListener("click", () => {
      // Cancels the run rather than stacking a second dialog over this one: choosing an input is
      // exactly the kind of thing you go and do, and coming back to a half-filled sheet whose
      // options you can no longer see is worse than starting again.
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

  /** The options as currently ticked. */
  get options() {
    const chosen = { ...DEFAULTS };
    for (const [name, input] of this.inputs) chosen[name] = input.checked;
    const audio = this.audioInputs.find((input) => input.checked);
    if (audio) chosen.audio_source = audio.value;
    return chosen;
  }

  /**
   * Open the sheet. Resolves to the chosen options, or `null` if cancelled.
   *
   * Escape and the scrim both cancel, and cancelling starts nothing — which is the whole reason
   * `arming` is a separate run state rather than part of `recording`.
   */
  show() {
    if (!this.root) return Promise.resolve(DEFAULTS);

    for (const [name, input] of this.inputs) input.checked = DEFAULTS[name];
    this._validate();

    this.root.hidden = false;
    this.trap.activate(this.inputs.values().next().value);

    return new Promise((resolve) => {
      this._resolve = resolve;
    });
  }

  /** Whether the sheet is open, so a caller does not stack two. */
  get isOpen() {
    return Boolean(this.root) && !this.root.hidden;
  }

  _validate() {
    // All three off records nothing. Refused here as a courtesy; the server refuses it again,
    // because the client check is a convenience and the server check is the rule.
    const nothing = Object.values(this.options).every((value) => value === false);
    if (this.refusal) this.refusal.hidden = !nothing;
    if (this.confirm) this.confirm.disabled = nothing;
    return !nothing;
  }

  _close(result) {
    if (!this.root || this.root.hidden) return;
    this.root.hidden = true;
    this.trap.release();

    const resolve = this._resolve;
    this._resolve = null;
    resolve?.(result);
  }
}
