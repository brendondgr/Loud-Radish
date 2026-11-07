/**
 * The transcript's text size (FE §13): the one size a reader adjusts, independently of the rest
 * of the interface, because it is the text read for two hours.
 *
 * A browser preference, not configuration — it is about this screen and these eyes. Set in
 * Settings → Context (redesign Step 6), where it moved from two buttons in the transcript's
 * toolbar; the default is the owner's "smaller" choice from the main-screen lab.
 */

import * as prefs from "./storage.js";

export const MIN_SIZE = 14;
export const MAX_SIZE = 28;

export function textSize() {
  const stored = Number(prefs.get("transcriptSize"));
  return Number.isFinite(stored) ? clamp(stored) : clamp(Number(prefs.DEFAULTS.transcriptSize));
}

/** Apply a size to the page and remember it. Returns the size applied. */
export function setTextSize(px) {
  const size = clamp(px);
  applyTextSize(size);
  prefs.set("transcriptSize", size);
  return size;
}

export function applyTextSize(px) {
  document.documentElement.style.setProperty("--transcript-size", `${clamp(px)}px`);
}

function clamp(px) {
  return Math.max(MIN_SIZE, Math.min(MAX_SIZE, Number(px) || MIN_SIZE));
}
