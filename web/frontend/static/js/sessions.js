/**
 * The recordings page — every session that has been captured.
 *
 * Its own entry point rather than part of `main.js`: this page has no WebSocket, no transcript
 * store, and no assistant, and loading that machinery to render a list would mean a page that
 * cannot open when the pipeline is unhealthy — exactly when someone wants to retrieve a transcript.
 */

import { actionButton, primaryAction, reasonLine } from "./components/recording-picker.js";
import { RecordingWindow } from "./components/recording-window.js";
import { TranscribeConsent } from "./components/transcribe-consent.js";
import { $, el, setText, toggle } from "./core/dom.js";
import { duration as formatDuration } from "./core/format.js";
import { deletionWarning, mediaChips, size, when } from "./core/recording-facts.js";
import { exportJob } from "./stores/export.js";
import { api, ApiError } from "./transport/api.js";

class SessionsPage {
  constructor() {
    this.list = $("[data-sessions-list]");
    this.empty = $("[data-sessions-empty]");
    this.error = $("[data-sessions-error]");
    this.directory = $("[data-sessions-directory]");
    this.runningKey = "";
    // The same window that opens when a recording stops, reached from the other end. This page has
    // no socket, so it polls while an export runs — see `openWindow`.
    this.window = new RecordingWindow($("[data-recording-window]"));
    this.window.ask = (session) => this.askSection(session);
    this.consent = new TranscribeConsent($("[data-transcribe-consent]"));
    /** The recording the main screen is showing, so its row can say so (D-076). */
    this.viewingKey = "";
    /** Why nothing can be opened or transcribed right now — a recording or a pass running. */
    this.busy = "";
  }

  // -- bringing a recording back to the main screen (D-076) -------------------------------

  handlers() {
    return {
      onOpen: (session) => void this.bringBack(session),
      onTranscribe: (session) => void this.transcribe(session),
    };
  }

  /** Open it on the main screen, then go there — that is where it can be asked about. */
  async bringBack(session) {
    try {
      await api.openRecording(session.key);
      window.location.href = "/";
    } catch (error) {
      this.report(error instanceof ApiError ? error.message : "That recording could not be opened.");
    }
  }

  /**
   * Ask, and only on approval transcribe it — then go to the main screen, where the pass shows its
   * progress and, if the box was left ticked, the recording opens when it finishes.
   */
  async transcribe(session) {
    this.window.close();
    const started = await this.consent.show(session.key);
    if (started) window.location.href = "/";
  }

  /** The recording window's "Ask about it" section. */
  askSection(session) {
    if (session.key === this.viewingKey) {
      return el("p", { className: "overview__hint", text: "On the main screen now." });
    }
    const action = primaryAction(session, { busy: this.busy });
    const hint =
      action.kind === "open"
        ? "Bring it back to the main screen and ask the assistant about it. Your earlier " +
          "questions come back with it."
        : action.kind === "transcribe"
          ? "It has no transcript yet, so there is nothing to ask about. Transcribing it is " +
            "your choice — nothing runs until you approve it."
          : "";
    return el("div", {
      className: "overview__row overview__row--stack",
      children: [
        hint ? el("p", { className: "overview__hint", text: hint }) : null,
        actionButton(session, this.handlers(), { busy: this.busy }),
        reasonLine(session, this.busy),
      ],
    });
  }

  report(message) {
    toggle(this.error, Boolean(message));
    setText(this.error, message);
  }

  /**
   * Open the recording window for one session, and keep it fed.
   *
   * **This page deliberately has no WebSocket** — its own header says so: loading the transport,
   * the stores and the transcript machinery to render a list would mean a page that cannot open
   * when the pipeline is unhealthy, which is exactly when someone wants to retrieve a recording.
   * So an export's progress is polled here rather than pushed, which for a job that reports about
   * once a second is a difference nobody can see.
   */
  async openWindow(session) {
    const poll = setInterval(async () => {
      if (!this.window.isOpen) return;
      try {
        const body = await api.exportStatus(session.key);
        if (body.job) exportJob.set(body.job);
      } catch {
        // A failed poll is a frame missed, not a state. The next one answers.
      }
    }, 1000);

    try {
      await this.window.show(session.key, {
        session: { ...session, running: session.key === this.runningKey },
      });
    } finally {
      clearInterval(poll);
      await this.load();
      // The list is re-rendered on close, which replaces the button the focus trap would have
      // returned to. Put focus back on the same row's, so a keyboard user is where they were.
      const row = this.list.querySelector(`[data-session-key="${CSS.escape(session.key)}"]`);
      row?.querySelector("button")?.focus();
    }
  }

  async load() {
    try {
      const [body, state] = await Promise.all([api.sessions(), api.session().catch(() => null)]);
      this.runningKey = body.running_key ?? "";
      this.viewingKey = state?.viewing?.key ?? "";
      this.busy = state?.running
        ? "Stop the current recording first."
        : state?.transcription?.state === "running"
          ? "Wait for the running transcription to finish."
          : "";
      setText(this.directory, `Stored in ${body.directory}`);
      this.render(body.sessions ?? []);
      toggle(this.error, false);
    } catch (error) {
      toggle(this.error, true);
      setText(this.error, error.message);
    }
  }

  render(sessions) {
    toggle(this.empty, sessions.length === 0);
    this.list.replaceChildren(...sessions.map((session) => this.row(session)));
  }

  row(session) {
    const running = session.key === this.runningKey;
    const media = session.media ?? {};
    const stored = media.recording_bytes
      ? size(session.size_bytes + media.recording_bytes)
      : size(session.size_bytes);
    const meta = [
      when(session.started_at),
      session.duration_seconds ? formatDuration(session.duration_seconds) : null,
      `${session.segments} segments`,
      `${session.words} words`,
      session.glossary_terms ? `${session.glossary_terms} terms` : null,
      stored,
    ].filter(Boolean);

    const children = [
      el("div", {
        className: "session__head",
        children: [
          el("h2", { className: "session__title", text: session.title }),
          // Marked rather than hidden: it is the session the user is most likely looking for, and
          // its numbers are about to change.
          running ? el("span", { className: "badge", text: "recording now" }) : null,
        ],
      }),
      el("p", { className: "session__meta", text: meta.join(" · ") }),
      mediaChips(media),
    ];

    if (session.problem) {
      children.push(el("p", { className: "session__problem", text: session.problem }));
    } else {
      children.push(this.actions(session, running));
    }

    return el("article", {
      className: "session",
      attrs: { role: "listitem", "data-session-key": session.key },
      children,
    });
  }

  /**
   * The row's controls: open the recording's window, or delete it (D-077).
   *
   * Every export used to sit here — a format select, an "Export" link for the transcript, a
   * "Conversation" link and an "Export…" button for the web app — and two controls named Export
   * that did different things was reported as confusing. They are sections of the window now.
   */
  actions(session, running) {
    // The one thing this recording is for — bring it back, or transcribe it first — and why not
    // when it can be neither. The same answer the window and the main screen's picker give.
    const primary =
      session.key === this.viewingKey
        ? el("span", { className: "badge", text: "on the main screen" })
        : actionButton(session, this.handlers(), { busy: this.busy });

    const open = el("button", {
      className: "button",
      text: "Open…",
      attrs: {
        type: "button",
        title: "What this recording holds, and every way to export it",
        "aria-label": `Open ${session.title}`,
      },
    });
    open.addEventListener("click", () => void this.openWindow(session));

    const remove = el("button", {
      className: "button button--quiet",
      text: "Delete",
      attrs: { type: "button", disabled: running, "aria-label": `Delete ${session.title}` },
    });
    remove.addEventListener("click", () => this.remove(session));

    return el("div", {
      className: "session__actions",
      children: [primary, open, remove, reasonLine(session, this.busy)],
    });
  }

  async remove(session) {
    const confirmed = window.confirm(deletionWarning(session));
    if (!confirmed) return;

    try {
      await api.deleteSession(session.key);
      await this.load();
    } catch (error) {
      toggle(this.error, true);
      setText(this.error, error.message);
    }
  }
}

new SessionsPage().load();
