/**
 * Formatting for everything the user reads as a number.
 *
 * Times are session-relative here, matching the transcript. Wall-clock is available on hover, so
 * the user can correlate the transcript with their own notes (FE §4.2).
 */

/** Session-relative seconds as `HH:MM:SS`. */
export function timestamp(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return [hours, minutes, secs].map((n) => String(n).padStart(2, "0")).join(":");
}

/** A duration in words, for counts and gaps: "2 min", "45 s". */
export function duration(seconds) {
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

/** An ISO timestamp as local wall-clock time. */
export function wallClock(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Real-time factor, e.g. `1.8×`. */
export function realtimeFactor(value) {
  return `${(value ?? 0).toFixed(1)}×`;
}

/** A latency in seconds, e.g. `2.3 s`. */
export function latency(seconds) {
  return `${(seconds ?? 0).toFixed(1)} s`;
}

/** Pluralise a count: `pluralise(1, "segment")` → "1 segment". */
export function pluralise(count, noun, plural = `${noun}s`) {
  return `${count} ${count === 1 ? noun : plural}`;
}
