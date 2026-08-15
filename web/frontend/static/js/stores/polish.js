/**
 * The polished-block store — finished minutes of transcript, rewritten for reading.
 *
 * Held separately from the segment store on purpose. A polished block is **derived** from segments
 * and does not replace them: the store keeps the raw segments it covers, and the pane hides them
 * rather than deleting them. That is what makes the whole layer safe to ignore — a page with no
 * blocks in it is the page the application shipped with, showing exactly what the speech model
 * produced.
 *
 * Blocks arrive in time order and never overlap, since the backend cuts each chunk at the end of
 * the previous one. Sorting is cheap insurance for the reconnection replay, where they may arrive
 * interleaved with live events.
 */

import { emit } from "../core/bus.js";

const CHANGED = "store.polish.changed";

export const POLISH_CHANGED = CHANGED;

class PolishStore {
  constructor() {
    /** @type {Array<object>} blocks, ordered by start time */
    this.blocks = [];
    /** Block ids already held, so a replay is idempotent. */
    this.seen = new Set();
    /** Segment ids covered by some block, so the pane can ask about one segment in constant time. */
    this.covered = new Set();
  }

  /** Add one block. Returns false for an id already held. */
  add(block) {
    if (!block || typeof block.id !== "number") return false;
    if (this.seen.has(block.id)) return false;

    this.seen.add(block.id);
    this.blocks.push(block);
    for (const id of block.source_ids ?? []) this.covered.add(id);

    if (this.blocks.length > 1 && block.start < this.blocks[this.blocks.length - 2].start) {
      this.blocks.sort((a, b) => a.start - b.start);
    }

    emit(CHANGED, { added: block, blocks: this.blocks });
    return true;
  }

  /** Add several, emitting once. Used by the page-load fetch. */
  addMany(blocks) {
    const added = (blocks ?? []).filter((block) => {
      if (!block || this.seen.has(block.id)) return false;
      this.seen.add(block.id);
      this.blocks.push(block);
      for (const id of block.source_ids ?? []) this.covered.add(id);
      return true;
    });
    if (!added.length) return [];

    this.blocks.sort((a, b) => a.start - b.start);
    emit(CHANGED, { blocks: this.blocks, batch: added });
    return added;
  }

  /** Whether a raw segment has been superseded by a polished block. */
  coversSegment(segmentId) {
    return this.covered.has(segmentId);
  }

  /** The block covering a session-absolute time, for citation and glossary navigation. */
  blockAt(seconds) {
    return this.blocks.find((block) => block.start <= seconds && block.end >= seconds) ?? null;
  }

  get count() {
    return this.blocks.length;
  }

  /** Clear everything. Called when a new session starts — never on disconnect. */
  reset() {
    this.blocks = [];
    this.seen.clear();
    this.covered.clear();
    emit(CHANGED, { blocks: this.blocks, reset: true });
  }
}

export const polish = new PolishStore();
