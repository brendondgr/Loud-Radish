/**
 * One recording as a card, in whichever arrangement the lab is set to (D-082).
 *
 * The DOM is the same for every layout — picture, then body — and the layout is a class on the
 * page. That keeps six layouts to one renderer, and means switching between them while comparing
 * costs a class change rather than a different code path that might drift.
 */

import { el } from "../core/dom.js";
import { actionsFor } from "./actions.js";
import { bytes, cardTitle, duration, formatDate, hourHue, snippet, startedAt } from "./describe.js";
import { iconNode } from "./icons.js";
import { actionMenu, closeMenus } from "./menu.js";

const MEDIA = [
  ["video", "Video"],
  ["audio", "Audio"],
  ["transcript", "Transcript"],
];

const SCRUB_STEPS = 8;

const thumbUrl = (key, seconds) =>
  `/api/labs/sessions/${encodeURIComponent(key)}/thumbnail` +
  (seconds === undefined ? "" : `?t=${Math.max(0, seconds).toFixed(1)}`);

/** The media icons: video, audio, transcript, each present or (optionally) drawn faded. */
export function mediaIcons(session, { labelled = false, showMissing = true } = {}) {
  const media = session.media ?? {};
  const items = MEDIA.filter(([key]) => showMissing || media[key]).map(([key, label]) => {
    const present = Boolean(media[key]);
    const item = el("span", {
      className: "lc-media__item",
      attrs: { "data-present": String(present), title: present ? `Has ${label.toLowerCase()}` : `No ${label.toLowerCase()}` },
      children: [iconNode(key, { size: 14 })],
    });
    if (labelled) item.append(el("span", { text: label }));
    return item;
  });
  return el("span", {
    className: "lc-media",
    attrs: { role: "img", "aria-label": mediaSentence(media) },
    children: items,
  });
}

function mediaSentence(media) {
  const has = MEDIA.filter(([key]) => media[key]).map(([, label]) => label.toLowerCase());
  return has.length ? `Has ${has.join(", ")}` : "Holds nothing yet";
}

// -- the picture ---------------------------------------------------------------------------------

function seeded(key) {
  let state = [...key].reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 7);
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

function wave(key) {
  const random = seeded(key);
  const bars = Array.from({ length: 48 }, (_, index) => {
    const envelope = 0.35 + 0.65 * Math.sin((index / 47) * Math.PI) ** 0.6;
    const height = Math.max(0.08, envelope * (0.25 + random() * 0.75));
    const y = 50 - height * 42;
    return `<rect x="${index * 4 + 2}" y="${y.toFixed(1)}" width="2.2" height="${(height * 84).toFixed(1)}" rx="1.1"/>`;
  }).join("");
  const node = el("div", { className: "lc-placeholder lc-placeholder--wave" });
  node.innerHTML = `<svg viewBox="0 0 196 100" preserveAspectRatio="none" aria-hidden="true" fill="currentColor">${bars}</svg>`;
  return node;
}

function placeholder(session, glance, settings) {
  const media = session.media ?? {};
  // A slim row's picture is too small for words to be read.
  const kind = settings.layout === "list" && settings.noVideo === "words" ? "icon" : settings.noVideo;
  // Not when the card already prints what was said underneath; the same words twice is noise.
  if (kind === "words" && glance?.opening && !settings.snippet) {
    return el("div", {
      className: "lc-placeholder lc-placeholder--words",
      children: [el("p", { text: glance.opening })],
    });
  }
  if (kind === "wave" && (media.audio || media.audio_file)) return wave(session.key);
  if (kind === "tint" || kind === "wave") {
    const node = el("div", { className: "lc-placeholder lc-placeholder--tint" });
    node.style.setProperty("--hue", String(hourHue(session)));
    node.append(iconNode(media.audio ? "audio" : "transcript", { size: 28 }));
    return node;
  }
  return el("div", {
    className: "lc-placeholder lc-placeholder--icon",
    children: [iconNode(media.audio || media.audio_file ? "audio" : "transcript", { size: 34 })],
  });
}

function image(src, onFail) {
  const img = el("img", { attrs: { src, alt: "", loading: "lazy", decoding: "async", draggable: "false" } });
  img.addEventListener("error", onFail, { once: true });
  return img;
}

/** Move the pointer across the picture and it shows that moment of the video. */
function wireScrub(frame, img, session) {
  const length = session.duration_seconds || 0;
  if (length < 4) return;
  const bar = el("span", { className: "lc-scrub" });
  frame.append(bar);
  let shown = -1;
  const original = img.src;
  frame.addEventListener("pointermove", (event) => {
    const box = frame.getBoundingClientRect();
    const fraction = Math.min(0.999, Math.max(0, (event.clientX - box.left) / box.width));
    const step = Math.floor(fraction * SCRUB_STEPS);
    bar.style.setProperty("--at", `${((step + 0.5) / SCRUB_STEPS) * 100}%`);
    if (step === shown) return;
    shown = step;
    img.src = thumbUrl(session.key, ((step + 0.5) / SCRUB_STEPS) * length);
  });
  frame.addEventListener("pointerleave", () => {
    shown = -1;
    img.src = original;
  });
}

function picture(session, glance, settings) {
  const media = session.media ?? {};
  const frame = el("div", { className: "lc-thumb" });
  const fallback = () => frame.querySelector(".lc-thumb__art")?.replaceWith(art(true));

  function art(failed = false) {
    if (!media.video || failed) {
      const node = placeholder(session, glance, settings);
      node.classList.add("lc-thumb__art");
      return node;
    }
    const length = session.duration_seconds || 0;
    if (settings.thumb === "strip" && length >= 4) {
      return el("div", {
        className: "lc-thumb__art lc-strip",
        children: [0.15, 0.5, 0.85].map((at) => image(thumbUrl(session.key, at * length), fallback)),
      });
    }
    const middle = settings.thumb === "middle" && length >= 4;
    const img = image(thumbUrl(session.key, middle ? length / 2 : undefined), fallback);
    img.classList.add("lc-thumb__art");
    if (settings.scrub) queueMicrotask(() => wireScrub(frame, img, session));
    return img;
  }

  frame.append(art());
  return frame;
}

// -- the buttons ---------------------------------------------------------------------------------

export function actionButton(action, { onAction, labelled = false, compact = false }) {
  const tag = action.href && !action.disabled ? "a" : "button";
  const classes = ["lc-btn"];
  if (action.kind === "primary") classes.push("lc-btn--primary");
  if (action.kind === "danger") classes.push("lc-btn--danger");
  if (!labelled) classes.push("lc-btn--icon");
  if (compact) classes.push("lc-btn--compact");
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
  const { settings } = ctx;
  const all = actionsFor(session, ctx);
  const primary = all.find((action) => action.kind === "primary");
  const others = all.filter((action) => action !== primary);
  const quick = others.filter((action) => settings.quick.includes(action.id));
  const style = settings.actionStyle;
  const onAction = (action) => ctx.onAction(action, session);

  const row = el("div", { className: `lc-actions lc-actions--${style}` });
  const primaryLabelled = style !== "hover";
  if (ctx.viewing) {
    row.append(el("span", { className: "lc-badge lc-badge--live", text: "On the main screen" }));
  } else {
    row.append(actionButton(primary, { onAction, labelled: primaryLabelled }));
  }
  if (style === "bar" || style === "hover") {
    for (const action of quick) row.append(actionButton(action, { onAction }));
  }
  if (style === "labels") {
    for (const action of quick) row.append(actionButton(action, { onAction, labelled: true, compact: true }));
  }
  const rest = style === "menu" ? others : others.filter((action) => !quick.includes(action));
  if (rest.length) row.append(actionMenu(rest, { onAction, session }));
  return row;
}

// -- the card ------------------------------------------------------------------------------------

function statsLine(session, settings) {
  const parts = [];
  const want = new Set(settings.stats);
  if (want.has("duration") && session.duration_seconds) parts.push(duration(session.duration_seconds));
  if (want.has("words") && session.words) parts.push(`${session.words.toLocaleString()} words`);
  if (want.has("size") && session.size_bytes + (session.media?.recording_bytes ?? 0)) {
    parts.push(bytes(session.size_bytes + (session.media?.recording_bytes ?? 0)));
  }
  if (want.has("summaries") && session.summaries) parts.push(`${session.summaries} summaries`);
  if (want.has("chat") && session.chat_messages) parts.push(`${session.chat_messages} questions`);
  return parts;
}

/**
 * @param {object} session - a row from `/api/sessions`
 * @param {object} ctx - `{ settings, glance, busy, viewing, onOpen, onAction }`
 */
export function renderCard(session, ctx) {
  const { glance } = ctx;
  // A slim row has no room for icons on its picture, and a wide card exists to show what was said.
  const settings = {
    ...ctx.settings,
    mediaStyle: ctx.settings.layout === "list" && ctx.settings.mediaStyle === "overlay" ? "footer" : ctx.settings.mediaStyle,
    snippet: ctx.settings.snippet || ctx.settings.layout === "wide",
  };
  ctx = { ...ctx, settings };
  const title = cardTitle(session, glance, settings.title);
  const date = formatDate(startedAt(session), settings.date);
  const stats = statsLine(session, settings);

  const thumb = picture(session, glance, settings);
  if (settings.mediaStyle === "overlay") {
    thumb.append(el("div", { className: "lc-thumb__media", children: [mediaIcons(session, settings)] }));
  }
  if (stats.length && settings.stats.includes("duration") && session.duration_seconds) {
    thumb.append(el("span", { className: "lc-thumb__length", text: duration(session.duration_seconds) }));
  }
  if (settings.datePlace === "image" && date) {
    thumb.append(el("span", { className: "lc-thumb__date", text: date }));
  }

  const heading = el("h3", {
    className: "lc-title",
    attrs: { title: title.auto ? `${title.text} — made from the transcript` : title.text },
    children: [title.auto ? iconNode("spark", { size: 11, className: "lc-title__auto" }) : null, el("span", { text: title.text })],
  });
  const dateLine =
    settings.datePlace !== "image" || settings.mediaStyle === "footer" || settings.mediaStyle === "labelled"
      ? el("div", {
          className: "lc-meta",
          children: [
            settings.datePlace !== "image" && date ? el("time", { text: date, attrs: { datetime: session.started_at } }) : null,
            settings.mediaStyle === "footer" || settings.mediaStyle === "labelled"
              ? mediaIcons(session, { labelled: settings.mediaStyle === "labelled", showMissing: settings.showMissing })
              : null,
          ],
        })
      : null;
  const otherStats = stats.filter((part) => part !== duration(session.duration_seconds));
  const text = settings.snippet ? snippet(glance) : "";

  const body = el("div", {
    className: "lc-body",
    children: [
      settings.datePlace === "above" ? dateLine : null,
      heading,
      settings.datePlace !== "above" ? dateLine : null,
      text ? el("p", { className: "lc-snippet", text }) : null,
      otherStats.length ? el("p", { className: "lc-stats", text: otherStats.join(" · ") }) : null,
      actionRow(session, ctx),
    ],
  });

  const card = el("article", {
    className: "lc-card",
    attrs: {
      role: "listitem",
      tabindex: "0",
      "data-key": session.key,
      "data-selected": ctx.selected ? "true" : null,
      "aria-label": `${title.text}, ${date}`,
    },
    children: [thumb, body],
  });
  card.addEventListener("click", () => {
    closeMenus();
    ctx.onOpen(session, card);
  });
  card.addEventListener("keydown", (event) => {
    if (event.target === card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      ctx.onOpen(session, card);
    }
  });
  return card;
}
