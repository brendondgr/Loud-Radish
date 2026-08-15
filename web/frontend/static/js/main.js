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
import { ChatPane } from "./components/chat-pane.js";
import { GlossaryPanel } from "./components/glossary-panel.js";
import { Header } from "./components/header.js";
import { TranscriptSelection } from "./components/selection.js";
import { SettingsModal } from "./components/settings-modal.js";
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
import { CHAT_CHANGED, chat } from "./stores/chat.js";
import { config } from "./stores/config.js";
import { health } from "./stores/health.js";
import { session } from "./stores/session.js";
import { transcript } from "./stores/transcript.js";

function boot() {
  const socket = new TranscriptSocket();

  const pane = new TranscriptPane($("[data-transcript-pane]"));
  new StatusBar($("[data-status-bar]"));

  const settings = new SettingsModal($("[data-settings]"));

  const banners = new Banners($("[data-banners]"), {
    onRemedy: async ({ changes, settings: tab }) => {
      if (changes) await config.patch(changes);
      if (tab) await settings.show(tab);
    },
  });

  const header = new Header($(".header"), {
    onStart: () => start(banners, settings),
    onStop: () => stop(banners),
    onOpenSettings: (section) => settings.show(section),
  });

  // The chat pane and the glossary both navigate the transcript, and neither knows it exists —
  // they are handed a callback, which is what keeps the dependency one-way.
  const chatPane = new ChatPane($("[data-chat-pane]"), {
    onSeek: (seconds) => pane.scrollToTime(seconds),
  });

  const glossary = new GlossaryPanel($("[data-glossary-panel]"), {
    toggleButton: $("[data-glossary-toggle]"),
    countBadge: $("[data-glossary-count]"),
    onSeek: (seconds) => pane.scrollToTime(seconds),
  });

  new TranscriptSelection($("[data-transcript-pane]"), {
    onAsk: (quote) => {
      chatPane.setQuote(quote);
      showPane("chat", { persist: false });
    },
  });

  wireTranscript(socket, pane);
  wireHealth();
  wireSession(header);
  wireControls(pane);
  wireChat(chatPane);

  socket.connect();
  hydrate(chatPane, glossary);
}

/** Keep the narrow-layout tab badge in step with unread answers. */
function wireChat(chatPane) {
  const badge = $("[data-chat-badge]");
  const railBadge = $("[data-chat-rail-badge]");

  on(CHAT_CHANGED, () => {
    for (const node of [badge, railBadge]) {
      if (!node) continue;
      node.textContent = String(chat.unread);
      node.hidden = chat.unread === 0;
    }
  });

  // Reading the transcript is the signal that the user is caught up, so the read mark follows the
  // transcript rather than the conversation — that is what "what did I miss" is asking about.
  void chatPane;
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
  for (const tab of document.querySelectorAll("[data-pane-tab]")) {
    tab.addEventListener("click", () => showPane(tab.dataset.paneTab));
  }
  showPane(prefs.get("activePane"));

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

/**
 * Show one pane in the narrow layout.
 *
 * A no-op on a wide screen, where both are visible — but "ask about this" calls it, and that
 * gesture must land the user on the composer whichever layout they are in.
 *
 * `persist` is false for that gesture on purpose. Remembering it would mean that having once asked
 * about a selection, the application opens on the assistant from then on — and on a screen where
 * only one pane fits, the transcript is the thing it exists to show.
 */
function showPane(name, { persist = true } = {}) {
  const main = $(".app__main");
  main?.setAttribute("data-active-pane", name);
  if (persist) prefs.set("activePane", name);
  for (const tab of document.querySelectorAll("[data-pane-tab]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.paneTab === name));
  }
  if (name === "chat") chat.markRead();
}

/** Fetch state over HTTP on load, so the page is correct before the socket says anything. */
async function hydrate(chatPane, glossary) {
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

  // Loaded on boot rather than only when settings open: the header's model name and privacy
  // indicator are read from it, and they are what a user checks *before* pressing record.
  try {
    await config.load();
    session.setConfig(config.data);
  } catch (error) {
    if (error instanceof ApiError) console.warn("Could not load configuration:", error.message);
  }

  // Both degrade to an empty pane rather than an error: with no session there is nothing to load,
  // and that is the normal state before the first recording.
  await Promise.allSettled([chatPane?.load(), glossary?.load()]);
}

async function start(banners, settings) {
  try {
    await api.startSession({});
  } catch (error) {
    banners.show({
      code: error.code ?? "start-failed",
      severity: error.severity ?? "critical",
      message: error.message,
      // A failure to start is nearly always the input source, and the remedy is a setting. Opening
      // the tab that fixes it beats a message telling the user to go and find it.
      remedy: "audio",
      remedy_label: "Open audio settings",
    });
    settings?.show("audio");
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
