/**
 * The recordings lab's review (D-082) — one instance of `review.js`, under the storage key the
 * first round used, so a review already in progress carries over.
 */

import { DEFAULTS, LAYOUTS, SECTIONS } from "./options.js";
import { createReview } from "./review.js";

export const review = createReview({
  storageKey: "loud-radish.labs.sessions.v1",
  sections: SECTIONS,
  defaults: DEFAULTS,
  layouts: LAYOUTS,
  title: "Recordings lab feedback",
  page: "/labs/sessions/",
});

export const { lab, subscribe, setSetting } = review;
