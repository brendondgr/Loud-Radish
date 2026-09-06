/**
 * The post-recording export window (D-037).
 *
 * Reported as four things that are really one: no control over output quality or size, no way to
 * see what an export would cost before committing to it, no preview of what is about to be
 * exported, and a pipeline that runs as a black box. They are one window because they are one
 * decision followed by one wait.
 *
 * **Every figure comes from the server.** `GET /{key}/export/options` measured the recording with
 * `ffprobe` and resolved each preset against its real dimensions; a size computed in the browser
 * from the mirrored constants would describe a nominal file rather than the one on disk, which is
 * exactly the fault being repaired — the reported seminar asked for a 720p ceiling at capture and
 * recorded at 2560 x 1532.
 *
 * `show()` returns a promise, as the pre-flight sheet's does, because this is a sequence: choose,
 * then watch, then collect. The caller gets the finished job or `null`.
 */

import { FocusTrap } from "../a11y/focus-trap.js";
import { on } from "../core/bus.js";
import { $, el, setText, toggle } from "../core/dom.js";
import { duration as formatDuration } from "../core/format.js";
import { DEFAULT_PRESET } from "../core/export-presets.js";
import { EXPORT_CHANGED, exportJob } from "../stores/export.js";
import { api, ApiError } from "../transport/api.js";

/** Bytes as something a person weighing "can I send this" can read at a glance. */
function size(bytes) {
  if (!bytes) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/**
 * A range, collapsed to one figure when both ends round to the same thing.
 *
 * "29–29 MB" is a range that has stopped carrying information and started carrying doubt about the
 * component that drew it.
 */
function sizeRange(estimate) {
  if (!estimate || estimate.problem) return "—";
  const low = size(estimate.low_bytes);
  const high = size(estimate.high_bytes);
  if (estimate.exact || low === high) return low;
  return `${low.replace(/\s[KMG]B$/, "")}–${high}`;
}

export class ExportDialog {
  constructor(root) {
    this.root = root;
    if (!root) return;

    this.dialog = $("[data-export-dialog]", root);
    this.subtitle = $("[data-export-subtitle]", root);
    this.choose = $("[data-export-choose]", root);
    this.progress = $("[data-export-progress]", root);
    this.video = $("[data-export-video]", root);
    this.measured = $("[data-export-measured]", root);
    this.presetList = $("[data-export-presets]", root);
    this.includeChat = $("[data-export-include-chat]", root);
    this.chatHint = $("[data-export-chat-hint]", root);
    this.stageList = $("[data-export-stages]", root);
    this.overallFill = $("[data-export-overall-fill]", root);
    this.overallPercent = $("[data-export-overall-percent]", root);
    this.overallTime = $("[data-export-overall-time]", root);
    this.result = $("[data-export-result]", root);
    this.refusal = $("[data-export-refusal]", root);
    this.startButton = $("[data-export-start]", root);
    this.cancelButton = $("[data-export-cancel]", root);
    this.download = $("[data-export-download]", root);

    this.trap = new FocusTrap(this.dialog ?? root);
    this.key = "";
    this.options = null;
    this.preset = DEFAULT_PRESET;
    this._resolve = null;

    for (const button of root.querySelectorAll("[data-export-close]")) {
      button.addEventListener("click", () => this.close());
    }
    this.startButton?.addEventListener("click", () => this.start());
    this.cancelButton?.addEventListener("click", () => this.cancel());

    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        this.close();
      }
    });

    on(EXPORT_CHANGED, () => this.renderProgress());
  }

  get isOpen() {
    return Boolean(this.root) && !this.root.hidden;
  }

  /** Open for one session and resolve when it is closed. */
  async show(key, { title = "" } = {}) {
    if (!this.root) return null;
    this.key = key;
    this.options = null;
    this.preset = DEFAULT_PRESET;
    setText(this.subtitle, title || "Choose how it should look, and how large it should be.");
    this.root.hidden = false;
    this.trap.activate(this.startButton);
    this._showChoosing();
    await this._load();
    return new Promise((resolve) => {
      this._resolve = resolve;
    });
  }

  close() {
    if (!this.root || this.root.hidden) return;
    // The video keeps decoding otherwise, off-screen, against the same disk an encode may be
    // reading — and an audible one keeps playing behind a dialog that is no longer there.
    if (this.video) {
      this.video.pause();
      this.video.removeAttribute("src");
      this.video.load();
    }
    this.root.hidden = true;
    this.trap.release();
    const job = exportJob.covers(this.key) ? exportJob : null;
    this._resolve?.(job);
    this._resolve = null;
  }

  // -- choosing ---------------------------------------------------------------------

  async _load() {
    this._refuse("");
    try {
      this.options = await api.exportOptions(this.key);
    } catch (error) {
      this._refuse(
        error instanceof ApiError
          ? error.message
          : "This recording's options could not be read.",
      );
      toggle(this.startButton, false);
      return;
    }
    this.renderOptions();
    // A job may already be running — the window was closed and reopened, or the page was
    // reloaded mid-encode. Re-attaching beats starting a second one that would be refused.
    if (this.options.job) {
      exportJob.set(this.options.job);
      this._showProgress();
    }
  }

  renderOptions() {
    const source = this.options?.source ?? {};
    if (this.video) {
      this.video.src = api.sessionMediaUrl(this.key);
    }

    setText(
      this.measured,
      source.has_video
        ? `Recorded at ${source.width}×${source.height}, ${Math.round(source.frame_rate)} fps, ` +
            `${formatDuration(source.duration_s)} — ${size(source.size_bytes)} on disk.`
        : "This recording has no video.",
    );

    const chat = this.options?.chat_messages ?? 0;
    toggle(this.includeChat?.closest(".export__switch"), chat > 0);
    if (chat > 0) {
      setText(
        this.chatHint,
        `Off by default. Whoever opens the export connects their own model and asks their own ` +
          `questions; your ${chat} would be waiting in the panel when they did.`,
      );
    }

    this.presetList?.replaceChildren(
      ...(this.options?.presets ?? []).map((preset) => this._presetRow(preset)),
    );
    this._select(this.options?.default ?? DEFAULT_PRESET);
  }

  _presetRow(preset) {
    const output = preset.output ?? {};
    // What it *is*, not what it is called. "1204×720 at 15 fps" is the sentence someone weighing
    // quality against size is actually reading.
    //
    // Both dimensions, never "720p". A window recording is whatever shape that window was — the
    // one this was built against is 2560×1532 — and calling that "1532p" borrows a label from
    // broadcast resolutions it has nothing to do with. It also hides the half that decides the
    // file size, since a 2560-wide 720p frame is twice a 1280-wide one.
    const shape = preset.audio_only
      ? "No video"
      : `${output.width || "—"}×${output.height || "—"} at ${Math.round(output.frame_rate) || "—"} fps`;

    const input = el("input", {
      attrs: { type: "radio", name: "export-preset", value: preset.id },
    });
    input.addEventListener("change", () => this._select(preset.id));

    return el("label", {
      className: "export__preset",
      attrs: { "data-export-preset": preset.id },
      children: [
        input,
        el("span", {
          className: "export__preset-text",
          children: [
            el("span", {
              className: "export__preset-head",
              children: [
                el("span", { className: "export__preset-name", text: preset.label }),
                el("span", {
                  className: "export__preset-size",
                  text: sizeRange(preset.estimate),
                }),
              ],
            }),
            el("span", { className: "export__preset-hint", text: preset.hint }),
            el("span", {
              className: "export__preset-shape",
              text: preset.estimate?.exact
                ? `${shape} · exactly as recorded`
                : `${shape} · about ${formatDuration(preset.estimate?.seconds ?? 0)} to encode`,
            }),
          ],
        }),
      ],
    });
  }

  _select(presetId) {
    this.preset = presetId;
    for (const row of this.presetList?.querySelectorAll("[data-export-preset]") ?? []) {
      const chosen = row.dataset.exportPreset === presetId;
      row.classList.toggle("export__preset--chosen", chosen);
      const input = $("input", row);
      if (input) input.checked = chosen;
    }
  }

  // -- running ----------------------------------------------------------------------

  async start() {
    // **A dialog that was never shown has no session to export.** Both pages construct one at load
    // and the buttons live in shared markup, so a stray click can reach a controller that is not
    // the open one — which produced `POST /api/sessions//export/start` and a 404 nobody could act
    // on. Refusing here keeps a request that cannot succeed off the wire.
    if (!this.key || !this.isOpen) return;
    this._refuse("");
    try {
      const body = await api.startExport(this.key, this.preset, Boolean(this.includeChat?.checked));
      exportJob.set(body.job);
      this._showProgress();
    } catch (error) {
      this._refuse(
        error instanceof ApiError ? error.message : "The export could not be started.",
      );
    }
  }

  async cancel() {
    if (!this.key) return;
    try {
      await api.cancelExport(this.key);
    } catch {
      // A cancel that fails is a job that already finished, which is what the next frame says.
    }
  }

  renderProgress() {
    if (!this.root || this.root.hidden || !exportJob.covers(this.key)) return;

    const percent = Math.round(exportJob.progress * 100);
    if (this.overallFill) this.overallFill.style.width = `${percent}%`;
    setText(this.overallPercent, `${percent}%`);
    setText(
      this.overallTime,
      exportJob.isRunning && exportJob.remainingSeconds > 0
        ? `about ${formatDuration(exportJob.remainingSeconds)} left`
        : "",
    );

    this.stageList?.replaceChildren(...exportJob.stages.map((stage) => this._stageRow(stage)));

    toggle(this.cancelButton, exportJob.isRunning);
    toggle(this.startButton, !exportJob.isRunning && !exportJob.isDone);
    toggle(this.download, exportJob.isDone);
    if (exportJob.isDone && this.download) {
      this.download.href = api.exportResultUrl(this.key);
    }

    if (exportJob.isDone) {
      // The promise against the result, side by side. It is the one thing worth saying at the end
      // of a wait someone agreed to on the strength of an estimate.
      setText(
        this.result,
        `${size(exportJob.outputBytes)}, against ${size(exportJob.estimatedBytes)} estimated. ` +
          `Took ${formatDuration(exportJob.elapsedSeconds)}.`,
      );
      toggle(this.result, true);
    } else if (exportJob.hasFailed) {
      setText(this.result, exportJob.error || "The export failed.");
      toggle(this.result, true);
    } else {
      toggle(this.result, false);
    }
  }

  _stageRow(stage) {
    const percent = Math.round((stage.progress ?? 0) * 100);
    const detail =
      stage.state === "running" && stage.remaining_s > 0
        ? `${percent}% · about ${formatDuration(stage.remaining_s)} left`
        : stage.detail || "";

    return el("li", {
      className: `export__stage export__stage--${stage.state}`,
      children: [
        el("span", { className: "export__stage-label", text: stage.label }),
        el("span", {
          className: "export__bar export__bar--stage",
          children: [
            el("span", {
              className: "export__bar-fill",
              attrs: { style: `width: ${stage.state === "skipped" ? 0 : percent}%` },
            }),
          ],
        }),
        el("span", {
          className: "export__stage-detail",
          text: stage.state === "skipped" ? stage.detail || "Skipped" : detail,
        }),
      ],
    });
  }

  // -- the two faces ----------------------------------------------------------------

  _showChoosing() {
    toggle(this.choose, true);
    toggle(this.progress, false);
    toggle(this.startButton, true);
    toggle(this.cancelButton, false);
    toggle(this.download, false);
    toggle(this.result, false);
    this._refuse("");
  }

  _showProgress() {
    toggle(this.choose, false);
    toggle(this.progress, true);
    // A refusal left standing beside a finished export says something untrue about it. The same
    // rule the stall banner follows: a message is retracted when it stops being the case.
    this._refuse("");
    this.renderProgress();
  }

  _refuse(message) {
    setText(this.refusal, message);
    toggle(this.refusal, Boolean(message));
  }
}
