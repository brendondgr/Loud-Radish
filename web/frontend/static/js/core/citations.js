/**
 * Turning `[MM:SS]` in an answer into something clickable.
 *
 * A citation the reader cannot follow is decoration. The point of asking the model to cite
 * timestamps is that the claim can be checked against what the speaker actually said, and that
 * requires one click rather than a manual scroll through two hours of transcript.
 *
 * **Built with DOM nodes, never a string of HTML.** The text being scanned is model output. It is
 * inserted with `textContent` everywhere else in this application for exactly that reason, and
 * assembling markup here would reintroduce the injection this convention exists to prevent.
 */

import { el } from "./dom.js";

/**
 * `[12:34]`, `[1:02:03]`, the ranges models write unprompted (`[00:03-00:15]`), and the lists they
 * write when one claim spans two moments: `[00:02, 00:52]`.
 *
 * Neither the range nor the list form is in the prompt, but both arrive anyway. Matching only the
 * exact form asked for leaves the commonest citations in a live answer as unclickable text.
 */
const TIME = String.raw`\d{1,2}:\d{2}(?::\d{2})?`;
/** One citation entry: a single moment, or a range whose start is what we seek to. */
const ENTRY = String.raw`${TIME}(?:\s*[–—-]\s*${TIME})?`;
const CITATION = new RegExp(String.raw`\[(${ENTRY}(?:\s*[,;]\s*${ENTRY})*)\]`, "g");
const SEPARATOR = /\s*[,;]\s*/;
const LEADING_TIME = new RegExp(String.raw`^${TIME}`);

/** Parse `MM:SS` or `HH:MM:SS` into seconds. Returns null if it is neither. */
export function parseTimestamp(label) {
  const parts = label.split(":").map(Number);
  if (parts.some((part) => !Number.isFinite(part))) return null;
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return null;
}

/**
 * Render `text` into `target`, replacing citations with buttons.
 *
 * @param {Element} target the element to fill
 * @param {string} text the model's answer
 * @param {(seconds: number) => void} onSeek called with the cited time
 */
export function renderWithCitations(target, text, onSeek) {
  target.replaceChildren();

  let cursor = 0;
  for (const match of text.matchAll(CITATION)) {
    if (match.index > cursor) {
      target.append(document.createTextNode(text.slice(cursor, match.index)));
    }

    target.append(...citationNodes(match[0], match[1], onSeek));
    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) {
    target.append(document.createTextNode(text.slice(cursor)));
  }
}

/**
 * The nodes one bracket becomes.
 *
 * A single entry keeps its text verbatim, brackets and all. A list becomes one button per moment —
 * `[00:02, 00:52]` renders as `[00:02] [00:52]` — because a single button covering both can only
 * seek to one of them, and the second moment is the one the reader cannot otherwise reach.
 */
function citationNodes(source, inner, onSeek) {
  const entries = inner.split(SEPARATOR);
  const seek = entries.map((entry) => parseTimestamp(entry.match(LEADING_TIME)?.[0] ?? ""));
  if (seek.some((seconds) => seconds === null)) return [document.createTextNode(source)];

  if (entries.length === 1) return [citationButton(source, seek[0], onSeek)];

  const nodes = [];
  entries.forEach((entry, index) => {
    if (index > 0) nodes.push(document.createTextNode(" "));
    nodes.push(citationButton(`[${entry}]`, seek[index], onSeek));
  });
  return nodes;
}

function citationButton(label, seconds, onSeek) {
  const button = el("button", {
    className: "citation",
    text: label,
    attrs: {
      type: "button",
      "data-seconds": seconds,
      // Named for what it does rather than what it says, since the visible text is a bare number.
      "aria-label": `Go to ${label.replace(/[[\]]/g, "")} in the transcript`,
    },
  });
  button.addEventListener("click", () => onSeek?.(seconds));
  return button;
}
