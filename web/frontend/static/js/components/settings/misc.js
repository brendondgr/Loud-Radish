/**
 * The Context and Storage tabs.
 *
 * Both are almost entirely declarative — the binding layer handles every field. What is left is the
 * handful of places where one setting only makes sense in terms of another, or where a control does
 * something rather than storing something.
 */

import { $, el, setText } from "../../core/dom.js";
import { duration } from "../../core/format.js";
import { config } from "../../stores/config.js";
import { api } from "../../transport/api.js";
import { session } from "../../stores/session.js";

export class ContextSettings {
  constructor(root) {
    this.note = $("[data-context-budget-note]", root);
  }

  sync() {
    if (!this.note) return;

    const budget = Number(config.get("context.token_budget") ?? 0);
    const mode = config.get("llm.mode");
    const window = Number(
      config.get(mode === "api" ? "llm.api.context_window" : "llm.local.context_window") ?? 0
    );

    // A budget above the model's window is the failure this note exists to prevent: the provider
    // truncates the prompt from the front, which removes the instructions and leaves the model
    // answering from transcript fragments with no idea what it was asked to do.
    if (window && budget > window * 0.8) {
      setText(
        this.note,
        `That is close to the model's ${window.toLocaleString()}-token window. Leave room for the answer — around ${Math.floor(window * 0.6).toLocaleString()} is safer.`
      );
      return;
    }

    setText(
      this.note,
      window
        ? `Out of the model's ${window.toLocaleString()}-token window.`
        : "Set the model's context window on the Assistant tab so this can be checked against it."
    );
  }
}

export class StorageSettings {
  constructor(root, { onStatus }) {
    this.root = root;
    this.onStatus = onStatus;
    this.facts = $("[data-settings-facts]", root);
    this.exportNote = $("[data-export-note]", root);
    this.recordingsList = $("[data-recordings-list]", root);
    this.recordingsNote = $("[data-recordings-note]", root);

    $("[data-export-now]", root)?.addEventListener("click", () => this.exportTranscript());
  }

  async load() {
    if (this.facts) {
      try {
        const health = await api.health();
        this.renderFacts(health);
      } catch (error) {
        setText(this.exportNote, error.message);
      }
    }
    await this.loadRecordings();
  }

  /**
   * List the audio still on disk (D-021).
   *
   * An empty list is the *normal* state, because a successful pass deletes its own audio — so the
   * note explains that rather than leaving an empty panel looking broken.
   */
  async loadRecordings() {
    if (!this.recordingsList) return;
    try {
      const { recordings, retain_audio: retain } = await api.recordings();
      this.renderRecordings(recordings, retain);
    } catch (error) {
      setText(this.recordingsNote, error.message);
      this.recordingsList.replaceChildren();
    }
  }

  renderRecordings(recordings, retain) {
    setText(
      this.recordingsNote,
      recordings.length === 0
        ? "None. Audio is deleted once it has been transcribed — an empty list is the normal state."
        : retain
          ? `${recordings.length} kept, because audio retention is on.`
          : `${recordings.length} left over from a pass that failed or was interrupted. ` +
            "Transcribe one again, or delete it."
    );

    this.recordingsList.replaceChildren(
      ...recordings.map((entry) => this.renderRecording(entry))
    );
  }

  renderRecording(entry) {
    const megabytes = (entry.bytes / (1024 * 1024)).toFixed(1);
    const detail = entry.unreadable
      ? "unreadable — only deleting is possible"
      : `${duration(entry.duration_s ?? 0)} · ${megabytes} MB`;

    const retry = el("button", { className: "button button--small", text: "Transcribe" });
    retry.disabled = Boolean(entry.unreadable);
    retry.addEventListener("click", () => this.retranscribe(entry.name, retry));

    const remove = el("button", {
      className: "button button--small button--quiet",
      text: "Delete",
    });
    remove.addEventListener("click", () => this.deleteRecording(entry.name));

    return el("li", {
      className: "recordings__item",
      children: [
        el("div", {
          className: "recordings__text",
          children: [
            el("span", { className: "recordings__name", text: entry.name }),
            el("span", { className: "recordings__detail", text: detail }),
          ],
        }),
        el("div", { className: "recordings__actions", children: [retry, remove] }),
      ],
    });
  }

  async retranscribe(name, button) {
    button.disabled = true;
    try {
      await api.transcribeRecording(name);
      this.onStatus?.(`Transcribing ${name}. Progress shows in the transcript.`, "ok");
    } catch (error) {
      // The message already names the remedy — no model loaded, a pass already running, a session
      // recording — so it is surfaced verbatim rather than replaced with something vaguer.
      this.onStatus?.(error.message, "error");
      button.disabled = false;
    }
  }

  async deleteRecording(name) {
    if (!window.confirm(`Delete ${name}? The audio cannot be recovered.`)) return;
    try {
      await api.deleteRecording(name);
      await this.loadRecordings();
      this.onStatus?.(`${name} deleted.`, "ok");
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }

  sync() {
    if (!this.exportNote) return;
    setText(
      this.exportNote,
      session.running || session.sessionId
        ? `Downloads as ${config.get("storage.default_export_format")}.`
        : "Nothing to export yet — record something first."
    );
  }

  renderFacts(health) {
    const optional = health.optional ?? {};
    const rows = [
      ["Version", health.version ?? "—"],
      ["Config file", config.get("__path") ?? health.config_path ?? "data/loud-radish-config.json"],
      ["Real transcription", optional.asr_whisper ? "installed" : "not installed"],
      ["Device capture", optional.audio_device ? "installed" : "not installed"],
      ["Silero detection", optional.vad_silero ? "installed" : "not installed"],
      ["Credential store", optional.credentials ? "installed" : "not installed"],
    ];

    this.facts.replaceChildren(
      ...rows.flatMap(([term, value]) => [
        el("dt", { text: term }),
        el("dd", { text: String(value) }),
      ])
    );
  }

  /**
   * Download the transcript.
   *
   * Navigating to the URL rather than fetching it: the response is a file, and letting the browser
   * handle a file download is both simpler and better behaved than building a blob and a synthetic
   * link — the filename comes from the server's own header.
   */
  exportTranscript() {
    if (!session.running && !session.sessionId) {
      this.onStatus?.("There is no session to export yet.", "error");
      return;
    }
    window.location.assign(api.exportUrl(config.get("storage.default_export_format")));
    this.onStatus?.("Export started.", "ok");
  }
}
