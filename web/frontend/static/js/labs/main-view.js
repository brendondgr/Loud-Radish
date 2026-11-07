/**
 * The main screen, drawn in the lab's style (D-082) — header, viewing banner, transcript,
 * assistant, video, status — from one of the user's own recordings.
 *
 * **Look-only.** Nothing here records, asks or searches: every control carries `data-mock`, and
 * the page answers a click on one by saying so. What is shown is real — the transcript, the
 * conversation, the glossary and the video frame all come from a past recording — so the style is
 * judged against the content it will actually hold.
 */

import { el } from "../core/dom.js";
import { duration, formatDate, startedAt } from "./describe.js";
import { iconNode } from "./icons.js";

const mock = (label, { icon, text, className = "lm-btn", title } = {}) =>
  el("button", {
    className,
    attrs: { type: "button", "data-mock": label, title: title ?? label, "aria-label": text ? null : label },
    children: [icon ? iconNode(icon, { size: 15 }) : null, text ? el("span", { text }) : null],
  });

/** The glossary, with how many terms it holds inside the button (owner's note, third round). */
function glossaryButton(count, { compact = false } = {}) {
  const button = mock("Glossary", { icon: "book", text: compact ? null : "Glossary", className: compact ? "lm-btn lm-btn--quiet lm-btn--counted" : "lm-btn lm-btn--quiet" });
  button.setAttribute("aria-label", `Glossary, ${count} terms`);
  button.append(el("span", { className: "lm-badge", text: String(count) }));
  return button;
}

function clock(seconds) {
  const total = Math.round(seconds || 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}

// -- header and health -------------------------------------------------------------------------------

function recordButton(style) {
  if (style === "round") {
    return el("button", {
      className: "lm-record lm-record--round",
      attrs: { type: "button", "data-mock": "Start recording", title: "Start recording", "aria-label": "Start recording" },
      children: [el("span", { className: "lm-record__dot" })],
    });
  }
  if (style === "icon") return mock("Start recording", { icon: "record", className: "lm-record lm-record--icon" });
  return mock("Start recording", { icon: "record", text: "Start recording", className: "lm-record lm-record--pill" });
}

export function health(content) {
  const bars = Array.from({ length: 10 }, (_, index) =>
    el("span", { className: "lm-meter__bar", attrs: { "data-on": String(index < 4) } }),
  );
  return el("div", {
    className: "lm-health",
    children: [
      el("span", { className: "lm-health__item", children: [iconNode("mic", { size: 13 }), el("span", { className: "lm-meter", children: bars })] }),
      el("span", { className: "lm-health__item", text: "Speech" }),
      el("span", { className: "lm-health__item lm-mono", text: "0.42× realtime" }),
      el("span", { className: "lm-health__item lm-mono", text: "lag 1.2s" }),
      el("span", {
        className: "lm-health__item lm-health__ok",
        children: [el("span", { className: "lm-dot" }), el("span", { text: content.model })],
      }),
    ],
  });
}

function viewingChip(content) {
  return el("span", {
    className: "lm-viewing-chip",
    children: [iconNode("transcript", { size: 13 }), el("span", { text: content.title }), mock("Close", { icon: "close", className: "lm-chip-close" })],
  });
}

export function header(content, settings, hooks) {
  const assistantToggle =
    settings.layout === "focus"
      ? mock("Assistant", { icon: "ask", text: "Assistant", className: "lm-btn lm-btn--quiet lm-assistant-toggle" })
      : null;
  assistantToggle?.removeAttribute("data-mock");
  assistantToggle?.addEventListener("click", hooks.onToggleAssistant);

  return el("header", {
    className: "lm-head lm-region",
    children: [
      el("a", {
        className: "lm-brand",
        attrs: { href: "/", title: "The real main screen" },
        children: [el("img", { attrs: { src: hooks.logo, alt: "", width: "22", height: "22" } }), el("span", { className: "lm-lab", text: "Lab" })],
      }),
      el("span", { className: "lm-state", children: [el("span", { className: "lm-dot lm-dot--idle" }), el("span", { text: "Not recording" })] }),
      el("span", { className: "lm-clock", text: clock(0) }),
      recordButton(settings.record),
      settings.banner === "chip" ? viewingChip(content) : null,
      el("span", { className: "lm-spacer" }),
      settings.health === "header" ? health(content) : null,
      el("span", { className: "lm-local", children: [iconNode("shield", { size: 13 }), el("span", { text: "Fully local" })] }),
      assistantToggle,
      mock("Recordings", { icon: "grid", text: "Recordings" }),
      mock("Settings", { icon: "gear", className: "lm-btn lm-btn--icon" }),
    ],
  });
}

export function banner(content) {
  return el("div", {
    className: "lm-banner",
    children: [
      iconNode("transcript", { size: 14 }),
      el("div", {
        className: "lm-banner__text",
        children: [
          el("strong", { text: `Viewing ${content.title}` }),
          el("span", { text: ` · ${formatDate(startedAt(content.session), "absolute")}` }),
          el("p", { text: "Ask the assistant about it — your earlier questions are below." }),
        ],
      }),
      mock("Open another recording", { text: "Open another…", className: "lm-btn lm-btn--quiet" }),
      mock("Close", { text: "Close", className: "lm-btn" }),
    ],
  });
}

// -- the transcript ----------------------------------------------------------------------------------

function paragraphs(segments) {
  const out = [];
  let current = null;
  for (const segment of segments) {
    if (!current || current.parts.length >= 4 || segment.start - current.end > 4) {
      current = { start: segment.start, end: segment.end, parts: [] };
      out.push(current);
    }
    current.parts.push(segment.text.trim());
    current.end = segment.end;
  }
  return out;
}

function transcriptToolbar(content, settings) {
  const compact = settings.toolbar === "compact";
  return el("div", {
    className: "lm-pane__head",
    children: [
      el("span", { className: "lm-pane__title", text: "Transcript" }),
      el("span", { className: "lm-count", text: `${content.detail.stats?.segments ?? content.detail.segments.length} segments` }),
      el("span", {
        className: "lm-segmented",
        children: [mock("Live pass", { text: "Live", className: "lm-seg" }), mock("Final pass", { text: "Final", className: "lm-seg lm-seg--on" })],
      }),
      el("span", { className: "lm-spacer" }),
      compact ? mock("Open a recording", { icon: "folder", className: "lm-btn lm-btn--icon" }) : mock("Open a recording", { text: "Open recording…", className: "lm-btn lm-btn--quiet" }),
      compact
        ? mock("Search the transcript", { icon: "search", className: "lm-btn lm-btn--icon" })
        : el("label", { className: "lm-search", children: [iconNode("search", { size: 13 }), el("span", { text: "Search transcript" })] }),
      glossaryButton(content.detail.glossary.length, { compact }),
    ],
  });
}

export function transcriptPane(content, settings) {
  const body = el("div", {
    className: "lm-text",
    children: paragraphs(content.detail.segments).map((para) =>
      el("div", {
        className: "lm-para",
        children: [
          settings.stamps === "hidden" ? null : el("span", { className: "lm-stamp", text: clock(para.start) }),
          el("p", { text: para.parts.join(" ") }),
        ],
      }),
    ),
  });
  return el("section", {
    className: "lm-pane lm-transcript lm-region",
    attrs: { "aria-label": "Transcript" },
    children: [
      transcriptToolbar(content, settings),
      settings.banner === "card" ? el("div", { className: "lm-banner-card", children: [banner(content)] }) : null,
      el("div", { className: "lm-scroll", children: [body] }),
    ],
  });
}

// -- the assistant and the video ----------------------------------------------------------------

export function monitor(content) {
  const media = content.session.media ?? {};
  return el("figure", {
    className: "lm-monitor",
    children: [
      media.video
        ? el("img", { attrs: { src: `/api/labs/sessions/${encodeURIComponent(content.session.key)}/thumbnail`, alt: "" } })
        : el("div", { className: "lm-monitor__none", children: [iconNode("video", { size: 22 }), el("span", { text: "No video in this recording" })] }),
      el("figcaption", {
        children: [el("span", { className: "lm-dot lm-dot--idle" }), el("span", { text: "Window" }), el("span", { className: "lm-mono", text: duration(content.session.duration_seconds) })],
      }),
    ],
  });
}

function fact(term, value) {
  return el("div", { children: [el("dt", { text: term }), el("dd", { text: value })] });
}

function section(title, children, action) {
  return el("section", {
    className: "lm-info__section",
    children: [
      el("div", { className: "lm-info__head", children: [el("h3", { text: title }), action ?? null] }),
      ...children,
    ],
  });
}

/**
 * The details column: the video, what this recording is, how it was captured, and its glossary —
 * everything that is not the transcript or the conversation, where it can all be seen at once
 * (owner's note, third round).
 */
export function infoPane(content, settings) {
  const { session, detail } = content;
  const model = (detail.segments.find((s) => s.model_id)?.model_id ?? "").split(":").filter(Boolean).join(" · ");
  const modes = { live: "Live, as it is said", recorded: "After the recording", window: "Live, and again after" };
  const glossary = detail.glossary ?? [];
  return el("aside", {
    className: "lm-pane lm-info lm-region",
    attrs: { "aria-label": "Video and details" },
    children: [
      el("div", { className: "lm-pane__head", children: [el("span", { className: "lm-pane__title", text: "Recording" })] }),
      el("div", {
        className: "lm-scroll",
        children: [
          monitor(content),
          settings.health === "column" ? section("Now", [health(content)]) : null,
          section("This recording", [
            el("dl", {
              className: "lm-facts",
              children: [
                fact("Started", formatDate(startedAt(session), "smart")),
                fact("Length", duration(session.duration_seconds)),
                fact("Words", (session.words ?? 0).toLocaleString()),
                fact("Questions", String(session.chat_messages ?? 0)),
              ],
            }),
          ]),
          section(
            "Capture",
            [
              el("dl", {
                className: "lm-facts lm-facts--rows",
                children: [
                  fact("Source", session.media?.video ? "A window, with its sound" : "Microphone"),
                  fact("Transcribed", modes[detail.session?.mode] ?? "Live"),
                  fact("Model", model || "—"),
                ],
              }),
            ],
            mock("Capture settings", { icon: "gear", className: "lm-btn lm-btn--icon" }),
          ),
          section(
            "Glossary",
            glossary.length
              ? [
                  el("ul", {
                    className: "lm-terms",
                    children: glossary.slice(0, 8).map((term) =>
                      el("li", { children: [el("strong", { text: term.term }), el("span", { text: term.definition })] }),
                    ),
                  }),
                ]
              : [el("p", { className: "lm-muted", text: "No terms yet." })],
            el("span", { className: "lm-badge", text: String(glossary.length) }),
          ),
        ],
      }),
    ],
  });
}

function messages(content, settings) {
  const chat = content.detail.chat ?? [];
  if (!chat.length) {
    return el("div", {
      className: "lm-empty",
      children: [
        el("h3", { text: "Ask about the talk" }),
        el("p", { text: "Answers come from the transcript and nothing else, with a timestamp you can click to jump to what was said." }),
      ],
    });
  }
  return el("div", {
    className: `lm-messages lm-messages--${settings.bubbles}`,
    children: chat.slice(-8).map((message) =>
      el("div", {
        className: `lm-msg lm-msg--${message.role}`,
        children: [
          settings.bubbles === "plain" ? el("span", { className: "lm-msg__who", text: message.role === "user" ? "You" : "Assistant" }) : null,
          el("p", { text: message.text }),
        ],
      }),
    ),
  });
}

function quickQuestions(content, style) {
  if (style === "hidden" || style === "menu") return null;
  return el("div", {
    className: `lm-quick lm-quick--${style}`,
    children: content.quick.slice(0, 5).map((action) =>
      mock(action.label, { text: action.label, className: "lm-quick__item", title: action.prompt }),
    ),
  });
}

/**
 * The question box. With quick questions as a pop-up, a button beside it opens them; both it and
 * Ask are icons only (owner's note, third round).
 */
function composer(content, settings, hooks) {
  let menu = null;
  let toggle = null;
  if (settings.quick === "menu") {
    toggle = el("button", {
      className: "lm-btn lm-btn--icon lm-quick-toggle",
      attrs: { type: "button", "aria-label": "Quick questions", title: "Quick questions", "aria-expanded": String(hooks.quickOpen), "aria-haspopup": "menu" },
      children: [iconNode("spark", { size: 15 })],
    });
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      hooks.onToggleQuick();
    });
    if (hooks.quickOpen) {
      menu = el("div", {
        className: "lm-quick-menu",
        attrs: { role: "menu" },
        children: [
          el("p", { className: "lm-quick-menu__title", text: "Quick questions" }),
          ...content.quick.map((action) =>
            el("button", {
              className: "lm-quick-menu__item",
              attrs: { type: "button", role: "menuitem", "data-mock": action.label, title: action.prompt },
              children: [el("span", { text: action.label }), action.hint ? el("kbd", { text: action.hint }) : null],
            }),
          ),
        ],
      });
    }
  }
  return el("div", {
    className: "lm-composer",
    children: [
      menu,
      toggle,
      el("span", { className: "lm-composer__input", text: "Ask about the talk…" }),
      mock("Ask", { icon: "send", className: "lm-btn lm-btn--primary lm-btn--send" }),
    ],
  });
}

export function assistantPane(content, settings, hooks) {
  const close = settings.layout === "focus" ? mock("Close the assistant", { icon: "close", className: "lm-btn lm-btn--icon" }) : null;
  close?.removeAttribute("data-mock");
  close?.addEventListener("click", hooks.onToggleAssistant);
  return el("aside", {
    className: "lm-pane lm-assistant lm-region",
    attrs: { "aria-label": "Assistant" },
    children: [
      settings.monitor === "card" ? monitor(content) : null,
      el("div", {
        className: "lm-pane__head",
        children: [
          el("span", { className: "lm-pane__title", text: "Assistant" }),
          el("span", { className: "lm-spacer" }),
          glossaryButton(content.detail.glossary.length, { compact: true }),
          mock("Clear the conversation", { icon: "trash", className: "lm-btn lm-btn--icon" }),
          close ?? mock("Collapse", { icon: "chevron", className: "lm-btn lm-btn--icon" }),
        ],
      }),
      el("div", { className: "lm-scroll", children: [messages(content, settings)] }),
      quickQuestions(content, settings.quick),
      composer(content, settings, hooks),
    ],
  });
}
