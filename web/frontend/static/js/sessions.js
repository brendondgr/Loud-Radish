/**
 * The recordings page — every session that has been captured.
 *
 * Its own entry point rather than part of `main.js`: this page has no WebSocket, no transcript
 * store, and no assistant, and loading that machinery to render a list would mean a page that
 * cannot open when the pipeline is unhealthy — exactly when someone wants to retrieve a transcript.
 */

import { ExportDialog } from "./components/export-dialog.js";
import { $, el, setText, toggle } from "./core/dom.js";
import { duration as formatDuration } from "./core/format.js";
import { exportJob } from "./stores/export.js";
import { api } from "./transport/api.js";


/**
 * Named the way the transcript pane names them: "Final" means something to a reader and
 * "revision 1" does not. Anything beyond the two the application produces today falls back to a
 * number rather than being hidden.
 */
const REVISION_LABELS = { 0: "Live", 1: "Final" };
const revisionLabel = (revision) => REVISION_LABELS[revision] ?? `Version ${revision}`;

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

/**
 * What a chip's tooltip says.
 *
 * Audio gets a sentence of its own because "yes" has two meanings worth telling apart: the
 * separate recording is still on disk, or a successful transcription pass deleted it and the sound
 * now lives in the video. Both are audio; only one of them can be transcribed again.
 */
function chipTitle(key, label, present, media) {
  if (!present) return `No ${label.toLowerCase()}`;
  if (key !== "audio") return `${label} is available`;
  return media.audio_file
    ? "The recorded audio is still on disk and can be transcribed again"
    : "The audio is in the video — the separate recording was removed once it was transcribed";
}

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
    // The same window that opens when a recording stops, reached from the other end. This page has
    // no socket, so it polls while an export runs — see `openExport`.
    this.exportDialog = new ExportDialog($("[data-export]"));
  }

  /**
   * Open the export window for one session, and keep it fed.
   *
   * **This page deliberately has no WebSocket** — its own header says so: loading the transport,
   * the stores and the transcript machinery to render a list would mean a page that cannot open
   * when the pipeline is unhealthy, which is exactly when someone wants to retrieve a recording.
   * So an export's progress is polled here rather than pushed, which for a job that reports about
   * once a second is a difference nobody can see.
   */
  async openExport(session) {
    const poll = setInterval(async () => {
      if (!this.exportDialog.isOpen) return;
      try {
        const body = await api.exportStatus(session.key);
        if (body.job) exportJob.set(body.job);
      } catch {
        // A failed poll is a frame missed, not a state. The next one answers.
      }
    }, 1000);

    try {
      await this.exportDialog.show(session.key, { title: session.title });
    } finally {
      clearInterval(poll);
      await this.load();
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
          attrs: { "data-present": String(present), title: chipTitle(key, label, present, media) },
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

    // **Only where there is a choice (D-066).** A session that transcribed live and again
    // afterwards holds two passes, and the export used to ship the newest with no way to ask for
    // the other. The control is drawn only for those sessions — a select with one option is
    // chrome that explains nothing, the same rule the transcript pane's Live/Final switch follows.
    const revisions = Array.isArray(session.revisions) ? session.revisions : [];
    const latest = revisions.length ? revisions[revisions.length - 1] : undefined;
    const version =
      revisions.length > 1
        ? el("select", {
            className: "field__control field__control--compact",
            attrs: { "aria-label": `Transcript version to export for ${session.title}` },
            children: revisions.map((revision) =>
              el("option", {
                text: revisionLabel(revision),
                attrs: { value: String(revision), ...(revision === latest ? { selected: "" } : {}) },
              })
            ),
          })
        : null;
    const chosenRevision = () => (version ? Number(version.value) : undefined);

    const download = el("a", {
      className: "button",
      text: "Export",
      attrs: {
        href: api.sessionExportUrl(session.key, "markdown", chosenRevision()),
        download: "",
      },
    });
    // The link's href follows both dropdowns, so the browser handles the download itself and the
    // filename comes from the server's own header rather than being invented here.
    const follow = () => {
      download.href = api.sessionExportUrl(session.key, format.value, chosenRevision());
    };
    format.addEventListener("change", follow);
    version?.addEventListener("change", follow);

    // **Its own button, and only where there is one.** The transcript export deliberately no
    // longer carries the conversation: a transcript is usually being handed to somebody else, and
    // one person's questions are not part of the record of the talk. They are still the user's,
    // and this is how they get them.
    const chat = session.chat_messages
      ? el("a", {
          className: "button button--quiet",
          text: "Conversation",
          attrs: {
            href: api.sessionChatUrl(session.key, "markdown"),
            download: "",
            title:
              `The ${session.chat_messages} messages you exchanged with the assistant during ` +
              "this recording, on their own. Not included in the transcript or the web app.",
          },
        })
      : null;

    const remove = el("button", {
      className: "button button--quiet",
      text: "Delete",
      attrs: { type: "button", disabled: running },
    });
    remove.addEventListener("click", () => this.remove(session));

    // Offered only when the session holds video, audio, and a transcript. The export *is* those
    // three — a player, a transcript that follows it, and questions asked against both — so a
    // button that produced two of them under the same name would disappoint quietly.
    //
    // **A button rather than a link, since D-037.** It used to be an `<a download>` that fetched
    // hundreds of megabytes with no options and no idea what it would produce. It opens the export
    // window now: the same window that opens when a recording stops, reached from the other end.
    let webapp = null;
    if (session.media?.exportable) {
      webapp = el("button", {
        className: "button button--primary",
        text: "Export…",
        attrs: {
          type: "button",
          title:
            "Preview the recording, choose its size and quality, and watch the export run. " +
            "Your own conversation is not included unless you ask for it.",
        },
      });
      webapp.addEventListener("click", () => void this.openExport(session));
    }

    return el("div", {
      className: "session__actions",
      children: [version, format, download, chat, webapp, remove].filter(Boolean),
    });
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
