/**
 * The main-screen lab, `/labs/main/` (D-080): the live application's screen redrawn in the style
 * chosen in the recordings lab, in four arrangements, with the same customize panel and export.
 *
 * It shows one of the user's past recordings as though it were open on the main screen — the one
 * with the most conversation, preferring one with video — because the style has to be judged
 * against a real transcript and real answers, not placeholder text. Controls are drawn, not wired.
 */

import { $, el } from "../core/dom.js";
import { api, ApiError, get } from "../transport/api.js";
import { renderCustomizer, verdictButtons } from "./customizer.js";
import { cardTitle } from "./describe.js";
import { DEFAULTS, LAYOUTS, SECTIONS } from "./main-options.js";
import { assistantPane, banner, header, health, monitor, transcriptPane } from "./main-view.js";
import { createReview } from "./review.js";

const review = createReview({
  storageKey: "loud-radish.labs.main.v1",
  sections: SECTIONS,
  defaults: DEFAULTS,
  layouts: LAYOUTS,
  title: "Main screen lab feedback",
  page: "/labs/main/",
});
const { lab } = review;

const root = $("[data-lab]");
const stage = $("[data-lab-stage]");
const panel = $("[data-lab-panel]");
const status = $("[data-lab-status]");
const labBar = $("[data-lab-bar]");

const view = { content: null, error: "", panelOpen: false, assistantOpen: false };

let statusTimer = 0;
function report(message) {
  status.textContent = message;
  status.hidden = false;
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => (status.hidden = true), 4000);
}

// -- what to show ------------------------------------------------------------------------------------

/** The recording with the most conversation, preferring one with video; else the longest. */
function showcase(sessions) {
  const usable = sessions.filter((s) => !s.orphan && s.segments > 0);
  const score = (s) => [s.chat_messages > 0 ? 1 : 0, s.media?.video ? 1 : 0, s.chat_messages, s.words];
  return usable.sort((a, b) => {
    const [x, y] = [score(a), score(b)];
    for (let i = 0; i < x.length; i += 1) if (x[i] !== y[i]) return y[i] - x[i];
    return 0;
  })[0];
}

async function load() {
  try {
    const listing = await api.sessions();
    const session = showcase(listing.sessions ?? []);
    if (!session) throw new Error("There is no recording with a transcript to show yet.");
    const key = encodeURIComponent(session.key);
    const [detail, glances, quick] = await Promise.all([
      get(`/api/sessions/${key}?limit=160`),
      get(`/api/labs/sessions/glance?keys=${key}`).catch(() => ({ glances: {} })),
      api.quickActions().catch(() => ({ actions: [] })),
    ]);
    const glance = glances.glances?.[session.key];
    view.content = {
      session,
      detail,
      quick: quick.actions ?? [],
      title: cardTitle(session, glance, "keywords").text,
      model: "Connected",
    };
  } catch (error) {
    view.error = error instanceof ApiError || error instanceof Error ? error.message : "Nothing could be loaded.";
  }
  render();
}

// -- drawing -------------------------------------------------------------------------------------------

function applySettingsToRoot() {
  const s = lab.settings;
  Object.assign(root.dataset, {
    layout: s.layout,
    assist: s.assistWidth,
    density: s.density,
    record: s.record,
    health: s.layout === "dock" ? "header" : s.health,
    font: s.font,
    textSize: s.textSize,
    stamps: s.stamps,
    toolbar: s.toolbar,
    bubbles: s.bubbles,
    monitor: s.monitor,
    banner: s.banner,
    accent: s.accent,
    surface: s.surface,
    radius: s.radius,
    panel: String(view.panelOpen),
    assistantOpen: String(view.assistantOpen),
  });
}

const hooks = () => ({
  logo: root.dataset.logo,
  panelOpen: view.panelOpen,
  onTogglePanel: togglePanel,
  onToggleAssistant: () => {
    view.assistantOpen = !view.assistantOpen;
    render();
  },
});

function render() {
  applySettingsToRoot();
  drawLabBar();
  if (view.panelOpen) drawPanel();
  if (!view.content) {
    stage.replaceChildren(el("p", { className: "lm-loading", text: view.error || "Loading a recording…" }));
    return;
  }
  // The dock carries the level, speed and lag itself.
  const settings = { ...lab.settings, health: lab.settings.layout === "dock" ? "header" : lab.settings.health };
  const { content } = view;
  const transcript = transcriptPane(content, settings);
  if (settings.monitor === "pip") transcript.append(el("div", { className: "lm-pip", children: [monitor(content)] }));

  const parts = [
    header(content, settings, hooks()),
    settings.banner === "strip" ? banner(content) : null,
    el("div", { className: "lm-body", children: [transcript, assistantPane(content, settings, hooks())] }),
    settings.health === "bar" ? el("footer", { className: "lm-statusbar lm-region", children: [health(content)] }) : null,
  ];
  stage.replaceChildren(...parts.filter(Boolean));
}

/**
 * The lab's own controls — layout, verdict, Customize — kept apart from the drawn screen so they
 * are never mistaken for part of the design being judged.
 */
function drawLabBar() {
  const layouts = LAYOUTS.map((layout, index) => {
    const button = el("button", {
      className: "lt-layout",
      text: layout.label,
      attrs: { type: "button", role: "radio", "aria-checked": String(lab.settings.layout === layout.id), title: `${layout.hint} (${index + 1})` },
    });
    button.addEventListener("click", () => review.setSetting("layout", layout.id));
    return button;
  });
  const customize = el("button", {
    className: "lt-customize",
    attrs: { type: "button", "aria-pressed": String(view.panelOpen), title: "Customize (C)" },
    text: "Customize",
  });
  customize.addEventListener("click", togglePanel);
  labBar.replaceChildren(
    el("span", { className: "lt-lab", text: "Lab" }),
    el("div", { className: "lt-layouts", attrs: { role: "radiogroup", "aria-label": "Arrangement" }, children: layouts }),
    verdictButtons(review, lab.settings.layout),
    customize,
  );
}

function togglePanel() {
  view.panelOpen = !view.panelOpen;
  panel.hidden = !view.panelOpen;
  render();
}

function drawPanel() {
  renderCustomizer(panel, review, { report, onClose: togglePanel });
}

// -- wiring --------------------------------------------------------------------------------------------

review.subscribe((changed) => {
  if (changed.every((key) => ["notes", "general", "verdictNote"].includes(key))) return;
  render();
});

stage.addEventListener("click", (event) => {
  const control = event.target.closest("[data-mock]");
  if (control) report(`Look-only: "${control.dataset.mock}" is drawn here, not wired. It works on the real main screen.`);
});

document.addEventListener("keydown", (event) => {
  if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const index = Number(event.key) - 1;
  if (index >= 0 && index < LAYOUTS.length) review.setSetting("layout", LAYOUTS[index].id);
  if (event.key === "c") togglePanel();
  if (event.key === "Escape" && view.assistantOpen) {
    view.assistantOpen = false;
    render();
  }
});

render();
void load();
