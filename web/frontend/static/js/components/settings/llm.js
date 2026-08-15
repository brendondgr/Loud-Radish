/**
 * The Assistant tab: which language model answers, and whether it can be reached.
 *
 * Two things here are worth stating.
 *
 * **The test probes what is on screen.** The endpoint field is where a wrong address gets fixed, so
 * testing the *saved* value would make the button useless exactly when it is needed. The unsaved
 * form values go with the request.
 *
 * **A credential goes one way only.** The key field writes; nothing reads it back. What comes back
 * is whether a key is present and where it would be read from — never its value.
 */

import { $, $$, el, setText } from "../../core/dom.js";
import { config } from "../../stores/config.js";
import { api } from "../../transport/api.js";

/** Human wording for each of the four connection-test outcomes. */
const RESULT_LABEL = {
  connected: "Connected",
  no_server: "Nothing is listening",
  auth_rejected: "Key rejected",
  server_error: "Server problem",
};

export class LlmSettings {
  constructor(root, { onStatus, onChanged }) {
    this.root = root;
    this.onStatus = onStatus;
    this.onChanged = onChanged;

    this.presetSelect = $("[data-llm-preset]", root);
    this.providerSelect = $("[data-llm-provider]", root) ?? $("#llm-provider", root);
    this.privacyNote = $("[data-llm-privacy]", root);
    this.keyInput = $("[data-llm-key]", root);
    this.keyNote = $("[data-llm-key-note]", root);
    /** Both mode panels have their own model select and result line; only one is ever visible. */
    this.modelSelects = $$("[data-llm-model-select]", root);
    this.results = $$("[data-llm-result]", root);

    for (const button of $$("[data-llm-test]", root)) {
      button.addEventListener("click", () => this.test());
    }
    this.presetSelect?.addEventListener("change", () => this.applyPreset());
    for (const select of this.modelSelects) {
      select.addEventListener("change", () => this.selectModel(select));
    }
    $("[data-llm-key-save]", root)?.addEventListener("click", () => this.storeKey());
    $("[data-llm-key-clear]", root)?.addEventListener("click", () => this.clearKey());
  }

  async load() {
    try {
      const body = await api.llmConfig();
      this.renderPresets(body.local.presets ?? []);
      this.renderProviders(body.api.providers ?? []);
      this.renderCredential(body.credential);
      this.sync();
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }

  sync() {
    this.renderPrivacy();
    this.renderModelOptions();
  }

  // -- rendering ------------------------------------------------------------------

  renderPresets(presets) {
    if (!this.presetSelect) return;
    this.presetSelect.replaceChildren(
      el("option", { text: "Common…", attrs: { value: "" } }),
      ...presets.map((preset) =>
        el("option", { text: preset.name, attrs: { value: preset.endpoint } })
      )
    );
  }

  renderProviders(providers) {
    if (!this.providerSelect || !providers.length) return;
    const current = config.get("llm.api.provider") ?? "";
    this.providerSelect.replaceChildren(
      ...providers.map((provider) =>
        el("option", { text: provider.name, attrs: { value: provider.id } })
      )
    );
    this.providerSelect.value = current;
    this.providers = providers;
  }

  renderCredential(credential) {
    if (!this.keyNote || !credential) return;
    this.credential = credential;

    if (!credential.writable) {
      const provider = this.providers?.find((entry) => entry.id === credential.provider);
      const variable = provider?.env_var ?? "the provider's API key variable";
      setText(
        this.keyNote,
        credential.present
          ? `Read from ${variable}. Install the credentials extra to store keys here instead.`
          : `No OS credential store is available, so a key cannot be saved from here. Set ${variable} in the environment, or install the credentials extra.`
      );
      if (this.keyInput) this.keyInput.disabled = true;
      return;
    }

    setText(
      this.keyNote,
      credential.present
        ? "A key is stored in your OS credential store. Enter a new one to replace it."
        : "Stored in your OS credential store, never in the config file and never sent to the browser."
    );
  }

  renderPrivacy() {
    if (!this.privacyNote) return;
    setText(
      this.privacyNote,
      config.get("llm.mode") === "local"
        ? "Nothing leaves this machine, provided the address points at one on your own network."
        : "Transcript excerpts are sent to this provider. The header will say so while this is active."
    );
  }

  /** Show the configured model in both selects, even before any list has been fetched. */
  renderModelOptions(models = null) {
    for (const select of this.modelSelects) {
      const path = select.dataset.llmMode === "api" ? "llm.api.model" : "llm.local.model";
      const current = config.get(path) ?? "";

      if (models) {
        select.replaceChildren(
          el("option", { text: "No model selected", attrs: { value: "" } }),
          ...models.map((model) => el("option", { text: model, attrs: { value: model } }))
        );
        // A configured model the server did not list is kept as an option rather than dropped:
        // the list may be filtered, and silently clearing the selection would be worse.
        if (current && !models.includes(current)) {
          select.append(el("option", { text: `${current} — not listed`, attrs: { value: current } }));
        }
      } else if (!select.options.length || (current && !select.querySelector(`option[value="${CSS.escape(current)}"]`))) {
        select.replaceChildren(
          el("option", {
            text: current || "Test the connection to list models",
            attrs: { value: current },
          })
        );
      }

      select.value = current;
    }
  }

  renderResult(result, message) {
    for (const node of this.results) {
      node.setAttribute("data-result", result);
      setText(node, `${RESULT_LABEL[result] ?? "Result"} — ${message}`);
    }
  }

  // -- actions --------------------------------------------------------------------

  async applyPreset() {
    const endpoint = this.presetSelect.value;
    if (!endpoint) return;
    try {
      await config.patch({ "llm.local.endpoint": endpoint });
      const field = $("[data-config='llm.local.endpoint']", this.root);
      if (field) field.value = endpoint;
      this.presetSelect.value = "";
      await this.test();
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }

  async selectModel(select) {
    const path = select.dataset.llmMode === "api" ? "llm.api.model" : "llm.local.model";
    try {
      await config.patch({ [path]: select.value });
      this.onChanged?.();
      this.onStatus?.(select.value ? `Assistant set to ${select.value}.` : "Model cleared.", "ok");
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }

  /**
   * Probe the provider using the values currently in the form.
   *
   * The form is read directly rather than from the store because a field the user has typed into
   * but not yet blurred has not been written back yet — and that is precisely the field they are
   * about to test.
   */
  async test() {
    const overrides = {
      mode: config.get("llm.mode"),
      local: {
        endpoint: this._fieldValue("llm.local.endpoint") ?? config.get("llm.local.endpoint"),
        model: config.get("llm.local.model"),
      },
      api: {
        provider: config.get("llm.api.provider"),
        model: config.get("llm.api.model"),
        base_url: this._fieldValue("llm.api.base_url") || null,
      },
    };

    this.renderResult("connected", "Testing…");
    try {
      const outcome = await api.testLlm(overrides);
      this.renderResult(outcome.result, outcome.message);
      if (outcome.result === "connected") this.renderModelOptions(outcome.models ?? []);
      this.onChanged?.();
    } catch (error) {
      this.renderResult("server_error", error.message);
    }
  }

  async storeKey() {
    const value = this.keyInput?.value?.trim();
    if (!value) {
      setText(this.keyNote, "Enter a key first.");
      return;
    }
    try {
      const status = await api.storeLlmCredential(config.get("llm.api.provider"), value);
      // Cleared immediately: there is no reason for a secret to sit in a DOM node after it has
      // been handed over.
      this.keyInput.value = "";
      this.renderCredential(status);
      this.onStatus?.("Key stored.", "ok");
    } catch (error) {
      setText(this.keyNote, error.message);
      this.onStatus?.(error.message, "error");
    }
  }

  async clearKey() {
    try {
      this.renderCredential(await api.clearLlmCredential(config.get("llm.api.provider")));
      this.onStatus?.("Key removed.", "ok");
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }

  _fieldValue(path) {
    return $(`[data-config='${path}']`, this.root)?.value ?? null;
  }
}
