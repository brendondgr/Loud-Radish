/**
 * The main-screen lab's options (D-080), as data — read by the shared customizer, the page and
 * the feedback export, exactly as `options.js` is for the recordings lab.
 *
 * The look section repeats the recordings lab's so the two pages are judged in one style; its
 * defaults are the owner's first-round choice there.
 */

export const LAYOUTS = [
  {
    id: "classic",
    label: "Classic, restyled",
    hint: "Today's arrangement — header, transcript, assistant, status bar — in the new style.",
  },
  {
    id: "panels",
    label: "Floating panels",
    hint: "Each region is a raised card with space around it, like the recording cards.",
  },
  {
    id: "focus",
    label: "Focus",
    hint: "The transcript alone at reading width; the assistant slides in when asked for.",
  },
  {
    id: "dock",
    label: "Bottom dock",
    hint: "No header. Recording controls and health sit in a floating dock at the bottom.",
  },
];

const choice = (id, label, choices, extra = {}) => ({ id, kind: "choice", label, choices, ...extra });
const c = (id, label, hint) => (hint ? { id, label, hint } : { id, label });

export const SECTIONS = [
  {
    id: "layout",
    label: "Layout",
    options: [
      choice("layout", "Arrangement", LAYOUTS),
      choice("assistWidth", "Assistant width", [c("narrow", "Narrow"), c("medium", "Medium"), c("wide", "Wide")]),
      choice("density", "Spacing", [c("tight", "Tight"), c("cozy", "Cozy"), c("airy", "Airy")]),
    ],
  },
  {
    id: "header",
    label: "Header & status",
    options: [
      choice("record", "Record button", [
        c("pill", "Pill with words"),
        c("round", "Round red button"),
        c("icon", "Icon only"),
      ]),
      choice("health", "Microphone level, speed, lag", [
        c("bar", "A status bar at the bottom"),
        c("header", "Chips in the header"),
        c("column", "In the details column"),
      ], { hint: "In the dock arrangement they are always in the dock; without a details column, in the header." }),
    ],
  },
  {
    id: "transcript",
    label: "Transcript",
    options: [
      choice("font", "Typeface", [c("serif", "Serif (today)"), c("sans", "Sans-serif")]),
      choice("textSize", "Text size", [c("s", "Smaller"), c("m", "Today's"), c("l", "Larger")]),
      choice("stamps", "Timestamps", [
        c("above", "Above each paragraph (today)"),
        c("margin", "In the left margin"),
        c("hidden", "Hidden"),
      ]),
      choice("toolbar", "Toolbar", [c("full", "Words and controls"), c("compact", "Icons only")]),
    ],
  },
  {
    id: "assistant",
    label: "Assistant",
    options: [
      choice("bubbles", "Messages", [
        c("bubbles", "Your questions in bubbles"),
        c("plain", "Plain, like a document"),
      ]),
      choice("quick", "Quick questions", [
        c("menu", "A pop-up from a button by Ask"),
        c("chips", "Chips"),
        c("list", "A list"),
        c("hidden", "Hidden"),
      ]),
    ],
  },
  {
    id: "details",
    label: "Video & details",
    options: [
      choice("monitor", "Video, recording details, capture settings, glossary", [
        c("left", "Their own column, left of the transcript"),
        c("middle", "Their own column, between transcript and assistant"),
        c("card", "Just the video, as a card above the assistant"),
        c("pip", "Just the video, picture-in-picture over the transcript"),
        c("hidden", "Hidden"),
      ]),
    ],
  },
  {
    id: "banner",
    label: "Viewing a past recording",
    options: [
      choice("banner", "How it says so", [
        c("strip", "A strip under the header"),
        c("card", "A card at the top of the transcript"),
        c("chip", "A chip in the header"),
      ]),
    ],
  },
  {
    id: "look",
    label: "Look",
    options: [
      choice("accent", "Accent", [c("teal", "Teal (today)"), c("radish", "Radish red"), c("amber", "Amber"), c("violet", "Violet")]),
      choice("surface", "Panel surface", [c("outline", "Outlined"), c("raised", "Raised"), c("flat", "Flat")]),
      choice("radius", "Corners", [c("sharp", "Sharp"), c("soft", "Soft"), c("round", "Round")]),
    ],
  },
];

/**
 * The owner's second-round choice ("PReferences", 2026-11-07), plus what their notes asked for: the
 * video and details in a column of their own, and the quick questions behind a button.
 */
export const DEFAULTS = {
  layout: "classic",
  assistWidth: "medium",
  density: "airy",
  record: "pill",
  health: "header",
  font: "serif",
  textSize: "s",
  stamps: "margin",
  toolbar: "compact",
  bubbles: "bubbles",
  quick: "menu",
  monitor: "left",
  banner: "card",
  accent: "radish",
  surface: "raised",
  radius: "round",
};
