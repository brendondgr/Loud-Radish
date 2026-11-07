/**
 * What each recording is about — opening words, first summary, keywords — fetched in batches as
 * cards come into view (D-082, D-085). A card shows its model-written title when it has one; the
 * glance is its fallback and the line under it.
 */

import { api } from "../transport/api.js";

const BATCH = 40;

export class Glances {
  /**
   * @param {HTMLElement} scroller - the element the list scrolls in
   * @param {(keys: string[]) => void} onArrive - called with the keys whose glances arrived
   */
  constructor(scroller, onArrive) {
    this.found = new Map();
    this.wanted = new Set();
    this.onArrive = onArrive;
    this.timer = 0;
    this.fetching = false;
    this.observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const key = entry.target.dataset.key;
          if (entry.isIntersecting && key && !this.found.has(key)) this.wanted.add(key);
        }
        this.soon();
      },
      { root: scroller, rootMargin: "600px 0px" },
    );
  }

  get(key) {
    return this.found.get(key);
  }

  /** Whether a recording has anything a glance could say. */
  static worth(session) {
    return !session.orphan && session.segments > 0;
  }

  /** Watch these cards; fetch for any that come near the screen. */
  observe(cards, sessionsByKey) {
    this.observer.disconnect();
    for (const card of cards) {
      const session = sessionsByKey.get(card.dataset.key);
      if (session && Glances.worth(session) && !this.found.has(session.key)) this.observer.observe(card);
    }
  }

  /**
   * Ask for these straight away — the first screenful, rather than after the observer's first
   * pass, which a browser may hold back while the window is in the background.
   */
  want(sessions) {
    for (const session of sessions) {
      if (Glances.worth(session) && !this.found.has(session.key)) this.wanted.add(session.key);
    }
    this.soon(0);
  }

  soon(delay = 80) {
    if (!this.wanted.size) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.fetch(), delay);
  }

  async fetch() {
    if (this.fetching) return;
    this.fetching = true;
    try {
      while (this.wanted.size) {
        const keys = [...this.wanted].slice(0, BATCH);
        keys.forEach((key) => this.wanted.delete(key));
        try {
          const body = await api.glances(keys);
          for (const key of keys) this.found.set(key, body.glances?.[key] ?? {});
        } catch {
          for (const key of keys) this.found.set(key, {});
        }
        this.onArrive(keys);
      }
    } finally {
      this.fetching = false;
    }
  }
}
