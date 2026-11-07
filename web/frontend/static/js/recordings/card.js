/**
 * One recording as a wide card (D-085): picture on the left, what was said on the right.
 *
 * The design the owner chose in the recordings lab (D-082): small, airy cards grouped by day; a
 * filmstrip of the video (one large frame and two small) that skims as the pointer crosses it, or
 * a colour for the time of day where there is no video; the media icons and the date on the
 * picture; length, words and size; and the buttons appearing on hover in the bottom-right corner.
 */

import { el } from "../core/dom.js";
import { iconNode } from "../core/icons.js";
import { api } from "../transport/api.js";
import { actionsFor } from "./actions.js";
import { bytes, cardTitle, duration, hourHue, onDisk, smartDate, startedAt } from "./describe.js";
import { actionMenu, closeMenus } from "./menu.js";

const MEDIA = [
  ["video", "Video"],
  ["audio", "Audio"],
  ["transcript", "Transcript"],
];

/** The two buttons a card shows on hover beside the main one; the rest are in the ⋯ menu. */
const QUICK = ["transcript", "folder"];

const SKIM_STEPS = 8;

/** Video, audio and transcript, each present or drawn faded. */
export function mediaIcons(session, { labelled = false } = {}) {
  const media = session.media ?? {};
  const has = MEDIA.filter(([key]) => media[key]).map(([, label]) => label.toLowerCase());
  return el("span", {
    className: "rmedia",
    attrs: { role: "img", "aria-label": has.length ? `Has ${has.join(", ")}` : "Holds nothing yet" },
    children: MEDIA.filter(([key]) => !labelled || media[key]).map(([key, label]) =>
      el("span", {
        className: "rmedia__item",
        attrs: { "data-present": String(Boolean(media[key])) },
        children: [iconNode(key, { size: 14 }), labelled ? el("span", { text: label }) : null],
      }),
    ),
  });
}

// -- the picture ---------------------------------------------------------------------------------

function image(src, onFail) {
  const img = el("img", {
    attrs: { src, alt: "", loading: "lazy", decoding: "async", draggable: "false" },
  });
  img.addEventListener("error", onFail, { once: true });
  return img;
}

function noVideo(session) {
  const node = el("div", { className: "rcard__art rcard__tint" });
  node.style.setProperty("--hue", String(hourHue(session)));
  node.append(iconNode(session.media?.audio ? "audio" : "transcript", { size: 28 }));
  return node;
}

/** Move the pointer across the picture and the large frame shows that moment of the video. */
function wireSkim(frame, img, session) {
  const length = session.duration_seconds || 0;
  const bar = el("span", { className: "rcard__skim" });
  frame.append(bar);
  const original = img.src;
  let shown = -1;
  frame.addEventListener("pointermove", (event) => {
    const box = frame.getBoundingClientRect();
    const fraction = Math.min(0.999, Math.max(0, (event.clientX - box.left) / box.width));
    const step = Math.floor(fraction * SKIM_STEPS);
    bar.style.setProperty("--at", `${((step + 0.5) / SKIM_STEPS) * 100}%`);
    if (step === shown) return;
    shown = step;
    img.src = api.thumbnailUrl(session.key, { seconds: ((step + 0.5) / SKIM_STEPS) * length });
  });
  frame.addEventListener("pointerleave", () => {
    shown = -1;
    img.src = original;
  });
}

function picture(session) {
  const frame = el("div", { className: "rcard__thumb" });
  const length = session.duration_seconds || 0;
  const fallback = () => frame.querySelector(".rcard__art")?.replaceWith(noVideo(session));

  if (!session.media?.video) {
    frame.append(noVideo(session));
  } else if (length >= 4) {
    const frames = [0.15, 0.5, 0.85].map((at) =>
      image(api.thumbnailUrl(session.key, { seconds: at * length }), fallback),
    );
    frame.append(el("div", { className: "rcard__art rcard__strip", children: frames }));
    wireSkim(frame, frames[0], session);
  } else {
    const src = session.media.preview ? api.thumbnailUrl(session.key) : api.thumbnailUrl(session.key, { seconds: 0 });
    const img = image(src, fallback);
    img.classList.add("rcard__art");
    frame.append(img);
  }

  frame.append(el("div", { className: "rcard__media", children: [mediaIcons(session)] }));
  if (length) frame.append(el("span", { className: "rcard__length", text: duration(length) }));
  const date = smartDate(startedAt(session));
  if (date) {
    frame.append(el("time", { className: "rcard__date", text: date, attrs: { datetime: session.started_at } }));
  }
  return frame;
}

// -- the buttons ---------------------------------------------------------------------------------

export function actionButton(action, { onAction, labelled = false, compact = false }) {
  const tag = action.href && !action.disabled ? "a" : "button";
  const classes = ["rbtn"];
  if (action.kind === "primary") classes.push("rbtn--primary");
  if (action.kind === "danger") classes.push("rbtn--danger");
  if (!labelled) classes.push("rbtn--icon");
  if (compact) classes.push("rbtn--compact");
  const node = el(tag, {
    className: classes.join(" "),
    attrs: {
      type: tag === "button" ? "button" : null,
      href: tag === "a" ? action.href : null,
      download: tag === "a" ? "" : null,
      title: action.disabled || action.label,
      "aria-label": labelled ? null : action.label,
      "aria-disabled": action.disabled ? "true" : null,
    },
    children: [iconNode(action.icon, { size: 15 }), labelled ? el("span", { text: action.label }) : null],
  });
  node.addEventListener("click", (event) => {
    event.stopPropagation();
    if (action.disabled) {
      event.preventDefault();
      return;
    }
    if (action.run) onAction(action);
  });
  return node;
}

function actionRow(session, ctx) {
  const all = actionsFor(session, ctx);
  const primary = all.find((action) => action.kind === "primary");
  const others = all.filter((action) => action !== primary);
  const onAction = (action) => ctx.onAction(action, session);

  const row = el("div", { className: "rcard__actions" });
  if (!ctx.viewing && !ctx.running) row.append(actionButton(primary, { onAction }));
  for (const action of others.filter((a) => QUICK.includes(a.id))) {
    row.append(actionButton(action, { onAction }));
  }
  row.append(actionMenu(others.filter((a) => !QUICK.includes(a.id)), { onAction, session }));
  return row;
}

// -- the card ------------------------------------------------------------------------------------

/**
 * @param {object} session - a row from `/api/sessions`
 * @param {object} ctx - `{ glance, busy, viewing, running, selected, onOpen, onAction }`
 */
export function renderCard(session, ctx) {
  const title = cardTitle(session, ctx.glance);
  const stats = [
    session.words ? `${session.words.toLocaleString()} words` : "",
    onDisk(session) ? bytes(onDisk(session)) : "",
  ].filter(Boolean);
  const said = session.problem || ctx.glance?.summary || ctx.glance?.opening || "";
  const badge = ctx.running ? "Recording now" : ctx.viewing ? "On the main screen" : "";

  const body = el("div", {
    className: "rcard__body",
    children: [
      el("h3", {
        className: "rcard__title",
        attrs: { title: title.auto ? `${title.text} — made from the transcript` : title.text },
        children: [
          title.auto ? iconNode("spark", { size: 11, className: "icon rcard__auto" }) : null,
          el("span", { text: title.text }),
        ],
      }),
      badge ? el("span", { className: "rcard__badge", text: badge }) : null,
      said
        ? el("p", { className: `rcard__said${session.problem ? " rcard__said--problem" : ""}`, text: said })
        : null,
      stats.length ? el("p", { className: "rcard__stats", text: stats.join(" · ") }) : null,
      actionRow(session, ctx),
    ],
  });

  const date = smartDate(startedAt(session));
  const card = el("article", {
    className: "rcard",
    attrs: {
      role: "listitem",
      tabindex: "0",
      "data-key": session.key,
      "data-selected": ctx.selected ? "true" : null,
      "aria-label": [title.text, date, badge].filter(Boolean).join(", "),
    },
    children: [picture(session), body],
  });
  card.addEventListener("click", () => {
    closeMenus();
    ctx.onOpen(session);
  });
  card.addEventListener("keydown", (event) => {
    if (event.target === card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      ctx.onOpen(session);
    }
  });
  return card;
}
