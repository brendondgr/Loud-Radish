/* Small shared helpers for the exported application.
 *
 * Loaded as a **classic script**, not a module, and so is everything beside it. That is not an
 * oversight: an exported recording has to open by double-clicking `index.html` in a file manager,
 * and every browser refuses `import` across `file://` URLs. Classic scripts share one global scope,
 * so the classes below simply see each other in load order.
 */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/** Local storage, scoped per recording so two exports open in one browser do not collide. */
function scopedStore(key) {
  const name = `transcriber-export:${window.EXPORT_KEY || "session"}:${key}`;
  return {
    read(fallback) {
      try {
        const raw = localStorage.getItem(name);
        return raw === null ? fallback : JSON.parse(raw);
      } catch {
        return fallback;
      }
    },
    write(value) {
      try {
        localStorage.setItem(name, JSON.stringify(value));
      } catch {
        /* Storage disabled costs the arrangement between visits and nothing else. */
      }
    },
  };
}

/** Seconds as `M:SS`, or `H:MM:SS` past the hour. The format every citation is written in. */
function clock(seconds) {
  const value = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const total = Math.floor(value);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const mm = hours ? String(minutes).padStart(2, "0") : String(minutes);
  return `${hours ? `${hours}:` : ""}${mm}:${String(rest).padStart(2, "0")}`;
}

/** `12:34` or `1:02:03` back to seconds, for a timestamp the model wrote. Null if it is neither. */
function parseClock(text) {
  const parts = String(text).split(":").map(Number);
  if (parts.some((part) => !Number.isFinite(part))) return null;
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return null;
}
