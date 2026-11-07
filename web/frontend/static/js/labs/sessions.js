/**
 * The recordings lab, `/labs/sessions/` (D-080): today's recordings as cards, in six layouts, with
 * a panel of options and a way to send the user's verdict back as text.
 *
 * A prototype beside the real Recordings page, not a replacement for it. It reads the same
 * `/api/sessions` listing and its buttons do the real things — bring a recording to the main
 * screen, download, open its folder, delete — so a design is judged on the user's own recordings.
 */

import { $, el } from "../core/dom.js";
import { TranscribeConsent } from "../components/transcribe-consent.js";
import { api, ApiError, get } from "../transport/api.js";
import { runAction } from "./actions.js";
import { renderCard } from "./card.js";
import { renderCustomizer } from "./customizer.js";
import { renderDetail } from "./detail.js";
import { cardTitle, groupOf, startedAt } from "./describe.js";
import { LAYOUTS } from "./options.js";
import { lab, setSetting, subscribe } from "./state.js";
import { FILTERS, matchesFilter, renderToolbar } from "./toolbar.js";

const root = $("[data-lab]");
const toolbar = $("[data-lab-toolbar]");
const list = $("[data-lab-list]");
const panel = $("[data-lab-panel]");
const host = $("[data-lab-detail]");
const inspector = $("[data-lab-inspector]");
const status = $("[data-lab-status]");
const consent = new TranscribeConsent($("[data-transcribe-consent]"));

const view = {
  sessions: [],
  query: "",
  filter: "all",
  sort: "newest",
  panelOpen: false,
  selected: "",
  busy: "",
  viewingKey: "",
  error: "",
};
const glances = new Map();

// -- talking to the user ---------------------------------------------------------------------------

let statusTimer = 0;
function report(message) {
  status.textContent = message;
  status.hidden = false;
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => (status.hidden = true), 5000);
}

// -- what is on screen -------------------------------------------------------------------------------

function haystack(session) {
  const glance = glances.get(session.key);
  return [session.title, cardTitle(session, glance, lab.settings.title).text, glance?.opening, glance?.summary, ...(glance?.keywords ?? [])]
    .join(" ")
    .toLowerCase();
}

function visible() {
  const query = view.query.trim().toLowerCase();
  const shown = view.sessions.filter(
    (session) => matchesFilter(session, view.filter) && (!query || haystack(session).includes(query)),
  );
  const time = (session) => startedAt(session)?.getTime() ?? 0;
  if (view.sort === "oldest") shown.sort((a, b) => time(a) - time(b));
  else if (view.sort === "longest") shown.sort((a, b) => (b.duration_seconds ?? 0) - (a.duration_seconds ?? 0));
  else shown.sort((a, b) => time(b) - time(a));
  return shown;
}

function counts() {
  return Object.fromEntries(FILTERS.map(([id]) => [id, view.sessions.filter((s) => matchesFilter(s, id)).length]));
}

function cardContext(session) {
  return {
    settings: lab.settings,
    glance: glances.get(session.key),
    busy: view.busy,
    viewing: session.key === view.viewingKey,
    selected: session.key === view.selected,
    onOpen: openDetail,
    onAction: act,
  };
}

function applySettingsToRoot() {
  const s = lab.settings;
  Object.assign(root.dataset, {
    layout: s.layout,
    size: s.size,
    density: s.density,
    header: s.header,
    sticky: String(s.stickyHeader),
    accent: s.accent,
    surface: s.surface,
    radius: s.radius,
    ratio: s.ratio,
    actions: s.actionStyle,
    media: s.mediaStyle,
    panel: String(view.panelOpen),
  });
}

function renderToolbarNow() {
  const shown = visible();
  renderToolbar(
    toolbar,
    { ...view, shown: shown.length, total: view.sessions.length, counts: counts() },
    {
      onQuery: (query) => {
        view.query = query;
        renderAll();
      },
      onFilter: (filter) => {
        view.filter = filter;
        renderAll();
      },
      onSort: (sort) => {
        view.sort = sort;
        renderAll();
      },
      onTogglePanel: togglePanel,
    },
  );
  return shown;
}

function renderList(shown) {
  const scroll = root.scrollTop;
  const by = lab.settings.layout === "timeline" ? "day" : lab.settings.group;
  const groups = new Map();
  for (const session of shown) {
    const group = groupOf(session, by);
    if (!groups.has(group.key)) groups.set(group.key, { label: group.label, sessions: [] });
    groups.get(group.key).sessions.push(session);
  }

  const sections = lab.settings.layout === "timeline" ? [timeline(groups)] : [...groups.values()].map((group) =>
    el("section", {
      className: "lg-group",
      children: [
        group.label
          ? el("h2", { className: "lg-group__label", children: [el("span", { text: group.label }), el("span", { className: "lg-group__count", text: String(group.sessions.length) })] })
          : null,
        el("div", { className: "lg-grid", attrs: { role: "list" }, children: group.sessions.map((s) => renderCard(s, cardContext(s))) }),
      ],
    }),
  );
  if (view.error) sections.push(el("p", { className: "lg-empty", text: view.error }));
  else if (!shown.length) {
    sections.push(el("p", { className: "lg-empty", text: view.sessions.length ? "Nothing matches." : "Loading recordings…" }));
  }
  list.replaceChildren(...sections);
  root.scrollTop = scroll;
  observeCards();
  renderDetailNow();
}

/**
 * Timeline: one continuous grid, each day opened by a date tile in the flow. A heading per day
 * leaves a mostly-empty row under every day with one recording, which is most days.
 */
function timeline(groups) {
  const cells = [];
  for (const group of groups.values()) {
    const date = startedAt(group.sessions[0]);
    cells.push(
      el("div", {
        className: "lg-marker",
        attrs: { role: "presentation" },
        children: [
          el("span", { className: "lg-marker__day", text: date ? String(date.getDate()) : "" }),
          el("span", {
            className: "lg-marker__label",
            text: /^(Today|Yesterday)$/.test(group.label) ? group.label : date?.toLocaleDateString(undefined, { weekday: "long" }) ?? "",
          }),
          el("span", { className: "lg-marker__month", text: date?.toLocaleDateString(undefined, { month: "long", year: "numeric" }) ?? "" }),
          el("span", { className: "lg-marker__count", text: `${group.sessions.length} recording${group.sessions.length === 1 ? "" : "s"}` }),
        ],
      }),
      ...group.sessions.map((s) => renderCard(s, cardContext(s))),
    );
  }
  return el("div", { className: "lg-grid lg-grid--timeline", attrs: { role: "list" }, children: cells });
}

let lastLayout = lab.settings.layout;

function renderAll() {
  // Split view always has a recording chosen; leaving it should not open that one in a panel.
  if (lastLayout === "split" && lab.settings.layout !== "split") view.selected = "";
  lastLayout = lab.settings.layout;
  applySettingsToRoot();
  const shown = renderToolbarNow();
  // The split view's right-hand pane and the group headings sit just under the header.
  root.style.setProperty("--toolbar-h", `${lab.settings.header === "floating" ? 0 : toolbar.offsetHeight}px`);
  renderList(shown);
}

// -- the detail view -------------------------------------------------------------------------------

function detailMode() {
  return lab.settings.layout === "split" ? "split" : lab.settings.detail;
}

function openDetail(session) {
  view.selected = view.selected === session.key && detailMode() !== "split" ? "" : session.key;
  renderList(visible());
}

function closeDetail() {
  const key = view.selected;
  view.selected = "";
  renderList(visible());
  list.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus();
}

function renderDetailNow() {
  const mode = detailMode();
  const shown = visible();
  if (mode === "split" && !shown.some((s) => s.key === view.selected)) view.selected = shown[0]?.key ?? "";
  const session = view.sessions.find((s) => s.key === view.selected);

  host.replaceChildren();
  host.hidden = true;
  inspector.replaceChildren();
  root.dataset.detail = session ? mode : "";
  if (!session) return;
  list.querySelectorAll("[data-selected]").forEach((node) => node.removeAttribute("data-selected"));
  const card = list.querySelector(`[data-key="${CSS.escape(session.key)}"]`);
  card?.setAttribute("data-selected", "true");

  const detail = renderDetail(session, { ...cardContext(session), mode, onClose: closeDetail });
  if (mode === "split") inspector.append(detail);
  else if (mode === "inline" && card) {
    card.after(detail);
    detail.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  else {
    host.hidden = false;
    host.dataset.mode = mode;
    const scrim = el("div", { className: "ld-scrim" });
    scrim.addEventListener("click", closeDetail);
    host.append(scrim, detail);
  }
}

// -- doing things ----------------------------------------------------------------------------------

async function act(action, session) {
  const changed = await runAction(action, session, { consent, report });
  if (changed === "deleted") {
    view.sessions = view.sessions.filter((s) => s.key !== session.key);
    if (view.selected === session.key) view.selected = "";
    renderAll();
  }
}

function togglePanel() {
  view.panelOpen = !view.panelOpen;
  panel.hidden = !view.panelOpen;
  if (view.panelOpen) drawPanel();
  applySettingsToRoot();
  renderToolbarNow();
}

function drawPanel() {
  renderCustomizer(panel, { report, count: () => visible().length, onClose: togglePanel });
}

// -- what each recording is about, fetched as cards come into view ------------------------------

const wanted = new Set();
let glanceTimer = 0;
const observer = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      const key = entry.target.dataset.key;
      if (entry.isIntersecting && key && !glances.has(key)) wanted.add(key);
    }
    if (wanted.size) {
      clearTimeout(glanceTimer);
      glanceTimer = setTimeout(fetchGlances, 80);
    }
  },
  { root, rootMargin: "600px 0px" },
);

function observeCards() {
  observer.disconnect();
  list.querySelectorAll(".lc-card").forEach((card) => {
    const session = view.sessions.find((s) => s.key === card.dataset.key);
    if (session && !session.orphan && session.segments > 0 && !glances.has(card.dataset.key)) observer.observe(card);
  });
}

async function fetchGlances() {
  const keys = [...wanted].slice(0, 40);
  keys.forEach((key) => wanted.delete(key));
  if (!keys.length) return;
  try {
    const body = await get(`/api/labs/sessions/glance?keys=${keys.map(encodeURIComponent).join(",")}`);
    for (const key of keys) glances.set(key, body.glances?.[key] ?? {});
  } catch {
    for (const key of keys) glances.set(key, {});
  }
  for (const key of keys) {
    const node = list.querySelector(`.lc-card[data-key="${CSS.escape(key)}"]`);
    const session = view.sessions.find((s) => s.key === key);
    if (node && session) node.replaceWith(renderCard(session, cardContext(session)));
    if (key === view.selected) renderDetailNow();
  }
  if (wanted.size) fetchGlances();
}

// -- start ------------------------------------------------------------------------------------------

subscribe((changed) => {
  const onlyText = changed.every((key) => ["notes", "general", "verdictNote"].includes(key));
  if (onlyText) return;
  if (view.panelOpen) drawPanel();
  renderAll();
});

document.addEventListener("keydown", (event) => {
  const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
  if (event.key === "Escape" && view.selected && detailMode() !== "split" && !consent.isOpen) {
    closeDetail();
    return;
  }
  if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
  const index = Number(event.key) - 1;
  if (index >= 0 && index < LAYOUTS.length) setSetting("layout", LAYOUTS[index].id);
  if (event.key === "c") togglePanel();
  if (event.key === "/") {
    event.preventDefault();
    toolbar.querySelector("[data-lab-search]")?.focus();
  }
});

async function load() {
  renderAll();
  try {
    const [body, state] = await Promise.all([api.sessions(), api.session().catch(() => null)]);
    view.sessions = body.sessions ?? [];
    view.viewingKey = state?.viewing?.key ?? "";
    view.busy = state?.running
      ? "Stop the current recording first."
      : state?.transcription?.state === "running"
        ? "Wait for the running transcription to finish."
        : "";
    view.error = "";
  } catch (error) {
    view.error = error instanceof ApiError ? error.message : "The recordings could not be loaded.";
  }
  renderAll();
  // The first screenful straight away, rather than after the observer's first pass — which a
  // browser may hold back while the window is in the background.
  for (const session of visible().slice(0, 40)) {
    if (!session.orphan && session.segments > 0) wanted.add(session.key);
  }
  void fetchGlances();
}

void load();
