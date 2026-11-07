/**
 * The lab's memory: the chosen settings, a verdict and a note for each layout, a note for each
 * section of options, saved combinations, and a general note (D-080).
 *
 * Kept in this browser's local storage, because it is one person's review in progress and nothing
 * else needs it. Any of it can come back empty — a private window, cleared site data — so every
 * read falls back to a fresh review rather than failing.
 */

import { sanitize } from "./options.js";

const STORAGE_KEY = "loud-radish.labs.sessions.v1";

function fresh() {
  return { settings: sanitize(), verdicts: {}, notes: {}, combos: [], general: "" };
}

function read() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return fresh();
    const stored = JSON.parse(raw);
    return {
      ...fresh(),
      ...stored,
      settings: sanitize(stored.settings),
      combos: Array.isArray(stored.combos) ? stored.combos : [],
    };
  } catch {
    return fresh();
  }
}

export const lab = read();

const listeners = new Set();

/** Call `listener(changedKeys)` after every change. */
export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function save(changed = []) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(lab));
  } catch {
    // Storage is a convenience here; the page works without it.
  }
  for (const listener of listeners) listener(changed);
}

export function setSetting(id, value) {
  lab.settings[id] = value;
  save([id]);
}

export function applySettings(settings) {
  lab.settings = sanitize(settings);
  save(Object.keys(lab.settings));
}

/** `score`: "love", "ok" or "no"; the same score again clears it. */
export function setVerdict(layoutId, score) {
  const current = lab.verdicts[layoutId] ?? {};
  lab.verdicts[layoutId] = { ...current, score: current.score === score ? "" : score };
  save(["verdicts"]);
}

export function setVerdictNote(layoutId, note) {
  lab.verdicts[layoutId] = { ...(lab.verdicts[layoutId] ?? {}), note };
  save(["verdictNote"]);
}

export function setNote(sectionId, note) {
  lab.notes[sectionId] = note;
  save(["notes"]);
}

export function setGeneral(note) {
  lab.general = note;
  save(["general"]);
}

export function saveCombo(name, note = "") {
  lab.combos.push({
    name: name || `Combination ${lab.combos.length + 1}`,
    note,
    settings: { ...lab.settings },
    savedAt: new Date().toISOString(),
  });
  save(["combos"]);
}

export function removeCombo(index) {
  lab.combos.splice(index, 1);
  save(["combos"]);
}

export function resetReview() {
  Object.assign(lab, fresh());
  save(Object.keys(lab.settings).concat(["verdicts", "notes", "combos", "general"]));
}
