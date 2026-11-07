/**
 * Approving a transcription of a recording that has no transcript (D-076).
 *
 * **Nothing transcribes a past recording until the user presses Transcribe here.** The dialog
 * reads the server's plan — which starts nothing — and says exactly what will run: taking the
 * sound out of the video when that is the only copy, then transcribing so many minutes with the
 * configured model. The checkbox decides whether the recording comes to the main screen after.
 *
 * Used on both pages. `show(key)` resolves to the started job's response, or `null` when the user
 * cancelled or the server refused.
 */

import { FocusTrap } from "../a11y/focus-trap.js";
import { $, el, setText, toggle } from "../core/dom.js";
import { duration as formatDuration } from "../core/format.js";
import { api, ApiError } from "../transport/api.js";

export class TranscribeConsent {
  constructor(root) {
    this.root = root;
    if (!root) return;
    this.dialog = $("[data-consent-dialog]", root);
    this.subtitle = $("[data-consent-subtitle]", root);
    this.steps = $("[data-consent-steps]", root);
    this.openWhenDone = $("[data-consent-open]", root);
    this.refusal = $("[data-consent-refusal]", root);
    this.confirm = $("[data-consent-confirm]", root);
    this.trap = new FocusTrap(this.dialog ?? root);
    this.key = "";
    this._resolve = null;

    for (const button of root.querySelectorAll("[data-consent-cancel]")) {
      button.addEventListener("click", () => this._finish(null));
    }
    this.confirm?.addEventListener("click", () => void this._approve());
    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        this._finish(null);
      }
    });
  }

  get isOpen() {
    return Boolean(this.root) && !this.root.hidden;
  }

  async show(key) {
    if (!this.root || this.isOpen) return null;
    this.key = key;
    this._refuse("");
    this.steps?.replaceChildren(el("li", { text: "Reading the recording…" }));
    setText(this.subtitle, "");
    if (this.openWhenDone) this.openWhenDone.checked = true;
    if (this.confirm) this.confirm.disabled = true;
    this.root.hidden = false;
    this.trap.activate(this.confirm);

    const done = new Promise((resolve) => {
      this._resolve = resolve;
    });

    try {
      this._render(await api.transcribePlan(key));
    } catch (error) {
      this.steps?.replaceChildren();
      this._refuse(error instanceof ApiError ? error.message : "This recording could not be read.");
    }
    return done;
  }

  _render(plan) {
    setText(this.subtitle, plan.title ?? "");
    const length = plan.seconds ? `${formatDuration(plan.seconds)} of audio` : "the audio";
    const steps = [];
    if (!plan.model_ready) steps.push(`Load the ${plan.model} speech model — it is not loaded.`);
    if (plan.source === "video") steps.push("Take the sound out of the video.");
    if (plan.source === "resume") {
      steps.push(`Continue the transcription that stopped part-way, over ${length}.`);
    } else {
      steps.push(`Transcribe ${length} with the ${plan.model} speech model.`);
    }
    steps.push("Nothing else: no summary or clean-up is run on your behalf.");
    this.steps?.replaceChildren(...steps.map((text) => el("li", { text })));

    if (plan.refusal) {
      this._refuse(plan.refusal.message);
      return;
    }
    if (this.confirm) {
      this.confirm.disabled = false;
      this.confirm.focus();
    }
  }

  async _approve() {
    if (!this.key || !this.confirm || this.confirm.disabled) return;
    this.confirm.disabled = true;
    setText(this.confirm, "Starting…");
    try {
      const body = await api.transcribeRecording(this.key, Boolean(this.openWhenDone?.checked));
      this._finish(body);
    } catch (error) {
      this._refuse(error instanceof ApiError ? error.message : "The transcription did not start.");
      this.confirm.disabled = false;
    } finally {
      setText(this.confirm, "Transcribe");
    }
  }

  _refuse(message) {
    setText(this.refusal, message);
    toggle(this.refusal, Boolean(message));
  }

  _finish(result) {
    if (!this.isOpen) return;
    this.root.hidden = true;
    this.trap.release();
    this._resolve?.(result);
    this._resolve = null;
  }
}
