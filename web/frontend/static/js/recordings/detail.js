/**
 * Everything about one recording, in the space it needs (D-085).
 *
 * Replaces the tall recording window's overview face (D-077), whose sections stacked down a dialog
 * that was mostly empty. A compact window instead: the video beside the main button, the numbers
 * and what was said; downloads and file actions in one row underneath. The title can be renamed in
 * place (D-084), the transcript's version chosen where there are two (D-066), and the web-app
 * export opens its own window from here.
 */

import { el } from "../core/dom.js";
import { iconNode } from "../core/icons.js";
import { revisionLabel } from "../core/recording-facts.js";
import { api } from "../transport/api.js";
import { TRANSCRIPT_FORMATS, actionsFor, transcriptUrl } from "./actions.js";
import { actionButton, mediaIcons } from "./card.js";
import { bytes, cardTitle, duration, fullDate, onDisk, startedAt } from "./describe.js";

function player(session) {
  const media = session.media ?? {};
  if (media.video) {
    return el("video", {
      className: "rdetail__player",
      attrs: {
        controls: true,
        preload: "none",
        poster: api.thumbnailUrl(session.key),
        src: api.sessionMediaUrl(session.key),
      },
    });
  }
  return el("div", {
    className: "rdetail__player rdetail__player--none",
    children: [
      iconNode(media.audio || media.audio_file ? "audio" : "transcript", { size: 30 }),
      el("span", {
        text: media.audio_file ? "Audio only — no video was recorded" : "No video was recorded",
      }),
    ],
  });
}

function facts(session) {
  const rows = [
    ["Length", session.duration_seconds ? duration(session.duration_seconds) : "—"],
    ["Words", session.words ? session.words.toLocaleString() : "—"],
    ["Summaries", String(session.summaries ?? 0)],
    ["Glossary", String(session.glossary_terms ?? 0)],
    ["Questions", String(session.chat_messages ?? 0)],
    ["On disk", bytes(onDisk(session))],
  ];
  return el("dl", {
    className: "rdetail__facts",
    children: rows.map(([term, value]) =>
      el("div", { children: [el("dt", { text: term }), el("dd", { text: value })] }),
    ),
  });
}

function group(label, children) {
  const items = children.filter(Boolean);
  if (!items.length) return null;
  return el("section", {
    className: "rdetail__group",
    attrs: { "aria-label": label },
    children: [
      el("h3", { className: "rdetail__group-label", text: label }),
      el("div", { className: "rdetail__group-items", children: items }),
    ],
  });
}

/** The title, and a Rename button that turns it into a field. */
function heading(session, title, { onRename }) {
  const name = el("h2", {
    className: "rdetail__title",
    attrs: { id: "recording-detail-title" },
    children: [
      title.auto ? iconNode("spark", { size: 12, className: "icon rcard__auto" }) : null,
      el("span", { text: title.text }),
    ],
  });
  const rename = el("button", {
    className: "rbtn rbtn--compact rdetail__rename",
    text: "Rename",
    attrs: { type: "button", "aria-label": "Rename this recording" },
  });
  const wrap = el("div", { className: "rdetail__name", children: [name, rename] });

  rename.addEventListener("click", () => {
    const input = el("input", {
      className: "rdetail__name-input",
      attrs: {
        type: "text",
        value: session.named ? session.title : "",
        placeholder: title.text,
        maxlength: "120",
        "aria-label": "New title",
      },
    });
    const save = el("button", { className: "rbtn rbtn--primary rbtn--compact", text: "Save", attrs: { type: "submit" } });
    const cancel = el("button", { className: "rbtn rbtn--compact", text: "Cancel", attrs: { type: "button" } });
    const form = el("form", { className: "rdetail__name rdetail__name--editing", children: [input, save, cancel] });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (input.value.trim()) await onRename(session, input.value);
    });
    cancel.addEventListener("click", () => {
      form.replaceWith(wrap);
      rename.focus();
    });
    form.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        cancel.click();
      }
    });
    wrap.replaceWith(form);
    input.focus();
    input.select();
  });
  return wrap;
}

/** Each format as a link, following the chosen version when there are two (D-066). */
function transcriptGroup(session, onAction) {
  if (session.orphan || !session.segments) return null;
  const revisions = Array.isArray(session.revisions) ? session.revisions : [];
  const latest = revisions.at(-1);
  const links = TRANSCRIPT_FORMATS.map(([fmt, , short]) =>
    actionButton(
      { id: `transcript-${fmt}`, label: short, icon: "transcript", href: transcriptUrl(session.key, fmt, latest) },
      { onAction, labelled: true, compact: true },
    ),
  );
  let version = null;
  if (revisions.length > 1) {
    version = el("select", {
      className: "rdetail__select",
      attrs: { "aria-label": "Transcript version" },
      children: revisions.map((revision) =>
        el("option", {
          text: revisionLabel(revision),
          attrs: { value: String(revision), selected: revision === latest ? "" : null },
        }),
      ),
    });
    version.addEventListener("change", () => {
      TRANSCRIPT_FORMATS.forEach(([fmt], index) => {
        links[index].href = transcriptUrl(session.key, fmt, Number(version.value));
      });
    });
  }
  return group("Transcript", [version, ...links]);
}

/**
 * @param {object} session
 * @param {object} ctx - `{ glance, busy, viewing, running, onAction, onClose, onRename }`
 */
export function renderDetail(session, ctx) {
  const title = cardTitle(session, ctx.glance);
  const actions = actionsFor(session, ctx);
  const find = (id) => actions.find((action) => action.id === id);
  const onAction = (action) => ctx.onAction(action, session);
  const primary = find("primary");
  const compact = { onAction, labelled: true, compact: true };

  const close = el("button", {
    className: "rbtn rbtn--icon rdetail__close",
    attrs: { type: "button", "aria-label": "Close", title: "Close (Esc)" },
    children: [iconNode("close", { size: 15 })],
  });
  close.addEventListener("click", () => ctx.onClose());

  const head = el("header", {
    className: "rdetail__head",
    children: [
      el("div", {
        className: "rdetail__head-text",
        children: [
          heading(session, title, ctx),
          el("p", {
            className: "rdetail__sub",
            children: [
              el("time", { text: fullDate(startedAt(session)), attrs: { datetime: session.started_at } }),
              mediaIcons(session, { labelled: true }),
            ],
          }),
        ],
      }),
      close,
    ],
  });

  const status = ctx.running ? "Recording now" : ctx.viewing ? "On the main screen now" : "";
  const use = el("div", {
    className: "rdetail__use",
    children: [
      status ? el("span", { className: "rcard__badge", text: status }) : actionButton(primary, { onAction, labelled: true }),
      primary?.disabled && !status ? el("p", { className: "rdetail__reason", text: primary.disabled }) : null,
    ],
  });

  const glance = ctx.glance;
  const said = glance?.summary || glance?.opening;
  const about = el("section", {
    className: "rdetail__about",
    attrs: { "aria-label": "What was said" },
    children: [
      glance?.keywords?.length
        ? el("div", { className: "rdetail__keywords", children: glance.keywords.map((word) => el("span", { text: word })) })
        : null,
      el("p", {
        className: `rdetail__said${said ? "" : " rdetail__said--none"}`,
        text: said || session.problem || "Nothing was transcribed.",
      }),
    ],
  });

  return el("div", {
    className: "rdetail",
    attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": "recording-detail-title", "data-key": session.key },
    children: [
      head,
      el("div", {
        className: "rdetail__top",
        children: [player(session), el("div", { className: "rdetail__side", children: [use, facts(session), about] })],
      }),
      el("div", {
        className: "rdetail__groups",
        children: [
          transcriptGroup(session, onAction),
          group(
            "Download",
            ["chat", "video", "audio", "webapp"].map((id) => find(id) && actionButton(find(id), compact)),
          ),
          group("Files", [find("folder") && actionButton(find("folder"), compact), actionButton(find("delete"), compact)]),
        ],
      }),
    ],
  });
}
