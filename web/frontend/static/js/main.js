/**
 * Application entry point — the only module that knows the parts exist.
 *
 * Everything below is wiring: connect the socket, route events into stores, hand the stores to
 * components. Components never talk to each other and never call the API directly; that is what
 * keeps each one testable and replaceable.
 */

import { on } from "./core/bus.js";
import { $ } from "./core/dom.js";
import * as prefs from "./core/storage.js";
import { Banners } from "./components/banners.js";
import { Header } from "./components/header.js";
import { StatusBar } from "./components/status-bar.js";
import { TranscriptPane } from "./components/transcript-pane.js";
import { ApiError, api } from "./transport/api.js";
import {
  ASR_PROGRESS,
  AUDIO_LEVEL,
  CONNECTION_CHANGED,
  SESSION_STARTED,
  SESSION_STATE,
  SESSION_STOPPED,
  STATUS,
  TRANSCRIPT_COMMITTED,
  TRANSCRIPT_HYPOTHESIS,
  VAD_STATE,
} from "./transport/events.js";
import { TranscriptSocket } from "./transport/socket.js";
import { health } from "./stores/health.js";
import { session } from "./stores/session.js";
import { transcript } from "./stores/transcript.js";

function boot() {
  const socket = new TranscriptSocket();

  const banners = new Banners($("[data-banners]"), {
    onRemedy: (changes) => api.patchConfig(changes),
  });

  const pane = new TranscriptPane($("[data-transcript-pane]"));
  new StatusBar($("[data-status-bar]"));

  const header = new Header($(".header"), {
    onStart: () => start(banners),
    onStop: () => stop(banners),
    onOpenSettings: (section) => {
      // Settings arrive in a later phase. Say so plainly rather than doing nothing, which reads
      // as a broken control.
      banners.show({
        code: "settings-pending",
        severity: "warning",
        message: `Settings (${section}) are not built yet. Configure the recorder from its config file for now.`,
      });
    },
  });

  wireTranscript(socket, pane);
  wireHealth();
  wireSession(header);
  wireControls(pane);

  socket.connect();
  hydrate();
}

/** Route transcript events into the store. This is the contract's most important seam. */
function wireTranscript(socket, pane) {
  on(TRANSCRIPT_COMMITTED, (segment) => {
    // Append. Never modify. The store ignores an id it already holds, which is what makes the
    // reconnection replay safe to apply without deduplication logic here.
    if (transcript.commit(segment)) socket.noteSegment(segment.id);
  });

  on(TRANSCRIPT_HYPOTHESIS, ({ text, start }) => {
    // Replace the tail wholly. An empty string clears it.
    transcript.setHypothesis(text, start);
  });

  window.transcriptPane = pane; // used by citation and glossary navigation in later phases
}

function wireHealth() {
  on(AUDIO_LEVEL, (level) => health.setLevel(level));
  on(VAD_STATE, ({ speaking }) => health.setSpeaking(speaking));
  on(STATUS, (status) => health.setStatus(status));
  on(ASR_PROGRESS, (progress) => health.setModelLoad(progress));
  on(CONNECTION_CHANGED, ({ state }) => health.setConnection(state));
}

function wireSession(header) {
  on(SESSION_STARTED, (payload) => {
    // A new session is the one moment the transcript is cleared — never on disconnect.
    transcript.reset();
    session.start(payload);
  });

  on(SESSION_STOPPED, (payload) => session.stop(payload));
  on(SESSION_STATE, (state) => session.hydrate(state));
  void header;
}

/** Controls that belong to the page frame rather than to any one component. */
function wireControls(pane) {
  // Transcript text size, adjustable independently of the rest of the interface.
  let size = prefs.get("transcriptSize");
  applyTextSize(size);

  for (const button of document.querySelectorAll("[data-text-size]")) {
    button.addEventListener("click", () => {
      size = Math.max(14, Math.min(28, size + Number(button.dataset.textSize)));
      applyTextSize(size);
      prefs.set("transcriptSize", size);
    });
  }

  // Narrow-layout pane tabs.
  const main = $(".app__main");
  for (const tab of document.querySelectorAll("[data-pane-tab]")) {
    tab.addEventListener("click", () => {
      const pane = tab.dataset.paneTab;
      main?.setAttribute("data-active-pane", pane);
      prefs.set("activePane", pane);
      for (const other of document.querySelectorAll("[data-pane-tab]")) {
        other.setAttribute("aria-selected", String(other === tab));
      }
    });
  }

  // Search highlights rather than filters, so the transcript keeps its shape and the live stream
  // keeps accumulating behind it.
  const searchInput = $("[data-transcript-search]");
  let searchTimer = null;
  searchInput?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      const query = searchInput.value.trim();
      if (!query) {
        pane.clearHighlight();
        return;
      }
      try {
        const { segments } = await api.search(query);
        pane.highlight(segments.map((segment) => segment.id));
      } catch {
        pane.clearHighlight();
      }
    }, 200);
  });
}

function applyTextSize(px) {
  document.documentElement.style.setProperty("--transcript-size", `${px}px`);
}

/** Fetch state over HTTP on load, so the page is correct before the socket says anything. */
async function hydrate() {
  try {
    const state = await api.session();
    session.hydrate(state);
    if (state.metrics) health.setStatus(state.metrics);

    if (state.running) {
      const { segments } = await api.since(0);
      transcript.commitMany(segments);
    }
  } catch (error) {
    if (error instanceof ApiError) console.warn("Could not load session state:", error.message);
  }
}

async function start(banners) {
  try {
    await api.startSession({});
  } catch (error) {
    banners.show({
      code: error.code ?? "start-failed",
      severity: error.severity ?? "critical",
      message: error.message,
    });
  }
}

async function stop(banners) {
  try {
    await api.stopSession();
  } catch (error) {
    banners.show({ code: error.code ?? "stop-failed", severity: "warning", message: error.message });
  }
}

boot();
