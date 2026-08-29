/**
 * The recordings page — every session that has been captured.
 *
 * Its own entry point rather than part of `main.js`: this page has no WebSocket, no transcript
 * store, and no assistant, and loading that machinery to render a list would mean a page that
 * cannot open when the pipeline is unhealthy — exactly when someone wants to retrieve a transcript.
 */

import { $, el, setText, toggle } from "./core/dom.js";
import { duration as formatDuration } from "./core/format.js";
import { api } from "./transport/api.js";

const FORMATS = [
  ["markdown", "Markdown"],
  ["text", "Plain text"],
  ["srt", "SRT"],
  ["vtt", "WebVTT"],
  ["json", "JSON"],
];

function when(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

/** The three things a recording can hold, in a fixed order so a column of rows scans. */
const MEDIA = [
  ["video", "Video"],
  ["audio", "Audio"],
  ["transcript", "Transcript"],
];

function size(bytes) {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

class SessionsPage {
  constructor() {
    this.list = $("[data-sessions-list]");
    this.empty = $("[data-sessions-empty]");
    this.error = $("[data-sessions-error]");
    this.directory = $("[data-sessions-directory]");
    this.runningKey = "";
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
      this.media(media),
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
   * What this recording holds — video, audio, transcript — as three chips.
   *
   * All three are always drawn, present or not. A row that showed only what it had would need
   * reading rather than scanning, because "video · transcript" and "video · audio" have the same
   * shape and different meanings.
   */
  media(media) {
    return el("div", {
      className: "session__media",
      attrs: { "aria-label": "What this recording holds" },
      children: MEDIA.map(([key, label]) => {
        const present = Boolean(media[key]);
        return el("span", {
          className: "media-chip",
          text: label,
          attrs: {
            "data-present": String(present),
            title: present ? `${label} is available` : `No ${label.toLowerCase()}`,
          },
        });
      }),
    });
  }

  actions(session, running) {
    const format = el("select", {
      className: "field__control field__control--compact",
      attrs: { "aria-label": `Export format for ${session.title}` },
      children: FORMATS.map(([value, label]) =>
        el("option", { text: label, attrs: { value } })
      ),
    });

    const download = el("a", {
      className: "button",
      text: "Export",
      attrs: { href: api.sessionExportUrl(session.key, "markdown"), download: "" },
    });
    // The link's href follows the dropdown, so the browser handles the download itself and the
    // filename comes from the server's own header rather than being invented here.
    format.addEventListener("change", () => {
      download.href = api.sessionExportUrl(session.key, format.value);
    });

    const remove = el("button", {
      className: "button button--quiet",
      text: "Delete",
      attrs: { type: "button", disabled: running },
    });
    remove.addEventListener("click", () => this.remove(session));

    return el("div", { className: "session__actions", children: [format, download, remove] });
  }

  async remove(session) {
    const confirmed = window.confirm(
      `Delete "${session.title}"? The transcript, summaries, and glossary all go with it, and this cannot be undone.`
    );
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
