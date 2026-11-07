/**
 * The header: run state, elapsed clock, what is running, the primary control, and identity.
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
import { $, setAttr, setText, toggle } from "../core/dom.js";
import { timestamp } from "../core/format.js";
import { ARMING, ERROR, IDLE, PAUSED, PROCESSING, RECORDING, STOPPING } from "../core/modes.js";
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
  [ARMING]: { label: "Choosing what to record…", button: "Cancel", state: "arming", enabled: true },
  [RECORDING]: { label: "Recording", button: "Stop", state: "recording", enabled: true },
  [PAUSED]: { label: "Paused", button: "Stop", state: "paused", enabled: true },
  [STOPPING]: { label: "Stopping…", button: "Stopping…", state: "stopping", enabled: false },
  [PROCESSING]: { label: "Transcribing…", button: "Transcribing…", state: "busy", enabled: false },
  [ERROR]: { label: "Recording failed", button: "Start recording", state: "error", enabled: true },
};

/** How the label beside the clock names each source. */
const SOURCE_NAMES = { microphone: "Microphone", system: "Computer sound", window: "Window" };

/** States in which a run exists to be described. */
const DESCRIBED = new Set([RECORDING, PAUSED, STOPPING, PROCESSING]);

/**
 * "Microphone · live + after" — what is being captured, and when it is transcribed. "Window + mic"
 * when the microphone is mixed into another source's sound (D-081).
 */
export function describeRun(options) {
  if (!options) return "";
  const parts = [];
  if (options.video) parts.push("video");
  if (options.live_transcription) parts.push("live");
  if (options.post_transcription) parts.push("after");
  let source = SOURCE_NAMES[options.source] ?? "";
  const inputs = options.audio_inputs ?? [];
  if (source && options.source !== "microphone" && inputs.includes("microphone")) {
    // A window narrated over with no sound of its own is "Window (mic only)"; with its sound too
    // it is "Window + mic".
    source = inputs.length > 1 ? `${source} + mic` : `${source} (mic only)`;
  }
  return [source, parts.join(" + ")].filter(Boolean).join(" · ");
}

export class Header {
  constructor(root, { onArm, onStop, onPause, onResume, onOpenSettings }) {
    this.root = root;
    this.onArm = onArm;
    this.onStop = onStop;
    this.onPause = onPause;
    this.onResume = onResume;

    this.state = $("[data-record-state]", root);
    this.stateLabel = $("[data-record-label]", root);
    this.clock = $("[data-clock]", root);
    this.runChip = $("[data-run-chip]", root);
    this.toggle = $("[data-session-toggle]", root);
    this.toggleLabel = $("[data-session-toggle-label]", root) ?? this.toggle;
    this.toggleIcon = $("[data-session-toggle-icon]", root);
    this.hold = $("[data-session-hold]", root);
    this.privacy = $("[data-privacy]", root);
    this.privacyLabel = $("[data-privacy-label]", root);
    this.modelName = $("[data-model-name]", root);
    this.llmName = $("[data-llm-name]", root);

    this.toggle?.addEventListener("click", () => this._onToggle());
    this.hold?.addEventListener("click", () => this._onHold());
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
    setText(this.stateLabel, this._stateLabel(shown));
    setAttr(this.state, "title", modeStore.state === ERROR ? modeStore.message : "");

    // The one state whose label carries a number. A pass over a long recording can run for half
    // an hour, and a control that says only "Transcribing…" for that long is indistinguishable
    // from one that has hung.
    const label =
      modeStore.state === PROCESSING && recording.totalSeconds > 0
        ? `Transcribing… ${recording.percent}%`
        : shown.button;

    setText(this.toggleLabel, label);
    // The dot says "record"; on Stop, Cancel or a percentage it would say the wrong thing.
    toggle(this.toggleIcon, modeStore.state === IDLE);
    if (this.toggle) {
      this.toggle.disabled = !shown.enabled;
      // Danger styling only while there is something to lose. `stopping` has already been asked
      // for, and colouring an inert button as destructive invites a second click at it.
      this.toggle.classList.toggle("button--danger", modeStore.state === RECORDING);
    }

    this._renderHold();
    this._renderRunChip();
    this.renderClock();
    this.renderIdentity();
  }

  /**
   * The pause control, which exists only while there is a capture to hold.
   *
   * Hidden rather than disabled outside those two states: a disabled control invites the question
   * "why can I not press this", and the answer — there is no recording — is already the whole of
   * what the rest of the header says.
   */
  _renderHold() {
    if (!this.hold) return;
    const held = modeStore.state === PAUSED;
    const available = held || modeStore.state === RECORDING;
    this.hold.hidden = !available;
    setText(this.hold, held ? "Resume" : "Pause");
    setAttr(this.hold, "aria-pressed", String(held));
  }

  _stateLabel(shown) {
    if (modeStore.state === PROCESSING && recording.totalSeconds > 0) {
      return `Transcribing ${recording.percent}%`;
    }
    // Arming has two halves: the sheet is open, then — for a window with video — the desktop's
    // picker is. The second is the one worth naming, because it is a different program's dialog.
    if (modeStore.state === ARMING && modeStore.options) {
      return modeStore.options.video ? "Choosing a window…" : "Starting…";
    }
    return shown.label;
  }

  _renderRunChip() {
    if (!this.runChip) return;
    const text = DESCRIBED.has(modeStore.state) ? describeRun(modeStore.options) : "";
    setText(this.runChip, text);
    // The label truncates on a narrow header, so the whole of it is also on the title.
    setAttr(this.runChip, "title", text ? `This recording: ${text}` : "");
    this.runChip.hidden = !text;
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
    // The names ellipsise when a recording's controls need the room, so the whole of them is on
    // the button's title as well.
    setAttr(
      this.modelName?.closest(".model-identity"),
      "title",
      `${this.modelName?.textContent ?? ""} · ${this.llmName?.textContent ?? ""}`
    );

    const local = session.fullyLocal;
    setAttr(this.privacy, "data-state", local ? "local" : "remote");
    setText(this.privacyLabel, local ? "Fully local" : "Sending data off this machine");
  }

  /** Pause holds the capture; resume continues the same session, file and store. */
  async _onHold() {
    if (modeStore.state === PAUSED) {
      await this.onResume?.();
      return;
    }
    if (modeStore.state === RECORDING) await this.onPause?.();
  }

  /** One control, every state. What pressing it means comes from the store, not a boolean here. */
  async _onToggle() {
    switch (modeStore.action) {
      case "arm":
        await this.onArm?.();
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
