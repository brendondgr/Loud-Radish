/**
 * The transcript store — committed segments and the single hypothesis tail.
 *
 * The two are held **separately and deliberately**. The hypothesis is not the last element of the
 * segment list; it is its own field, replaced wholly on every update. Modelling it as a list entry
 * is what duplicates text on screen (FE §4.1), so the shape of this store is the safeguard.
 */

import { emit } from "../core/bus.js";

const CHANGED = "store.transcript.changed";
const HYPOTHESIS_CHANGED = "store.hypothesis.changed";

export const TRANSCRIPT_CHANGED = CHANGED;
export const HYPOTHESIS_CHANGED_TOPIC = HYPOTHESIS_CHANGED;

class TranscriptStore {
  constructor() {
    /** @type {Array<object>} committed segments, ordered by id */
    this.segments = [];
    /** Segment ids already held, so a replay is idempotent without deduplication logic. */
    this.seen = new Set();
    /** The tentative tail. A string, not a segment. */
    this.hypothesis = "";
    this.hypothesisStart = 0;
  }

  /**
   * Append a committed segment.
   *
   * Ordering is by id rather than arrival, and a known id is ignored — which is exactly what makes
   * the reconnection replay safe to apply blindly (FE §9.3).
   */
  commit(segment) {
    if (!segment || typeof segment.id !== "number") return false;
    if (this.seen.has(segment.id)) return false;

    this.seen.add(segment.id);
    this.segments.push(segment);

    // Almost always already in order; the sort is the cheap insurance that makes it certain.
    if (this.segments.length > 1 && segment.id < this.segments[this.segments.length - 2].id) {
      this.segments.sort((a, b) => a.id - b.id);
    }

    emit(CHANGED, { added: segment, segments: this.segments });
    return true;
  }

  /** Append several segments, emitting once. Used by the reconnection replay. */
  commitMany(segments) {
    const added = segments.filter((segment) => {
      if (!segment || this.seen.has(segment.id)) return false;
      this.seen.add(segment.id);
      this.segments.push(segment);
      return true;
    });
    if (!added.length) return [];

    this.segments.sort((a, b) => a.id - b.id);
    emit(CHANGED, { added: added[added.length - 1], segments: this.segments, batch: added });
    return added;
  }

  /**
   * Replace the tentative tail wholly.
   *
   * An empty string is meaningful, not a no-op: it means the tail has been committed or discarded
   * and the display should clear.
   */
  setHypothesis(text, start = 0) {
    const next = text ?? "";
    if (next === this.hypothesis) return false;
    this.hypothesis = next;
    this.hypothesisStart = start;
    emit(HYPOTHESIS_CHANGED, { text: next, start });
    return true;
  }

  /** The highest committed segment id, or null when the transcript is empty. */
  get lastId() {
    return this.segments.length ? this.segments[this.segments.length - 1].id : null;
  }

  get count() {
    return this.segments.length;
  }

  /** Total committed duration, for the status display. */
  get durationSeconds() {
    return this.segments.length ? this.segments[this.segments.length - 1].end : 0;
  }

  /** Find the segment covering a session-absolute time — used by citation timestamps. */
  segmentAt(seconds) {
    const exact = this.segments.find(
      (segment) => segment.start <= seconds && segment.end >= seconds
    );
    if (exact) return exact;

    // A citation rarely lands inside a segment. The model rounds `[00:03]` to the second, the gap
    // between segments is silence nobody spoke in, and a cited time before the first segment is
    // routine — `[00:00]` for a talk whose first word lands at 0.8 s. Returning nothing for any of
    // these makes a citation that looks clickable do nothing, which is worse than approximate.
    return (
      [...this.segments].reverse().find((segment) => segment.start <= seconds) ??
      this.segments[0]
    );
  }

  /** Clear everything. Called when a new session starts — never on disconnect. */
  reset() {
    this.segments = [];
    this.seen.clear();
    this.hypothesis = "";
    this.hypothesisStart = 0;
    emit(CHANGED, { segments: this.segments, reset: true });
    emit(HYPOTHESIS_CHANGED, { text: "", start: 0 });
  }
}

export const transcript = new TranscriptStore();
