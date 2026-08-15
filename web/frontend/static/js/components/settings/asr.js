/**
 * The Transcription tab: choosing and loading a speech model.
 *
 * Backends this machine cannot run are listed and disabled with the reason attached, rather than
 * hidden (FE §7.2). Hiding them makes a missing optional dependency indistinguishable from a
 * feature that does not exist.
 *
 * Loading is the one genuinely slow operation in the settings dialog — tens of seconds for a large
 * model — so it reports progress from the WebSocket rather than leaving a button greyed out with
 * nothing happening.
 */

import { on } from "../../core/bus.js";
import { $, el, setText, toggle } from "../../core/dom.js";
import { config } from "../../stores/config.js";
import { api } from "../../transport/api.js";
import { ASR_PROGRESS } from "../../transport/events.js";

export class AsrSettings {
  constructor(root, { onStatus }) {
    this.root = root;
    this.onStatus = onStatus;
    /** Every backend the server described, keyed by id, so the model list can follow the engine. */
    this.backends = new Map();

    this.backendSelect = $("[data-asr-backend]", root);
    this.backendNote = $("[data-asr-backend-note]", root);
    this.modelSelect = $("[data-asr-model]", root);
    this.modelNote = $("[data-asr-model-note]", root);
    this.status = $("[data-asr-status]", root);
    this.progress = $("[data-asr-progress]", root);
    this.progressFill = $("[data-asr-progress-fill]", root);

    this.deviceSelect = $("#asr-device", root);
    this.precisionSelect = $("#asr-precision", root);

    this.backendSelect?.addEventListener("change", () => this.onBackendChanged());
    this.modelSelect?.addEventListener("change", () => this.onModelChanged());
    this.deviceSelect?.addEventListener("change", () => this.renderCompute());
    $("[data-asr-load]", root)?.addEventListener("click", () => this.loadModel());
    $("[data-asr-unload]", root)?.addEventListener("click", () => this.unloadModel());

    on(ASR_PROGRESS, (payload) => this.renderProgress(payload));
  }

  async load() {
    try {
      const { backends, current } = await api.asrModels();
      this.backends = new Map(backends.map((backend) => [backend.backend, backend]));
      this.renderBackends(backends);
      this.renderModels();
      this.renderCurrent(current);
    } catch (error) {
      setText(this.backendNote, error.message);
    }
  }

  sync() {
    if (!this.backendSelect) return;
    this.backendSelect.value = config.get("asr.backend") ?? "";
    this.renderModels();
    this.renderCompute();
  }

  /**
   * Disable devices and precisions this machine cannot actually run.
   *
   * `float16` on a CPU-only build is a setting that fails at model load — which happens when the
   * user presses record, long after they chose it. CTranslate2 knows what is supported, so the
   * options that would fail are struck out here with the reason attached rather than offered.
   */
  renderCompute() {
    const compute = this.backends.get(this.backendSelect?.value)?.compute;
    if (!compute || !compute.devices) {
      // A backend that does not answer the question — the mock — constrains nothing.
      for (const select of [this.deviceSelect, this.precisionSelect]) {
        for (const option of select?.options ?? []) option.disabled = false;
      }
      return;
    }

    for (const option of this.deviceSelect?.options ?? []) {
      const supported = compute.devices.includes(option.value);
      option.disabled = !supported;
      option.textContent = option.textContent.replace(/ — not available.*$/, "");
      if (!supported) option.textContent += " — not available on this machine";
    }

    const device = this.deviceSelect?.value ?? "auto";
    const allowed = compute[device] ?? [];
    for (const option of this.precisionSelect?.options ?? []) {
      const supported = allowed.length === 0 || allowed.includes(option.value);
      option.disabled = !supported;
      option.textContent = option.textContent.replace(/ — not supported.*$/, "");
      if (!supported) option.textContent += ` — not supported on ${device}`;
    }

    // A precision that is no longer selectable must not stay selected, or the load fails with a
    // value the interface is still showing as chosen.
    if (this.precisionSelect?.selectedOptions?.[0]?.disabled && allowed.length) {
      this.precisionSelect.value = allowed[0];
      void config.patch({ "asr.precision": allowed[0] });
    }
  }

  // -- rendering ------------------------------------------------------------------

  renderBackends(backends) {
    const current = config.get("asr.backend") ?? "";
    this.backendSelect.replaceChildren(
      ...backends.map((backend) =>
        el("option", {
          text: backend.available ? backend.name : `${backend.name} — unavailable`,
          attrs: { value: backend.backend, disabled: !backend.available },
        })
      )
    );
    this.backendSelect.value = current;
    this.renderBackendNote();
  }

  renderBackendNote() {
    const backend = this.backends.get(this.backendSelect?.value);
    if (!backend) return;
    setText(
      this.backendNote,
      backend.available
        ? `${backend.family}. Runs on ${backend.capabilities.runs_on}.`
        : backend.unavailable_reason
    );
  }

  renderModels() {
    if (!this.modelSelect) return;
    const backend = this.backends.get(this.backendSelect?.value);
    const models = backend?.models ?? [];
    const current = config.get("asr.model") ?? "";

    this.modelSelect.replaceChildren(
      ...models.map((model) =>
        el("option", { text: `${model.name} — ${model.size}`, attrs: { value: model.name } })
      )
    );

    // A model name carried over from another backend is not in this list. Falling back to the
    // first entry keeps the select and the configuration agreeing, rather than showing a blank.
    this.modelSelect.value = models.some((model) => model.name === current)
      ? current
      : (models[0]?.name ?? "");

    const chosen = models.find((model) => model.name === this.modelSelect.value);
    setText(this.modelNote, chosen?.note ?? "");
  }

  renderCurrent(current) {
    if (!current) return;
    const loaded = current.loaded ?? current.state === "ready";
    setText(
      this.status,
      loaded ? `Loaded: ${current.model_id || current.model || "model"}` : "No model loaded."
    );
  }

  renderProgress(payload) {
    if (!this.progress) return;
    const fraction = Number(payload?.progress ?? 0);
    const done = fraction >= 1 || payload?.state === "ready";

    toggle(this.progress, !done);
    if (this.progressFill) this.progressFill.style.width = `${Math.round(fraction * 100)}%`;
    if (payload?.message) setText(this.status, payload.message);
  }

  // -- actions --------------------------------------------------------------------

  async onBackendChanged() {
    try {
      await config.patch({ "asr.backend": this.backendSelect.value });
      this.renderBackendNote();
      this.renderModels();
      // The model list changed with the engine, so the chosen model has to be written too.
      await this.onModelChanged();
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }

  async onModelChanged() {
    const model = this.modelSelect?.value;
    if (!model) return;
    try {
      await config.patch({ "asr.model": model });
      const backend = this.backends.get(this.backendSelect?.value);
      const chosen = backend?.models?.find((entry) => entry.name === model);
      setText(this.modelNote, chosen?.note ?? "");
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }

  async loadModel() {
    const backend = this.backendSelect?.value;
    const model = this.modelSelect?.value;
    if (!backend || !model) return;

    setText(this.status, "Loading…");
    toggle(this.progress, true);
    try {
      const result = await api.loadModel({
        backend,
        model,
        device: config.get("asr.device"),
        precision: config.get("asr.precision"),
      });
      this.renderCurrent(result);
      this.onStatus?.("Model loaded.", "ok");
    } catch (error) {
      setText(this.status, error.message);
      this.onStatus?.(error.message, "error");
    } finally {
      toggle(this.progress, false);
    }
  }

  async unloadModel() {
    try {
      this.renderCurrent(await api.unloadModel());
      this.onStatus?.("Model unloaded — its memory is freed.", "ok");
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }
}
