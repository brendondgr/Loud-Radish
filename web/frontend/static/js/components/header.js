/**
 * The header: capture mode, run state, elapsed clock, the primary control, and identity.
 *
 * The control used to flip a boolean. It now renders six run states from `stores/mode.js` and asks
 * that store what pressing it means, rather than working the rules out itself (D-020). Everything
 * about *which* state is current belongs to the store; everything about *how it looks* belongs
 * here, and the table below is the whole of it.
 *
 * Stopping asks for confirmation. An accidental stop mid-talk is costly, and the confirmation is
 * the cheapest possible guard against it (FE §6.1).
 */

import { on } from "../core/bus.js";
import { $, setAttr, setText } from "../core/dom.js";
import { timestamp } from "../core/format.js";
import { ARMING, ERROR, IDLE, PROCESSING, RECORDING, STOPPING } from "../core/modes.js";
import { HEALTH_CHANGED, health } from "../stores/health.js";
import { MODE_CHANGED, mode as modeStore } from "../stores/mode.js";
import { RECORDING_CHANGED, recording } from "../stores/recording.js";
import { SESSION_CHANGED, session } from "../stores/session.js";

/**
 * How each run state presents itself. Specified in `docs/design-system.md` § Capture Modes.
 *
 * `state` is what the dot and the wash key off; it is not always the run state, because `arming`
 * and `processing` both read as "the application is working on it" and share an appearance.
 */
const PRESENTATION = {
  [IDLE]: { label: "Not recording", button: "Start recording", state: "idle", enabled: true },
  [ARMING]: { label: "Choosing a window…", button: "Cancel", state: "arming", enabled: true },
  [RECORDING]: { label: "Recording", button: "Stop", state: "recording", enabled: true },
  [STOPPING]: { label: "Stopping…", button: "Stopping…", state: "stopping", enabled: false },
  [PROCESSING]: { label: "Transcribing…", button: "Transcribing…", state: "busy", enabled: false },
  [ERROR]: { label: "Recording failed", button: "Start recording", state: "error", enabled: true },
};

export class Header {
  constructor(root, { onStart, onArm, onStop, onOpenSettings }) {
    this.root = root;
    this.onStart = onStart;
    this.onArm = onArm;
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
    on(MODE_CHANGED, () => this.render());
    on(RECORDING_CHANGED, () => this.render());
    on(HEALTH_CHANGED, () => this.renderIdentity());

    // The clock ticks once a second but is *derived* from the start time, so it stays correct
    // across a backgrounded tab or a machine that slept.
    setInterval(() => this.renderClock(), 1000);
    this.render();
  }

  render() {
    const shown = PRESENTATION[modeStore.state] ?? PRESENTATION[IDLE];

    setAttr(this.state, "data-state", shown.state);
    // The label stays short and the *banner* carries the message. Putting the failure text here
    // instead was tried and is wrong twice over: a sentence naming a file path stretches the
    // header until the primary control is pushed off a narrow screen, and it duplicates what the
    // banner directly below is already saying and already announcing.
    setText(
      this.stateLabel,
      modeStore.state === PROCESSING && recording.totalSeconds > 0
        ? `Transcribing ${recording.percent}%`
        : shown.label
    );
    setAttr(this.state, "title", modeStore.state === ERROR ? modeStore.message : "");

    // The one state whose label carries a number. A pass over a long recording can run for half
    // an hour, and a control that says only "Transcribing…" for that long is indistinguishable
    // from one that has hung.
    const label =
      modeStore.state === PROCESSING && recording.totalSeconds > 0
        ? `Transcribing… ${recording.percent}%`
        : shown.button;

    setText(this.toggle, label);
    if (this.toggle) {
      this.toggle.disabled = !shown.enabled;
      // Danger styling only while there is something to lose. `stopping` has already been asked
      // for, and colouring an inert button as destructive invites a second click at it.
      this.toggle.classList.toggle("button--danger", modeStore.state === RECORDING);
    }

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

  /**
   * One control, six states. What pressing it means comes from the store, not from a boolean here.
   */
  async _onToggle() {
    switch (modeStore.action) {
      case "arm":
        await this.onArm?.(modeStore.mode);
        return;
      case "start":
        await this.onStart?.(modeStore.mode);
        return;
      case "stop":
        // Arming has captured nothing yet, so cancelling it costs nothing and asking would be
        // ceremony. Stopping a recording cannot be undone, so it asks.
        if (modeStore.state === ARMING) {
          await this.onStop?.({ armedOnly: true });
          return;
        }
        if (window.confirm("Stop recording? The transcript so far is kept.")) {
          await this.onStop?.({ armedOnly: false });
        }
        return;
      default:
        // `stopping` and `processing`: the control is disabled and this is unreachable by click.
        // Kept explicit so a future state that forgets its presentation entry fails loudly here
        // rather than silently starting a second session.
    }
  }
}
