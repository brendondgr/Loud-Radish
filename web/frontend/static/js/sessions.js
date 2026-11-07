/**
 * The Recordings page (D-085): every recording as a wide card, grouped by day.
 *
 * Its own entry point rather than part of `main.js`: this page has no WebSocket, no transcript
 * store and no assistant, and loading that machinery to show a list would mean a page that cannot
 * open when the pipeline is unhealthy — exactly when someone wants to retrieve a transcript. So
 * what changes elsewhere (an export's progress, titles being written) is polled while it matters.
 *
 * The cards, the compact window, the toolbar and the batched glances are in `recordings/`.
 */

import { FocusTrap } from "./a11y/focus-trap.js";
import { RecordingWindow } from "./components/recording-window.js";
import { TranscribeConsent } from "./components/transcribe-consent.js";
import { $, el } from "./core/dom.js";
import { runAction } from "./recordings/actions.js";
import { renderCard } from "./recordings/card.js";
import { cardTitle, dayOf, startedAt } from "./recordings/describe.js";
import { renderDetail } from "./recordings/detail.js";
import { Glances } from "./recordings/glances.js";
import { menuOpen } from "./recordings/menu.js";
import { FILTERS, matchesFilter, renderToolbar } from "./recordings/toolbar.js";
import { exportJob } from "./stores/export.js";
import { api, ApiError } from "./transport/api.js";

const root = $("[data-recordings]");
const toolbar = $("[data-recordings-toolbar]");
const list = $("[data-recordings-list]");
const host = $("[data-recording-detail]");
const status = $("[data-recordings-status]");
const consent = new TranscribeConsent($("[data-transcribe-consent]"));
const exportWindow = new RecordingWindow($("[data-recording-window]"));

const view = {
  sessions: [],
  query: "",
  filter: "all",
  sort: "newest",
  selected: "",
  busy: "",
  viewingKey: "",
  runningKey: "",
  directory: "",
  untitled: null,
  error: "",
};
const glances = new Glances(root, (keys) => keys.forEach(redrawCard));
let trap = null;

// -- telling the owner ---------------------------------------------------------------------------

let statusTimer = 0;
function report(message, { stay = false } = {}) {
  status.textContent = message;
  status.hidden = !message;
  clearTimeout(statusTimer);
  if (!stay) statusTimer = setTimeout(() => (status.hidden = true), 6000);
}

// -- what is on screen ---------------------------------------------------------------------------

const byKey = () => new Map(view.sessions.map((session) => [session.key, session]));

function haystack(session) {
  const glance = glances.get(session.key);
  return [session.title, cardTitle(session, glance).text, glance?.opening, glance?.summary, ...(glance?.keywords ?? [])]
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

function context(session) {
  return {
    glance: glances.get(session.key),
    busy: view.busy,
    viewing: session.key === view.viewingKey,
    running: session.key === view.runningKey,
    selected: session.key === view.selected,
    onOpen: openDetail,
    onAction: act,
  };
}

function drawToolbar(shown) {
  renderToolbar(
    toolbar,
    {
      ...view,
      shown: shown.length,
      total: view.sessions.length,
      counts: Object.fromEntries(FILTERS.map(([id]) => [id, view.sessions.filter((s) => matchesFilter(s, id)).length])),
    },
    {
      onQuery: (query) => {
        view.query = query;
        draw();
      },
      onFilter: (filter) => {
        view.filter = filter;
        draw();
      },
      onSort: (sort) => {
        view.sort = sort;
        draw();
      },
      onName: () => void nameUntitled(),
    },
  );
}

function draw() {
  const shown = visible();
  drawToolbar(shown);

  // Grouped by day only in date order: "longest first" grouped by day would scatter each day.
  const byDay = view.sort !== "longest";
  const groups = new Map();
  for (const session of shown) {
    const day = byDay ? dayOf(session) : { key: "all", label: "" };
    if (!groups.has(day.key)) groups.set(day.key, { label: day.label, sessions: [] });
    groups.get(day.key).sessions.push(session);
  }

  const sections = [...groups.values()].map((group) =>
    el("section", {
      className: "rgroup",
      attrs: { "aria-label": group.label || "Recordings" },
      children: [
        group.label
          ? el("h2", {
              className: "rgroup__label",
              children: [el("span", { text: group.label }), el("span", { className: "rgroup__count", text: String(group.sessions.length) })],
            })
          : null,
        el("div", {
          className: "rgrid",
          attrs: { role: "list" },
          children: group.sessions.map((session) => renderCard(session, context(session))),
        }),
      ],
    }),
  );
  if (view.error) sections.push(el("p", { className: "recordings__empty", text: view.error }));
  else if (!shown.length) {
    const text = !view.loaded
      ? "Loading recordings…"
      : view.sessions.length
        ? "Nothing matches."
        : "Nothing recorded yet. Recordings appear here as soon as you stop one.";
    sections.push(el("p", { className: "recordings__empty", text }));
  }
  list.replaceChildren(...sections);
  glances.observe(list.querySelectorAll(".rcard"), byKey());
}

function redrawCard(key) {
  const session = view.sessions.find((s) => s.key === key);
  const node = list.querySelector(`.rcard[data-key="${CSS.escape(key)}"]`);
  if (session && node) {
    const focused = node.contains(document.activeElement);
    const fresh = renderCard(session, context(session));
    node.replaceWith(fresh);
    if (focused) fresh.focus();
  }
  // Not while the title is being typed: a glance arriving would throw the field away.
  if (session && key === view.selected && !host.querySelector(".rdetail__name--editing")) drawDetail();
}

// -- the compact window ----------------------------------------------------------------------------

function openDetail(session) {
  view.selected = session.key;
  drawDetail();
}

function closeDetail() {
  if (!view.selected) return;
  view.selected = "";
  host.hidden = true;
  host.replaceChildren();
  trap?.release();
  trap = null;
}

function drawDetail() {
  const session = view.sessions.find((s) => s.key === view.selected);
  if (!session) {
    closeDetail();
    return;
  }
  const detail = renderDetail(session, {
    ...context(session),
    onClose: closeDetail,
    onRename: rename,
  });
  const scrim = el("div", { className: "rdetail-host__scrim", attrs: { "aria-hidden": "true" } });
  scrim.addEventListener("click", closeDetail);
  const reopening = !host.hidden;
  host.replaceChildren(scrim, detail);
  host.hidden = false;
  // Redrawn in place after a rename: focus goes back to Rename rather than to the main button.
  trap?.release();
  trap = new FocusTrap(detail);
  const first = reopening ? ".rdetail__rename" : ".rdetail__use .rbtn";
  trap.activate(detail.querySelector(first) ?? detail.querySelector(".rdetail__close"));
}

// -- doing things --------------------------------------------------------------------------------

async function act(action, session) {
  const changed = await runAction(action, session, { consent, exportWindow: openExport, report });
  if (changed === "deleted") {
    view.sessions = view.sessions.filter((s) => s.key !== session.key);
    if (view.selected === session.key) closeDetail();
    draw();
  }
}

async function rename(session, title) {
  try {
    const body = await api.renameSession(session.key, title);
    Object.assign(session, { title: body.title, named: true });
    report("Renamed.");
    redrawCard(session.key);
    if (view.selected === session.key) drawDetail();
  } catch (error) {
    report(error instanceof ApiError ? error.message : "The recording could not be renamed.");
  }
}

/**
 * The web-app export, in its own window over this one. This page has no socket, so the export's
 * progress is polled while the window is open — about once a second, which is what it reports at.
 */
async function openExport(session) {
  const poll = setInterval(async () => {
    if (!exportWindow.isOpen) return;
    try {
      const body = await api.exportStatus(session.key);
      if (body.job) exportJob.set(body.job);
    } catch {
      // A failed poll is a frame missed, not a state. The next one answers.
    }
  }, 1000);
  host.hidden = true;
  trap?.release();
  try {
    await exportWindow.show(session.key, { session });
  } finally {
    clearInterval(poll);
    if (view.selected) drawDetail();
  }
}

/** Name the untitled recordings, on approval (D-084), and redraw as their titles arrive. */
async function nameUntitled() {
  const plan = view.untitled;
  if (!plan?.count) return;
  const approved = window.confirm(
    `Ask ${plan.model} to title ${plan.count} recording${plan.count === 1 ? "" : "s"}?\n\n` +
      "It reads each one's summaries, or its first few hundred words, one recording at a time. " +
      "Titles you have given are never changed, and any title can be renamed afterwards.",
  );
  if (!approved) return;
  try {
    await api.nameUntitled();
  } catch (error) {
    report(error instanceof ApiError ? error.message : "Naming could not be started.");
    return;
  }
  const timer = setInterval(async () => {
    try {
      view.untitled = await api.untitledPlan();
    } catch {
      return;
    }
    const run = view.untitled.backfill;
    report(run.running ? `Naming… ${run.done} of ${run.total}` : `Named ${run.named} of ${run.total}.`, {
      stay: run.running,
    });
    if (!run.running) {
      clearInterval(timer);
      await load();
    }
  }, 1500);
}

// -- start ---------------------------------------------------------------------------------------

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && view.selected && !menuOpen() && !consent.isOpen && !exportWindow.isOpen) {
    const key = view.selected;
    closeDetail();
    list.querySelector(`.rcard[data-key="${CSS.escape(key)}"]`)?.focus();
    return;
  }
  const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
  if (event.key === "/" && !typing && !view.selected) {
    event.preventDefault();
    toolbar.querySelector("[data-recordings-search]")?.focus();
  }
});

async function load() {
  draw();
  try {
    const [body, state, untitled] = await Promise.all([
      api.sessions(),
      api.session().catch(() => null),
      api.untitledPlan().catch(() => null),
    ]);
    view.sessions = body.sessions ?? [];
    view.directory = body.directory ?? "";
    view.runningKey = body.running_key ?? "";
    view.viewingKey = state?.viewing?.key ?? "";
    view.untitled = untitled;
    view.busy = state?.running
      ? "Stop the current recording first."
      : state?.transcription?.state === "running"
        ? "Wait for the running transcription to finish."
        : "";
    view.error = "";
  } catch (error) {
    view.error = error instanceof ApiError ? error.message : "The recordings could not be loaded.";
  }
  view.loaded = true;
  draw();
  if (view.selected) drawDetail();
  glances.want(visible().slice(0, 40));
}

void load();
