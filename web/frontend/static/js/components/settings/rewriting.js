/**
 * The Rewriting tab — the instructions both rewrite passes follow (D-068).
 *
 * Almost everything on the panel is declarative and the binding layer handles it. What is left is
 * the part an instruction field needs and a number field does not: **an empty box means the
 * instructions that shipped**, so the box has to show text it is deliberately not storing.
 *
 * That is done with a placeholder rather than by writing the shipped text into the field. The
 * distinction is the whole point of the design. A field seeded with the shipped text would be a
 * stored copy the moment anyone touched it, freezing that installation on today's wording; an
 * empty field with the wording behind it in grey keeps tracking whatever ships next, and Reset
 * gets back to that state by writing `""` rather than by writing the shipped text back in.
 *
 * The state line under each box says which of the two is in force, because a box that looks empty
 * and a box that looks empty because it was reset are the same picture otherwise.
 */

import { $$, setText } from "../../core/dom.js";
import { config } from "../../stores/config.js";

export class RewritingSettings {
  constructor(root, { onStatus } = {}) {
    this.root = root;
    this.onStatus = onStatus;
    this.fields = $$("[data-instructions]", root);

    for (const button of $$("[data-instructions-reset]", root)) {
      button.addEventListener("click", () => this.reset(button.dataset.instructionsReset));
    }
  }

  /** Put the shipped wording behind each empty box, and say which text is in force. */
  sync() {
    for (const field of this.fields) {
      const path = field.dataset.instructions;
      const shipped = config.shipped(path);
      // Only ever the placeholder. Writing it into `value` would store a copy on the next change
      // event, which is exactly the outcome the empty-means-shipped rule exists to avoid.
      field.placeholder = shipped;

      const written = String(config.get(path) ?? "").trim();
      const state = field
        .closest(".field")
        ?.querySelector("[data-instructions-state]");
      if (!state) continue;

      setText(
        state,
        written
          ? "Your own instructions are in force. Reset to go back to the ones that shipped."
          : "Using the instructions that shipped, shown in grey above. Type here to replace them."
      );
      state.dataset.tone = written ? "edited" : "";
    }
  }

  /**
   * Go back to the shipped instructions by clearing the field.
   *
   * Writing `""` rather than writing the shipped text back is deliberate: the empty state is what
   * keeps this installation tracking a later release's wording.
   */
  async reset(path) {
    if (!String(config.get(path) ?? "").trim()) {
      this.onStatus?.("Already using the instructions that shipped.");
      return;
    }

    try {
      await config.patch({ [path]: "" });
      const field = this.fields.find((node) => node.dataset.instructions === path);
      if (field) field.value = "";
      this.sync();
      this.onStatus?.("Back to the instructions that shipped.", "ok");
    } catch (error) {
      this.onStatus?.(error.message, "error");
    }
  }
}
