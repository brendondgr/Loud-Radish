/**
 * The recording window's overview face (D-077).
 *
 * Everything that can be done with one past recording, in one place. A row on the recordings page
 * used to carry a format select, an "Export" link for the transcript, a "Conversation" link, and an
 * "Export…" button for the web app — two controls with the same name that did different things.
 * Here each export is a labelled section, shown only where the recording has the thing it exports.
 *
 * Built from the session row `GET /api/sessions` returns, so the window and the list describe a
 * recording from the same facts.
 */

import { duration as formatDuration } from "../core/format.js";
import { el } from "../core/dom.js";
import { FORMATS, mediaChips, revisionLabel } from "../core/recording-facts.js";
import { api } from "../transport/api.js";

function section(title, children, modifier = "") {
  return el("section", {
    className: `overview__section${modifier ? ` overview__section--${modifier}` : ""}`,
    children: [el("h3", { className: "overview__heading", text: title }), ...children],
  });
}

function hint(text) {
  return el("p", { className: "overview__hint", text });
}

/**
 * Render the overview for `session` into `container`.
 *
 * @param {object} handlers
 * @param {() => void} handlers.onWebApp - open the web-app export's choosing face.
 * @param {() => void} handlers.onDelete - delete the recording, after the caller confirms.
 * @param {(session) => Node | null} [handlers.ask] - the "Ask about it" section's body, when the
 *   page offers one.
 */
export function renderOverview(container, session, { onWebApp, onDelete, ask } = {}) {
  if (!container) return;
  const media = session.media ?? {};

  // The date is already the window's subtitle; repeating it here is noise.
  const meta = [
    session.duration_seconds ? formatDuration(session.duration_seconds) : null,
    session.segments ? `${session.words} words` : null,
  ].filter(Boolean);

  const sections = [
    el("div", {
      className: "overview__summary",
      children: [
        el("p", { className: "overview__meta", text: meta.join(" · ") }),
        mediaChips(media),
      ],
    }),
  ];

  const askBody = ask?.(session);
  if (askBody) sections.push(section("Ask about it", [askBody]));

  if (media.transcript) sections.push(section("Transcript", transcriptExport(session)));

  if (session.chat_messages) {
    sections.push(
      section("Your conversation", [
        hint(
          `The ${session.chat_messages} messages you exchanged with the assistant, on their own. ` +
            "Not included in the transcript, and only in the web app if you ask for it.",
        ),
        el("a", {
          className: "button",
          text: "Download conversation",
          attrs: { href: api.sessionChatUrl(session.key, "markdown"), download: "" },
        }),
      ]),
    );
  }

  if (media.exportable) {
    const button = el("button", {
      className: "button",
      text: "Set up web app export…",
      attrs: { type: "button" },
    });
    button.addEventListener("click", () => onWebApp?.());
    sections.push(
      section("Web app", [
        hint(
          "The video, a transcript that follows it, and a question panel — as one ZIP that opens " +
            "with no server. Preview it and choose its size first.",
        ),
        button,
      ]),
    );
  }

  const remove = el("button", {
    className: "button button--danger",
    text: "Delete recording…",
    attrs: { type: "button", disabled: Boolean(session.running) },
  });
  remove.addEventListener("click", () => onDelete?.());
  sections.push(section("Delete", [remove], "danger"));

  container.replaceChildren(...sections);
}

/** Format, the pass to export when there are two (D-066), and the download itself. */
function transcriptExport(session) {
  const format = el("select", {
    className: "field__control field__control--compact",
    attrs: { "aria-label": "Transcript format" },
    children: FORMATS.map(([value, label]) => el("option", { text: label, attrs: { value } })),
  });

  // **Only where there is a choice (D-066).** A select with one option is chrome that explains
  // nothing — the same rule the transcript pane's Live/Final switch follows.
  const revisions = Array.isArray(session.revisions) ? session.revisions : [];
  const latest = revisions.length ? revisions[revisions.length - 1] : undefined;
  const version =
    revisions.length > 1
      ? el("select", {
          className: "field__control field__control--compact",
          attrs: { "aria-label": "Transcript version" },
          children: revisions.map((revision) =>
            el("option", {
              text: revisionLabel(revision),
              attrs: { value: String(revision), ...(revision === latest ? { selected: "" } : {}) },
            }),
          ),
        })
      : null;
  const chosenRevision = () => (version ? Number(version.value) : undefined);

  const download = el("a", {
    className: "button",
    text: "Download transcript",
    attrs: { href: api.sessionExportUrl(session.key, "markdown", chosenRevision()), download: "" },
  });
  // The link's href follows both selects, so the browser handles the download itself and the
  // filename comes from the server's own header rather than being invented here.
  const follow = () => {
    download.href = api.sessionExportUrl(session.key, format.value, chosenRevision());
  };
  format.addEventListener("change", follow);
  version?.addEventListener("change", follow);

  return [
    el("div", {
      className: "overview__row",
      children: [version, format, download].filter(Boolean),
    }),
  ];
}
