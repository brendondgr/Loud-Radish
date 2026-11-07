/**
 * Every knob the recordings lab offers (D-080), as data.
 *
 * The customizer draws its controls from this list, the page reads the chosen values from it, and
 * the feedback export names each choice by the same labels the user saw — so a line pasted back
 * into a conversation says "Titles: Keywords from the transcript", not `title=keywords`.
 *
 * `kind`: "choice" is one of several, "multi" is any of several, "toggle" is on or off.
 */

export const LAYOUTS = [
  {
    id: "gallery",
    label: "Gallery",
    hint: "Picture on top, title and actions underneath. The default most apps use.",
  },
  {
    id: "tiles",
    label: "Tiles",
    hint: "The picture is the card; the title sits on it. Most recordings per screen.",
  },
  {
    id: "wide",
    label: "Wide cards",
    hint: "Picture on the left, what was said on the right. Reads more, shows fewer.",
  },
  {
    id: "timeline",
    label: "Timeline",
    hint: "Each day opens with a date tile, then that day's cards, in one unbroken grid.",
  },
  {
    id: "split",
    label: "Split view",
    hint: "Cards on the left, the chosen recording always open on the right. No pop-up.",
  },
  {
    id: "list",
    label: "Slim list",
    hint: "Today's rows done properly: one line each, thumbnail and icons kept. For comparison.",
  },
];

export const SECTIONS = [
  {
    id: "layout",
    label: "Layout",
    options: [
      { id: "layout", kind: "choice", label: "Arrangement", choices: LAYOUTS },
      {
        id: "size",
        kind: "choice",
        label: "Card size",
        choices: [
          { id: "s", label: "Small" },
          { id: "m", label: "Medium" },
          { id: "l", label: "Large" },
        ],
      },
      {
        id: "density",
        kind: "choice",
        label: "Spacing",
        choices: [
          { id: "tight", label: "Tight" },
          { id: "cozy", label: "Cozy" },
          { id: "airy", label: "Airy" },
        ],
      },
      {
        id: "group",
        kind: "choice",
        label: "Group by",
        hint: "Timeline always groups by day.",
        choices: [
          { id: "none", label: "Nothing" },
          { id: "day", label: "Day" },
          { id: "week", label: "Week" },
          { id: "month", label: "Month" },
        ],
      },
    ],
  },
  {
    id: "header",
    label: "Header",
    options: [
      {
        id: "header",
        kind: "choice",
        label: "Header style",
        choices: [
          { id: "slim", label: "One slim bar", hint: "Title, search, filters and view in 48px." },
          { id: "stacked", label: "Two thin rows", hint: "Title row, then a filter row." },
          { id: "floating", label: "Floating dock", hint: "No header; a toolbar floats at the bottom." },
        ],
      },
      { id: "stickyHeader", kind: "toggle", label: "Header stays put while scrolling" },
    ],
  },
  {
    id: "titles",
    label: "Titles & dates",
    options: [
      {
        id: "title",
        kind: "choice",
        label: "Card title",
        hint:
          "A preview of auto-titling. The real version asks the model for a 4–6 word title when " +
          "transcription finishes; until then these are built from the transcript without it.",
        choices: [
          { id: "keywords", label: "Keywords from the transcript" },
          { id: "summary", label: "First summary sentence" },
          { id: "opening", label: "Opening words" },
          { id: "stored", label: "Stored title (today's)" },
          { id: "date", label: "The date, in words" },
        ],
      },
      {
        id: "date",
        kind: "choice",
        label: "Date",
        hint: "All in your own time zone. Stored titles were written in UTC, which is why they look off.",
        choices: [
          { id: "smart", label: "Smart", hint: "Today · 12:11 PM, Mon · 3:04 PM, Sep 18 · 9:30 AM" },
          { id: "relative", label: "Relative", hint: "3 hours ago, 2 weeks ago" },
          { id: "absolute", label: "Full", hint: "Sat 7 Nov 2026, 12:11 PM" },
          { id: "daypart", label: "Part of day", hint: "Saturday afternoon · 7 Nov" },
        ],
      },
      {
        id: "datePlace",
        kind: "choice",
        label: "Date sits",
        choices: [
          { id: "under", label: "Under the title" },
          { id: "above", label: "Above the title" },
          { id: "image", label: "On the picture" },
        ],
      },
      { id: "snippet", kind: "toggle", label: "Show what was said (opening words or summary)" },
    ],
  },
  {
    id: "pictures",
    label: "Pictures",
    options: [
      {
        id: "thumb",
        kind: "choice",
        label: "Video picture",
        choices: [
          { id: "preview", label: "Saved preview" },
          { id: "middle", label: "Frame from the middle" },
          { id: "strip", label: "Filmstrip of 3 frames" },
        ],
      },
      { id: "scrub", kind: "toggle", label: "Move the pointer across a picture to skim the video" },
      {
        id: "noVideo",
        kind: "choice",
        label: "No video",
        hint: "What a card shows when there is nothing to take a picture of.",
        choices: [
          { id: "words", label: "The transcript's words" },
          { id: "wave", label: "A waveform pattern", hint: "Decorative — not the real audio." },
          { id: "icon", label: "A large icon" },
          { id: "tint", label: "A colour for the time of day" },
        ],
      },
      {
        id: "ratio",
        kind: "choice",
        label: "Picture shape",
        choices: [
          { id: "wide", label: "16:9" },
          { id: "photo", label: "3:2" },
          { id: "square", label: "Square" },
        ],
      },
    ],
  },
  {
    id: "media",
    label: "Video, audio, transcript",
    options: [
      {
        id: "mediaStyle",
        kind: "choice",
        label: "Show them as",
        choices: [
          { id: "overlay", label: "Icons on the picture" },
          { id: "footer", label: "Icons beside the date" },
          { id: "labelled", label: "Icons with words" },
          { id: "off", label: "Hidden" },
        ],
      },
      { id: "showMissing", kind: "toggle", label: "Draw missing ones faded (otherwise leave them out)" },
      {
        id: "stats",
        kind: "multi",
        label: "Numbers",
        choices: [
          { id: "duration", label: "Length" },
          { id: "words", label: "Words" },
          { id: "size", label: "Size on disk" },
          { id: "summaries", label: "Summaries" },
          { id: "chat", label: "Questions asked" },
        ],
      },
    ],
  },
  {
    id: "actions",
    label: "Buttons",
    options: [
      {
        id: "actionStyle",
        kind: "choice",
        label: "Button style",
        choices: [
          { id: "bar", label: "Main button + icons" },
          { id: "labels", label: "Every button labelled" },
          { id: "menu", label: "Main button + ⋯ menu" },
          { id: "hover", label: "Icons appear on hover" },
        ],
      },
      {
        id: "primaryLabel",
        kind: "choice",
        label: "Main button says",
        choices: [
          { id: "main", label: "Bring to main screen" },
          { id: "open", label: "Open in app" },
          { id: "import", label: "Import" },
          { id: "ask", label: "Ask about it" },
        ],
      },
      {
        id: "quick",
        kind: "multi",
        label: "Quick buttons on each card",
        choices: [
          { id: "transcript", label: "Download transcript" },
          { id: "video", label: "Download video" },
          { id: "audio", label: "Download audio" },
          { id: "webapp", label: "Export web app" },
          { id: "folder", label: "Open folder" },
        ],
      },
      {
        id: "detail",
        kind: "choice",
        label: "Clicking a card opens",
        hint: "Split view always uses the side panel.",
        choices: [
          { id: "drawer", label: "A side panel" },
          { id: "sheet", label: "A compact window" },
          { id: "inline", label: "The card, expanded in place" },
        ],
      },
    ],
  },
  {
    id: "look",
    label: "Look",
    options: [
      {
        id: "accent",
        kind: "choice",
        label: "Accent",
        choices: [
          { id: "teal", label: "Teal (today)" },
          { id: "radish", label: "Radish red" },
          { id: "amber", label: "Amber" },
          { id: "violet", label: "Violet" },
        ],
      },
      {
        id: "surface",
        kind: "choice",
        label: "Card surface",
        choices: [
          { id: "outline", label: "Outlined" },
          { id: "raised", label: "Raised" },
          { id: "flat", label: "Flat" },
        ],
      },
      {
        id: "radius",
        kind: "choice",
        label: "Corners",
        choices: [
          { id: "sharp", label: "Sharp" },
          { id: "soft", label: "Soft" },
          { id: "round", label: "Round" },
        ],
      },
    ],
  },
];

/**
 * The owner's first-round choice ("My Actual Preference", 2026-11-07) — the starting point for
 * the second round. Radish is the accent only; surfaces stay graphite.
 */
export const DEFAULTS = {
  layout: "wide",
  size: "s",
  density: "airy",
  group: "day",
  header: "slim",
  stickyHeader: true,
  title: "keywords",
  date: "smart",
  datePlace: "image",
  snippet: false,
  thumb: "strip",
  scrub: true,
  noVideo: "tint",
  ratio: "photo",
  mediaStyle: "overlay",
  showMissing: true,
  stats: ["duration", "words", "size"],
  actionStyle: "hover",
  primaryLabel: "main",
  quick: ["transcript", "folder"],
  detail: "sheet",
  accent: "radish",
  surface: "raised",
  radius: "round",
};
