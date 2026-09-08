/**
 * A text field that is also a dropdown (D-067).
 *
 * Built by hand rather than with `<datalist>`, because a datalist is a suggestion the browser shows
 * when it feels like it — on some it appears only after typing, on others only on a double-click,
 * and on none of them does it look like a dropdown. The assistant's model field needs both halves
 * to be obvious: type anything, or open the list the server offered and pick from it.
 *
 * The markup it drives:
 *
 *     <div data-combobox>
 *       <input role="combobox" data-combobox-input aria-controls="<list id>">
 *       <button data-combobox-toggle>▾</button>
 *       <ul role="listbox" id="<list id>" data-combobox-list hidden></ul>
 *     </div>
 *
 * Picking an option puts it in the field and fires the field's own `change` event, so whoever
 * saves typed values saves picked ones the same way. Typing while the list is open narrows it.
 */

import { $, el } from "../../core/dom.js";

export class Combobox {
  constructor(root, { emptyText = "Nothing listed yet" } = {}) {
    this.root = root;
    this.input = $("[data-combobox-input]", root);
    this.toggle = $("[data-combobox-toggle]", root);
    this.list = $("[data-combobox-list]", root);
    this.emptyText = emptyText;
    this.options = [];
    this.active = -1;

    this.input.addEventListener("keydown", (event) => this.onKey(event));
    this.input.addEventListener("input", () => {
      if (this.isOpen) this.render(this.input.value);
    });
    this.input.addEventListener("blur", () => this.close());
    // `mousedown` rather than `click`, and prevented: a click on the toggle would first blur the
    // input, which closes the list, and then open it again — a flicker that reads as "nothing".
    this.toggle?.addEventListener("mousedown", (event) => {
      event.preventDefault();
      this.input.focus();
      if (this.isOpen) this.close();
      else this.open();
    });
  }

  get isOpen() {
    return !this.list.hidden;
  }

  /** Replace what the list offers. Opens it when asked, so a fetch shows its result at once. */
  setOptions(options, { open = false } = {}) {
    this.options = [...options];
    if (open) {
      this.input.focus();
      this.open(true);
    } else if (this.isOpen) {
      this.render(this.input.value);
    }
  }

  open(showAll = false) {
    this.render(showAll ? "" : this.input.value);
    this.list.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  close() {
    if (!this.isOpen) return;
    this.list.hidden = true;
    this.active = -1;
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
  }

  /** Draw the options matching `filter` — all of them when it is empty or matches nothing. */
  render(filter) {
    const needle = filter.trim().toLowerCase();
    let shown = needle
      ? this.options.filter((option) => option.toLowerCase().includes(needle))
      : this.options;
    if (!shown.length) shown = this.options;
    this.shown = shown;
    this.active = -1;

    if (!shown.length) {
      this.list.replaceChildren(
        el("li", {
          className: "combobox__option",
          text: this.emptyText,
          attrs: { role: "option", "aria-disabled": "true", "aria-selected": "false" },
        })
      );
      return;
    }

    this.list.replaceChildren(
      ...shown.map((option, index) => {
        const item = el("li", {
          className: "combobox__option",
          text: option,
          attrs: {
            role: "option",
            id: `${this.list.id}-${index}`,
            "aria-selected": String(option === this.input.value),
          },
        });
        // Prevented so the input keeps focus; the pick happens on the click that follows.
        item.addEventListener("mousedown", (event) => event.preventDefault());
        item.addEventListener("click", () => this.pick(option));
        return item;
      })
    );
  }

  pick(value) {
    this.input.value = value;
    this.close();
    this.input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  onKey(event) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!this.isOpen) this.open(true);
        this.move(1);
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!this.isOpen) this.open(true);
        this.move(-1);
        break;
      case "Enter":
        if (this.isOpen && this.active >= 0) {
          event.preventDefault();
          this.pick(this.shown[this.active]);
        }
        break;
      case "Escape":
        if (this.isOpen) {
          // Stopped here: the settings dialog closes on Escape too, and one press should close
          // the list, not the list and the dialog behind it.
          event.preventDefault();
          event.stopPropagation();
          this.close();
        }
        break;
      default:
        break;
    }
  }

  move(step) {
    const count = this.shown?.length ?? 0;
    if (!count) return;
    this.active = (this.active + step + count) % count;
    const items = this.list.querySelectorAll("[role='option']");
    items.forEach((item, index) => {
      item.setAttribute("aria-selected", String(index === this.active));
    });
    const current = items[this.active];
    this.input.setAttribute("aria-activedescendant", current.id);
    current.scrollIntoView({ block: "nearest" });
  }
}
