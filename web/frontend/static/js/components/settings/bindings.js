/**
 * Declarative binding between form controls and backend configuration.
 *
 * A control carries `data-config="audio.gain_db"` and this module does the rest: read the value out
 * of the store on open, write it back on change, and re-render from whatever the backend resolved.
 * The alternative — a handler per field — is roughly forty near-identical functions, and forty
 * places for the dotted path and the coercion to drift apart.
 *
 * **Why `change` and not `input`.** `change` fires on blur for text, on release for a slider, and
 * immediately for a checkbox or a select. That is exactly the moment a value is meant, rather than
 * every intermediate keystroke — which for a number field would send `4`, `40`, `400` on the way to
 * `4000`, each one clamped and written back into the field the user is still typing in.
 */

import { $$ } from "../../core/dom.js";
import { config } from "../../stores/config.js";

/** Read the value a control currently holds, coerced to the type the backend expects. */
export function valueOf(control) {
  if (control.type === "checkbox") return control.checked;

  const raw = control.value;
  if (control.type === "number" || control.type === "range") {
    if (raw === "") return control.hasAttribute("data-config-nullable") ? null : 0;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  if (raw === "" && control.hasAttribute("data-config-nullable")) return null;
  return raw;
}

/** Write a configuration value into a control. */
export function applyValue(control, value) {
  if (control.type === "checkbox") {
    control.checked = Boolean(value);
    return;
  }
  control.value = value === null || value === undefined ? "" : String(value);
}

/** Set every bound control under `root` from the store. */
export function hydrate(root) {
  for (const control of $$("[data-config]", root)) {
    applyValue(control, config.get(control.dataset.config));
    syncOutput(control);
  }
  refreshDependants(root);
}

/**
 * Mirror a slider's value into its `<output>`, so a range control is not a mystery.
 *
 * A range input with no visible value is unusable for anything numeric — the user can see roughly
 * where the handle sits and nothing else.
 */
export function syncOutput(control) {
  const id = control.dataset.configOutput;
  if (!id) return;
  const output = document.getElementById(id);
  if (!output) return;
  const suffix = control.dataset.configSuffix ?? "";
  output.textContent = `${control.value}${suffix}`;
}

/**
 * Show or hide anything with `data-when="path=value"`.
 *
 * This is what makes the mode switches work: the local endpoint fields appear only in local mode,
 * the file picker only when the source is a file. Several alternatives are accepted, separated by
 * `|`, so one attribute covers "microphone or loopback".
 */
export function refreshDependants(root) {
  for (const node of $$("[data-when]", root)) {
    const [path, expected = ""] = node.dataset.when.split("=");
    const actual = String(config.get(path.trim()) ?? "");
    const accepted = expected.split("|").map((value) => value.trim());
    node.hidden = !accepted.includes(actual);
  }
}

/**
 * Start writing changes back to the backend.
 *
 * @param {Element} root container holding the bound controls
 * @param {object} handlers
 * @param {(response: object, control: Element) => void} handlers.onApplied
 * @param {(error: Error, control: Element) => void} handlers.onError
 * @param {(consequence: string) => Promise<boolean>|boolean} handlers.confirm asked before a
 *   change that would interrupt a running session. Returning false abandons the change and puts
 *   the control back to what the backend still holds.
 */
export function attach(root, { onApplied, onError, confirm } = {}) {
  root.addEventListener("change", async (event) => {
    const control = event.target.closest("[data-config]");
    if (!control || !root.contains(control)) return;

    const path = control.dataset.config;
    const previous = config.get(path);
    const next = valueOf(control);
    if (previous === next) return;

    syncOutput(control);
    control.setAttribute("aria-busy", "true");

    try {
      const response = await config.patch({ [path]: next });

      // The consequence is asked about *after* the value is known to be valid but before the user
      // moves on. Asking first would mean warning about changes the backend then rejects.
      if (response.hot_swap !== "live" && confirm) {
        const proceed = await confirm(response.consequence, path);
        if (!proceed) {
          await config.patch({ [path]: previous });
          applyValue(control, previous);
          syncOutput(control);
          refreshDependants(root);
          return;
        }
      }

      // Re-read rather than trust: the backend may have clamped the value, and the field must show
      // what the pipeline will actually use.
      applyValue(control, config.get(path));
      syncOutput(control);
      refreshDependants(root);
      onApplied?.(response, control);
    } catch (error) {
      applyValue(control, previous);
      syncOutput(control);
      onError?.(error, control);
    } finally {
      control.removeAttribute("aria-busy");
    }
  });

  // Sliders update their readout continuously even though the write waits for `change`, so the
  // number under the handle tracks the handle.
  root.addEventListener("input", (event) => {
    const control = event.target.closest("[data-config][data-config-output]");
    if (control) syncOutput(control);
  });
}
