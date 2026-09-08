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
 *
 * **The model is typed, and it is also a dropdown (D-067).** It used to be a `<select>` holding
 * only what the last connection test returned, so a model the server did not list — or one the
 * user simply knew the name of — could not be entered at all. It is a combobox now: Get lists the
 * models at the address on screen and opens the list, typing narrows it, and whatever is in the
 * field when it changes is what gets saved. Get asks the server what it *offers* and does not care
 * what is currently configured; the connection test is what judges the configured model.
 */

import { $, $$, el, setText } from "../../core/dom.js";
import { config } from "../../stores/config.js";
import { api } from "../../transport/api.js";
import { Combobox } from "./combobox.js";

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

    this.getButtons = $$("[data-llm-get]", root);
    this.providerSelect = $("[data-llm-provider]", root) ?? $("#llm-provider", root);
    this.privacyNote = $("[data-llm-privacy]", root);
    this.keyInput = $("[data-llm-key]", root);
    this.keyNote = $("[data-llm-key-note]", root);
    /** Both mode panels have their own model field, suggestion list and result line; only one is
     *  ever visible. */
    this.modelInputs = $$("[data-llm-model-input]", root);
    this.modelBoxes = new Map(
      $$("[data-llm-model-box]", root).map((box) => [
        box.dataset.llmMode,
        new Combobox(box, { emptyText: "Nothing listed yet — press Get" }),
      ])
    );
    this.results = $$("[data-llm-result]", root);

    for (const button of $$("[data-llm-test]", root)) {
      button.addEventListener("click", () => this.test());
    }
    for (const button of this.getButtons) {
      button.addEventListener("click", () => this.getModels());
    }
    for (const input of this.modelInputs) {
      input.addEventListener("change", () => this.selectModel(input));
    }
    $("[data-llm-key-save]", root)?.addEventListener("click", () => this.storeKey());
    $("[data-llm-key-clear]", root)?.addEventListener("click", () => this.clearKey());
  }

  async load() {
    try {
      const body = await api.llmConfig();
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

  /**
   * Put the configured model in both fields, and — when a list is given — the server's models
   * in the visible mode's dropdown.
   *
   * The field's own value is never replaced by the list: a name the user typed that the server did
   * not list is still the name they meant. Only what the dropdown offers changes, and it opens
   * when asked to, so a Get shows its result rather than filing it away.
   */
  renderModelOptions(models = null, { open = false } = {}) {
    const mode = config.get("llm.mode") === "api" ? "api" : "local";
    for (const input of this.modelInputs) {
      const path = input.dataset.llmMode === "api" ? "llm.api.model" : "llm.local.model";
      // Leave a field alone while it is being typed into: the store is behind the keyboard.
      if (document.activeElement !== input) input.value = config.get(path) ?? "";
    }
    if (!models) return;
    this.modelBoxes.get(mode)?.setOptions(models, { open });
  }

  renderResult(result, message) {
    for (const node of this.results) {
      node.setAttribute("data-result", result);
      setText(node, `${RESULT_LABEL[result] ?? "Result"} — ${message}`);
    }
  }

  // -- actions --------------------------------------------------------------------

  /**
   * List the models the address on screen offers, into the model dropdown, and open it.
   *
   * Read from the form, not the store, because the address being listed is the one just typed —
   * and through the listing route rather than the connection test, because the test refuses when
   * the *configured* model is not at that address. That is the right answer for a test and the
   * wrong one for the button whose job is to find out what can be configured.
   */
  async getModels() {
    this.renderResult("connected", "Listing models…");
    try {
      const body = await api.llmModelsAt(this._overrides());
      const models = (body.models ?? []).map((model) => model.id ?? String(model));
      if (!models.length && body.note) {
        this.renderResult("no_server", body.note);
        this.renderModelOptions([]);
        return;
      }
      this.renderModelOptions(models, { open: true });
      const where = body.endpoint || this._fieldValue("llm.local.endpoint") || "the server";
      this.renderResult(
        "connected",
        models.length
          ? `${models.length} model${models.length === 1 ? "" : "s"} at ${where}. Pick one, or type a name.`
          : `Nothing listed at ${where}. Type the model name yourself.`
      );
    } catch (error) {
      this.renderResult("server_error", error.message);
    }
  }

  async selectModel(input) {
    const path = input.dataset.llmMode === "api" ? "llm.api.model" : "llm.local.model";
    const value = input.value.trim();
    input.value = value;
    try {
      await config.patch({ [path]: value });
      this.onChanged?.();
      this.onStatus?.(value ? `Assistant set to ${value}.` : "Model cleared.", "ok");
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
    this.renderResult("connected", "Testing…");
    try {
      const outcome = await api.testLlm(this._overrides());
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

  /** The configuration as it is on screen, unsaved fields included. */
  _overrides() {
    return {
      mode: config.get("llm.mode"),
      local: {
        endpoint: this._fieldValue("llm.local.endpoint") ?? config.get("llm.local.endpoint"),
        model: this._modelOnScreen("local") ?? config.get("llm.local.model"),
      },
      api: {
        provider: config.get("llm.api.provider"),
        model: this._modelOnScreen("api") ?? config.get("llm.api.model"),
        base_url: this._fieldValue("llm.api.base_url") || null,
      },
    };
  }

  _modelOnScreen(mode) {
    const input = this.modelInputs.find((item) => item.dataset.llmMode === mode);
    const value = input?.value?.trim();
    return value || null;
  }

  _fieldValue(path) {
    return $(`[data-config='${path}']`, this.root)?.value ?? null;
  }
}
