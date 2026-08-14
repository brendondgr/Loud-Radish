/**
 * Local view preferences.
 *
 * Only what is genuinely the browser's business lives here: pane widths, collapsed panels,
 * transcript text size, follow preference. **Everything else lives in the backend** — the frontend
 * keeps no parallel notion of what the settings are (FE §8.1).
 */

const PREFIX = "seminar.";

const DEFAULTS = {
  chatCollapsed: false,
  glossaryOpen: false,
  transcriptSize: 19,
  activePane: "transcript",
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
