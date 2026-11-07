/**
 * Local view preferences.
 *
 * Only what is genuinely the browser's business lives here: pane widths, collapsed panels,
 * transcript text size, follow preference. **Everything else lives in the backend** — the frontend
 * keeps no parallel notion of what the settings are (FE §8.1).
 */

const PREFIX = "seminar.";

export const DEFAULTS = {
  chatCollapsed: false,
  glossaryOpen: false,
  // The owner's "smaller" from the main-screen lab (redesign Step 6); it was 19.
  transcriptSize: 16.5,
  activePane: "transcript",
  // The record sheet's last answers: source, timing, a window's audio, video (D-075). A
  // *preference*, not configuration: it pre-fills a sheet that is always shown, so it decides
  // nothing on its own and does not belong in /api/config. Null until the first recording.
  lastRecordOptions: null,
  // The volume each sound was last left at in the sound panel, by input name (D-081). The next
  // recording starts each one there. A preference for the same reason as the sheet's answers.
  lastMixLevels: {},
};

/** Read one preference, falling back to its default. */
export function get(key) {
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    return raw === null ? DEFAULTS[key] : JSON.parse(raw);
  } catch {
    // Private browsing, a disabled store, or corrupt JSON. A missing preference is not worth
    // breaking the application over.
    return DEFAULTS[key];
  }
}

/** Write one preference. Failure is silent and harmless. */
export function set(key, value) {
  try {
    window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

/** Every preference, merged over the defaults. */
export function all() {
  return Object.fromEntries(Object.keys(DEFAULTS).map((key) => [key, get(key)]));
}
