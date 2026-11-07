/**
 * Everything about one recording, in the space it needs and no more (D-080).
 *
 * The replacement being tried for today's "Open…" window, whose sections stack down a tall dialog
 * with most of it empty. Here the picture and the buttons share the top, what was said comes next,
 * and the numbers fold into one compact grid. The same content goes into a side panel, a compact
 * window, a card expanded in place, or the split view's right-hand pane.
 */

import { el } from "../core/dom.js";
import { TRANSCRIPT_FORMATS, actionsFor, transcriptUrl } from "./actions.js";
import { actionButton, mediaIcons } from "./card.js";
import { bytes, cardTitle, duration, formatDate, startedAt } from "./describe.js";
import { iconNode } from "./icons.js";

const enc = encodeURIComponent;

function player(session) {
  const media = session.media ?? {};
  if (media.video) {
    return el("video", {
      className: "ld-player",
      attrs: {
        controls: true,
        preload: "none",
        poster: `/api/labs/sessions/${enc(session.key)}/thumbnail`,
        src: `/api/sessions/${enc(session.key)}/media`,
      },
    });
  }
  return el("div", {
    className: "ld-player ld-player--none",
    children: [
      iconNode(media.audio || media.audio_file ? "audio" : "transcript", { size: 30 }),
      el("span", { text: media.audio_file ? "Audio only — no video was recorded" : "No video was recorded" }),
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
    ["On disk", bytes((session.size_bytes ?? 0) + (session.media?.recording_bytes ?? 0))],
  ];
  return el("dl", {
    className: "ld-facts",
    children: rows.map(([term, value]) => el("div", { children: [el("dt", { text: term }), el("dd", { text: value })] })),
  });
}

function group(label, children) {
  const items = children.filter(Boolean);
  if (!items.length) return null;
  return el("section", {
    className: "ld-group",
    children: [el("h4", { className: "ld-group__label", text: label }), el("div", { className: "ld-group__items", children: items })],
  });
}

/**
 * @param {object} session
 * @param {object} ctx - `{ settings, glance, busy, viewing, onAction, onClose, mode }`
 */
export function renderDetail(session, ctx) {
  const { glance, settings } = ctx;
  const title = cardTitle(session, glance, settings.title);
  const actions = actionsFor(session, ctx);
  const find = (id) => actions.find((action) => action.id === id);
  const onAction = (action) => ctx.onAction(action, session);
  const primary = find("primary");

  const head = el("header", {
    className: "ld-head",
    children: [
      el("div", {
        className: "ld-head__text",
        children: [
          el("h2", {
            className: "ld-title",
            children: [title.auto ? iconNode("spark", { size: 12, className: "lc-title__auto" }) : null, el("span", { text: title.text })],
          }),
          el("p", {
            className: "ld-sub",
            children: [
              el("time", { text: formatDate(startedAt(session), "absolute"), attrs: { datetime: session.started_at } }),
              mediaIcons(session, { labelled: true, showMissing: false }),
            ],
          }),
        ],
      }),
      ctx.mode === "split"
        ? null
        : el("button", {
            className: "lc-btn lc-btn--icon ld-close",
            attrs: { type: "button", "aria-label": "Close", title: "Close (Esc)" },
            children: [iconNode("close", { size: 15 })],
          }),
    ],
  });
  head.querySelector(".ld-close")?.addEventListener("click", (event) => {
    event.stopPropagation();
    ctx.onClose();
  });

  const transcript = find("transcript");
  const downloads = group("Download", [
    ...(transcript
      ? TRANSCRIPT_FORMATS.map(([fmt, , short]) =>
          actionButton({ ...transcript, label: short, href: transcriptUrl(session.key, fmt) }, { onAction, labelled: true, compact: true }),
        )
      : []),
    ...["video", "audio", "webapp"].map((id) => find(id) && actionButton(find(id), { onAction, labelled: true, compact: true })),
  ]);
  const files = group("Files", [
    find("folder") && actionButton(find("folder"), { onAction, labelled: true, compact: true }),
    actionButton(find("delete"), { onAction, labelled: true, compact: true }),
  ]);

  const use = el("div", {
    className: "ld-use",
    children: [
      ctx.viewing
        ? el("span", { className: "lc-badge lc-badge--live", text: "On the main screen now" })
        : actionButton(primary, { onAction, labelled: true }),
      primary?.disabled ? el("p", { className: "ld-reason", text: primary.disabled }) : null,
    ],
  });

  const said = glance?.summary || glance?.opening;
  const about = el("section", {
    className: "ld-about",
    children: [
      glance?.keywords?.length
        ? el("div", { className: "ld-keywords", children: glance.keywords.map((word) => el("span", { text: word })) })
        : null,
      said ? el("p", { className: "ld-said", text: said }) : el("p", { className: "ld-said ld-said--none", text: "Nothing was transcribed." }),
    ],
  });

  // Where the window is wide enough for two columns, what was said fills the one beside the video.
  const beside = ctx.mode === "sheet" || ctx.mode === "inline";
  return el("div", {
    className: `ld ld--${ctx.mode}`,
    attrs: { "data-key": session.key },
    children: [
      head,
      el("div", {
        className: "ld-top",
        children: [player(session), el("div", { className: "ld-side", children: [use, facts(session), beside ? about : null] })],
      }),
      beside ? null : about,
      el("div", { className: "ld-groups", children: [downloads, files] }),
    ],
  });
}
