/**
 * The Audio tab: capture source, the recording library, and the live level meter.
 *
 * The parts that are not plain configuration all exist for one reason — a browser cannot see the
 * server's filesystem. `audio.file_path` is a path on the machine running the pipeline, so without
 * a listing endpoint and an upload the only way to choose a recording is to edit a config file by
 * hand. That is the step this whole tab exists to remove.
 */

import { on } from "../../core/bus.js";
import { $, el, setText } from "../../core/dom.js";
import { duration as formatDuration } from "../../core/format.js";
import { config } from "../../stores/config.js";
import { api } from "../../transport/api.js";
import { AUDIO_LEVEL } from "../../transport/events.js";
import { refreshDependants } from "./bindings.js";

/** Bytes as a short human string, for the recording list. */
function megabytes(bytes) {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export class AudioSettings {
  constructor(root, { onStatus }) {
    this.root = root;
    this.onStatus = onStatus;

    this.deviceSelect = $("[data-device-select]", root);
    this.deviceNote = $("[data-device-note]", root);
    this.fileSelect = $("[data-file-select]", root);
    this.fileNote = $("[data-file-note]", root);
    this.fileInput = $("[data-file-upload]", root);
    this.vadNote = $("[data-vad-note]", root);
    this.meter = $("[data-settings-meter]", root);
    this.meterFill = $("[data-settings-meter-fill]", root);
    this.meterNote = $("[data-settings-meter-note]", root);

    $("[data-device-refresh]", root)?.addEventListener("click", () => this.loadDevices());
    this.deviceSelect?.addEventListener("change", () => this.selectDevice());

    $("[data-file-upload-trigger]", root)?.addEventListener("click", () => this.fileInput?.click());
    this.fileInput?.addEventListener("change", () => this.upload());
    this.fileSelect?.addEventListener("change", () => this.selectFile());

    on(AUDIO_LEVEL, (level) => this.renderLevel(level));
  }

  /** Fetch both lists. Called when the dialog opens, so they are never stale on screen. */
  async load() {
    await Promise.all([this.loadDevices(), this.loadFiles()]);
    this.renderVadNote();
  }

  /** Re-render the parts that follow a configuration value rather than owning one. */
  sync() {
    this.renderVadNote();
    if (this.deviceSelect) this.deviceSelect.value = config.get("audio.device_id") ?? "";
    if (this.fileSelect) this.fileSelect.value = config.get("audio.file_path") ?? "";
  }

  // -- devices -------------------------------------------------------------------

  async loadDevices() {
    if (!this.deviceSelect) return;
    try {
      const { devices, device_support: supported, note } = await api.devices();
      this.renderDevices(devices.filter((device) => device.kind !== "file"), supported, note);
    } catch (error) {
      setText(this.deviceNote, error.message);
    }
  }

  renderDevices(devices, supported, note) {
    const current = config.get("audio.device_id") ?? "";
    this.deviceSelect.replaceChildren(
      el("option", { text: "System default", attrs: { value: "" } }),
      ...devices.map((device) =>
        el("option", {
          text: device.is_default ? `${device.name} — default` : device.name,
          attrs: { value: device.id },
        })
      )
    );
    this.deviceSelect.value = current;
    this.deviceSelect.disabled = !supported;

    // The remedy is named rather than the list simply being empty: an empty dropdown with no
    // explanation looks like a broken feature rather than a missing optional dependency.
    setText(
      this.deviceNote,
      note ||
        (devices.length
          ? "Takes effect when the next session starts."
          : "No input devices were found. Check that something is plugged in and unmuted.")
    );
  }

  async selectDevice() {
    const value = this.deviceSelect.value;
    try {
      await config.patch({ "audio.device_id": value === "" ? null : value });
      this.onStatus?.("Input device set.", "ok");
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }

  // -- the recording library -----------------------------------------------------

  async loadFiles() {
    if (!this.fileSelect) return;
    try {
      const { files, directory } = await api.audioFiles();
      this.renderFiles(files, directory);
    } catch (error) {
      setText(this.fileNote, error.message);
    }
  }

  renderFiles(files, directory) {
    const current = config.get("audio.file_path") ?? "";

    this.fileSelect.replaceChildren(
      el("option", { text: "No recording selected", attrs: { value: "" } }),
      ...files.map((file) => {
        const parts = [file.name];
        if (file.duration_seconds) parts.push(formatDuration(file.duration_seconds));
        parts.push(megabytes(file.size_bytes));
        if (file.problem) parts.push(file.problem);
        return el("option", {
          text: parts.join(" · "),
          // An unusable file is listed but not selectable. Omitting it entirely would look
          // identical to it never having been uploaded.
          attrs: { value: file.path, disabled: !file.usable },
        });
      })
    );
    this.fileSelect.value = current;

    setText(
      this.fileNote,
      files.length
        ? `Recordings in ${directory}. Add a WAV to put one there without leaving this window.`
        : `No recordings yet. Add a WAV, or drop one into ${directory}.`
    );
  }

  async selectFile() {
    try {
      const path = this.fileSelect.value;
      await config.patch({ "audio.file_path": path === "" ? null : path });
      this.onStatus?.(path ? "Recording selected." : "Recording cleared.", "ok");
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }

  async upload() {
    const file = this.fileInput?.files?.[0];
    if (!file) return;

    setText(this.fileNote, `Uploading ${file.name}…`);
    try {
      const { file: stored } = await api.uploadAudioFile(file);
      // The upload also selects it server-side, so the config cache has to be re-read or the form
      // would still show whatever was selected before.
      await config.load();
      await this.loadFiles();
      refreshDependants(this.root);
      this.onStatus?.(`${stored.name} added and selected.`, "ok");
    } catch (error) {
      setText(this.fileNote, error.message);
      this.onStatus?.(error.message, "error");
    } finally {
      // Cleared so choosing the same file twice in a row still fires `change`.
      this.fileInput.value = "";
    }
  }

  // -- live feedback --------------------------------------------------------------

  renderVadNote() {
    if (!this.vadNote) return;
    const detector = config.get("vad.detector");
    setText(
      this.vadNote,
      detector === "silero"
        ? "More accurate in a noisy room. Needs the vad-silero extra and a model file; the energy detector is used automatically if it is unavailable."
        : "Fast and dependency-free. Judges speech by loudness and zero-crossing rate, so a very noisy room can fool it."
    );
  }

  renderLevel(level) {
    if (!this.meterFill) return;
    // Level is linear RMS. The square root gives the meter a usable spread at speech levels —
    // linear leaves normal speech in the leftmost tenth of the bar.
    const fraction = Math.min(1, Math.sqrt(Math.max(0, level.rms ?? 0)));
    this.meterFill.style.width = `${(fraction * 100).toFixed(1)}%`;
    this.meter?.setAttribute("data-clipping", String(Boolean(level.clipping)));

    if (level.clipping) {
      setText(this.meterNote, "Clipping — the input is too loud. Lower the gain or move back.");
    } else if (fraction > 0.02) {
      setText(this.meterNote, "Receiving audio.");
    }
  }
}
