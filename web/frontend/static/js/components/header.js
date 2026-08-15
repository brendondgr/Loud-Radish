/**
 * The header: recording state, elapsed clock, the primary control, and identity.
 *
 * Stopping asks for confirmation. An accidental stop mid-talk is costly, and the confirmation is
 * the cheapest possible guard against it (FE §6.1).
 */

import { on } from "../core/bus.js";
import { $, setAttr, setText } from "../core/dom.js";
import { timestamp } from "../core/format.js";
import { HEALTH_CHANGED, health } from "../stores/health.js";
import { SESSION_CHANGED, session } from "../stores/session.js";

export class Header {
  constructor(root, { onStart, onStop, onOpenSettings }) {
    this.root = root;
    this.onStart = onStart;
    this.onStop = onStop;

    this.state = $("[data-record-state]", root);
    this.stateLabel = $("[data-record-label]", root);
    this.clock = $("[data-clock]", root);
    this.toggle = $("[data-session-toggle]", root);
    this.privacy = $("[data-privacy]", root);
    this.privacyLabel = $("[data-privacy-label]", root);
    this.modelName = $("[data-model-name]", root);
    this.llmName = $("[data-llm-name]", root);

    this.toggle?.addEventListener("click", () => this._onToggle());
    for (const button of root.querySelectorAll("[data-open-settings]")) {
      button.addEventListener("click", () => onOpenSettings?.(button.dataset.openSettings));
    }

    on(SESSION_CHANGED, () => this.render());
    on(HEALTH_CHANGED, () => this.renderIdentity());

    // The clock ticks once a second but is *derived* from the start time, so it stays correct
    // across a backgrounded tab or a machine that slept.
    setInterval(() => this.renderClock(), 1000);
    this.render();
  }

  render() {
    const running = session.running;

    setAttr(this.state, "data-state", running ? "recording" : "idle");
    setText(this.stateLabel, running ? "Recording" : "Not recording");
    setText(this.toggle, running ? "Stop" : "Start recording");
    this.toggle?.classList.toggle("button--danger", running);

    this.renderClock();
    this.renderIdentity();
  }

  renderClock() {
    setText(this.clock, timestamp(session.elapsedSeconds));
  }

  renderIdentity() {
    // `health.modelId` is what is actually loaded and only exists once a session has run. Before
    // that, the configured model is the honest answer — and the distinction is kept in the wording
    // rather than collapsed, because "selected" and "loaded" are different states and the gap
    // between them is where model-loading failures live.
    const configured = session.config?.asr?.model;
    setText(
      this.modelName,
      health.modelId || (configured ? `${configured} — not loaded` : "No model selected")
    );

    const llm = session.config?.llm;
    const llmModel = llm?.mode === "api" ? llm?.api?.model : llm?.local?.model;
    setText(this.llmName, session.llmConfigured ? llmModel : "Assistant not set up");

    const local = session.fullyLocal;
    setAttr(this.privacy, "data-state", local ? "local" : "remote");
    setText(this.privacyLabel, local ? "Fully local" : "Sending data off this machine");
  }

  async _onToggle() {
    if (!session.running) {
      await this.onStart?.();
      return;
    }
    // Confirmation, because an accidental stop mid-talk cannot be undone.
    if (window.confirm("Stop recording? The transcript so far is kept.")) {
      await this.onStop?.();
    }
  }
}
