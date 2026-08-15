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
 * `[12:34]`, `[1:02:03]`, and the ranges models write unprompted: `[00:03-00:15]`.
 *
 * The range form is not in the prompt but arrives anyway. Matching only the exact form asked for
 * leaves the commonest citation in a live answer as unclickable text.
 */
const CITATION = /\[(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*[–—-]\s*(\d{1,2}:\d{2}(?::\d{2})?))?\]/g;

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

    const seconds = parseTimestamp(match[1]);
    if (seconds === null) {
      target.append(document.createTextNode(match[0]));
    } else {
      target.append(citationButton(match[0], seconds, onSeek));
    }
    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) {
    target.append(document.createTextNode(text.slice(cursor)));
  }
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
