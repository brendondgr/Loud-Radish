/**
 * The recordings page — every session that has been captured.
 *
 * Its own entry point rather than part of `main.js`: this page has no WebSocket, no transcript
 * store, and no assistant, and loading that machinery to render a list would mean a page that
 * cannot open when the pipeline is unhealthy — exactly when someone wants to retrieve a transcript.
 */

import { RecordingWindow } from "./components/recording-window.js";
import { $, el, setText, toggle } from "./core/dom.js";
import { duration as formatDuration } from "./core/format.js";
import { deletionWarning, mediaChips, size, when } from "./core/recording-facts.js";
import { exportJob } from "./stores/export.js";
import { api } from "./transport/api.js";

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
      const body = await api.sessions();
      this.runningKey = body.running_key ?? "";
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
    const open = el("button", {
      className: "button button--primary",
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

    return el("div", { className: "session__actions", children: [open, remove] });
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
