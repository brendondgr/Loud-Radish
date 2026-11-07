/**
 * The panel of options, verdicts and notes beside a lab page (D-080).
 *
 * Drawn from the lab's review spec, so an option added there appears here, applies to the page,
 * and is named in the export without anything else changing. Every control applies at once; the
 * page redraws behind the panel so a choice is judged by looking, not by imagining. Shared by
 * every lab, which is why each function is handed the review it works on.
 */

import { el } from "../core/dom.js";
import { DECISIONS, feedbackMarkdown } from "./feedback.js";
import { iconNode } from "./icons.js";

const VERDICTS = [
  ["love", "heart", "Love it"],
  ["ok", "up", "It's OK"],
  ["no", "down", "Not this one"],
];

/** Love / OK / No for one layout, plus a one-line note. Used here and in the toolbar. */
export function verdictButtons(review, layoutId, { withNote = false } = {}) {
  const { lab } = review;
  const current = lab.verdicts[layoutId] ?? {};
  const row = el("div", { className: "lz-verdict" });
  for (const [score, icon, label] of VERDICTS) {
    const button = el("button", {
      className: `lz-verdict__btn lz-verdict__btn--${score}`,
      attrs: { type: "button", "aria-pressed": String(current.score === score), title: label, "aria-label": label },
      children: [iconNode(icon, { size: 14 })],
    });
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      review.setVerdict(layoutId, score);
    });
    row.append(button);
  }
  if (withNote) {
    const note = el("input", {
      className: "lz-input lz-verdict__note",
      attrs: { type: "text", placeholder: "Why?", value: current.note ?? "", "aria-label": "Note on this layout" },
    });
    note.addEventListener("change", () => review.setVerdictNote(layoutId, note.value));
    row.append(note);
  }
  return row;
}

function chips(review, option, value) {
  const multi = option.kind === "multi";
  return el("div", {
    className: "lz-chips",
    attrs: { role: multi ? "group" : "radiogroup", "aria-label": option.label },
    children: option.choices.map((choice) => {
      const on = multi ? value.includes(choice.id) : value === choice.id;
      const chip = el("button", {
        className: "lz-chip",
        text: choice.label,
        attrs: {
          type: "button",
          role: multi ? null : "radio",
          "aria-checked": multi ? null : String(on),
          "aria-pressed": multi ? String(on) : null,
          title: choice.hint ?? null,
        },
      });
      chip.addEventListener("click", () => {
        if (!multi) return review.setSetting(option.id, choice.id);
        const next = on ? value.filter((v) => v !== choice.id) : [...value, choice.id];
        return review.setSetting(option.id, next);
      });
      return chip;
    }),
  });
}

function control(review, option) {
  const value = review.lab.settings[option.id];
  if (option.kind === "toggle") {
    const input = el("input", { attrs: { type: "checkbox", checked: value } });
    input.addEventListener("change", () => review.setSetting(option.id, input.checked));
    return el("label", { className: "lz-toggle", children: [input, el("span", { text: option.label })] });
  }
  return el("div", {
    className: "lz-option",
    children: [
      el("p", { className: "lz-option__label", text: option.label }),
      chips(review, option, value),
      option.hint ? el("p", { className: "lz-hint", text: option.hint }) : null,
    ],
  });
}

function layoutVerdicts(review) {
  const { lab } = review;
  return el("div", {
    className: "lz-layouts",
    children: review.spec.layouts.map((layout) =>
      el("div", {
        className: "lz-layout",
        attrs: { "data-current": String(lab.settings.layout === layout.id) },
        children: [
          el("p", { className: "lz-layout__name", text: layout.label }),
          el("p", { className: "lz-hint", text: layout.hint }),
          verdictButtons(review, layout.id, { withNote: true }),
        ],
      }),
    ),
  });
}

function noteField(review, section) {
  const existing = review.lab.notes[section.id] ?? "";
  const area = el("textarea", {
    className: "lz-input lz-note",
    text: existing,
    attrs: { rows: "2", placeholder: `What you like or don't about ${section.label.toLowerCase()}…` },
  });
  area.addEventListener("change", () => review.setNote(section.id, area.value));
  return el("details", {
    className: "lz-note__wrap",
    attrs: { open: existing ? true : null },
    children: [el("summary", { text: existing ? "Your note" : "Add a note" }), area],
  });
}

function section(review, def, openIds) {
  return el("details", {
    className: "lz-section",
    attrs: { open: openIds.has(def.id) ? true : null, "data-section": def.id },
    children: [
      el("summary", { className: "lz-section__head", text: def.label }),
      el("div", {
        className: "lz-section__body",
        children: [
          ...def.options.map((option) => control(review, option)),
          def.id === "layout" ? layoutVerdicts(review) : null,
          noteField(review, def),
        ],
      }),
    ],
  });
}

function combos(review) {
  const { lab } = review;
  const name = el("input", { className: "lz-input", attrs: { type: "text", placeholder: "Name this combination" } });
  const add = el("button", { className: "lz-button", text: "Save", attrs: { type: "button" } });
  add.addEventListener("click", () => review.saveCombo(name.value.trim()));
  const list = el("ol", {
    className: "lz-combos",
    children: lab.combos.map((combo, index) => {
      const apply = el("button", { className: "lz-link", text: combo.name, attrs: { type: "button", title: "Use this combination" } });
      apply.addEventListener("click", () => review.applySettings(combo.settings));
      const remove = el("button", {
        className: "lz-link lz-link--muted",
        attrs: { type: "button", "aria-label": `Remove ${combo.name}` },
        children: [iconNode("close", { size: 12 })],
      });
      remove.addEventListener("click", () => review.removeCombo(index));
      return el("li", { children: [apply, remove] });
    }),
  });
  return el("section", {
    className: "lz-block",
    children: [
      el("h3", { className: "lz-block__title", text: "Combinations" }),
      el("p", { className: "lz-hint", text: "Keep the arrangements worth comparing; they go in the export." }),
      el("div", { className: "lz-row", children: [name, add] }),
      lab.combos.length ? list : null,
    ],
  });
}

function exporter(review, report, count) {
  const { lab } = review;
  const general = el("textarea", {
    className: "lz-input lz-note",
    text: lab.general ?? "",
    attrs: { rows: "3", placeholder: "Anything else — what to keep, what to drop, what's missing…" },
  });
  general.addEventListener("change", () => review.setGeneral(general.value));

  const copy = el("button", {
    className: "lz-button lz-button--primary",
    attrs: { type: "button" },
    children: [iconNode("copy", { size: 14 }), el("span", { text: "Copy feedback" })],
  });
  copy.addEventListener("click", async () => {
    review.setGeneral(general.value);
    const text = feedbackMarkdown(review, { count: count() });
    try {
      await navigator.clipboard.writeText(text);
      report("Feedback copied — paste it into the chat.");
    } catch {
      preview.open = true;
      report("Couldn't reach the clipboard; select the text below and copy it.");
    }
  });
  const download = el("button", { className: "lz-button", text: "Download .md", attrs: { type: "button" } });
  download.addEventListener("click", () => {
    review.setGeneral(general.value);
    const blob = new Blob([feedbackMarkdown(review, { count: count() })], { type: "text/markdown" });
    const link = el("a", { attrs: { href: URL.createObjectURL(blob), download: "recordings-lab-feedback.md" } });
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });
  const text = el("pre", { className: "lz-preview" });
  const preview = el("details", { className: "lz-note__wrap", children: [el("summary", { text: "Preview the text" }), text] });
  preview.addEventListener("toggle", () => {
    if (preview.open) text.textContent = feedbackMarkdown(review, { count: count() });
  });
  const reset = el("button", { className: "lz-link lz-link--muted", text: "Start the review over", attrs: { type: "button" } });
  reset.addEventListener("click", () => {
    if (window.confirm("Clear every setting, verdict, note and saved combination?")) review.resetReview();
  });

  const decision = el("div", {
    className: "lz-chips",
    attrs: { role: "radiogroup", "aria-label": "Your decision on this round" },
    children: Object.entries(DECISIONS).map(([id, label]) => {
      const chip = el("button", {
        className: `lz-chip lz-chip--${id}`,
        text: label,
        attrs: { type: "button", role: "radio", "aria-checked": String(lab.decision === id) },
      });
      chip.addEventListener("click", () => review.setDecision(id));
      return chip;
    }),
  });

  return el("section", {
    className: "lz-block lz-block--export",
    children: [
      el("h3", { className: "lz-block__title", text: "Send it back" }),
      el("p", { className: "lz-option__label", text: "Your decision on this round" }),
      decision,
      general,
      el("div", { className: "lz-row", children: [copy, download] }),
      preview,
      reset,
    ],
  });
}

/**
 * Draw the panel into `root`. Called again after every change; sections keep their open state.
 *
 * @param {HTMLElement} root
 * @param {{ report: (message: string) => void, count: () => number, onClose: () => void }} hooks
 */
export function renderCustomizer(root, review, { report, count = () => null, onClose }) {
  const openIds = new Set(
    [...root.querySelectorAll(".lz-section[open]")].map((node) => node.dataset.section),
  );
  if (!root.childElementCount) openIds.add("layout");
  const scroll = root.querySelector(".lz-scroll")?.scrollTop ?? 0;

  const close = el("button", {
    className: "lc-btn lc-btn--icon",
    attrs: { type: "button", "aria-label": "Close the panel", title: "Close" },
    children: [iconNode("close", { size: 15 })],
  });
  close.addEventListener("click", onClose);

  const body = el("div", {
    className: "lz-scroll",
    children: [...review.spec.sections.map((def) => section(review, def, openIds)), combos(review), exporter(review, report, count)],
  });
  root.replaceChildren(
    el("header", {
      className: "lz-head",
      children: [
        el("div", {
          children: [
            el("h2", { className: "lz-head__title", text: "Customize" }),
            el("p", { className: "lz-hint", text: "Changes apply as you click. Your review is kept in this browser." }),
          ],
        }),
        close,
      ],
    }),
    body,
  );
  body.scrollTop = scroll;
}
