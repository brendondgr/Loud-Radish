/**
 * Auto-follow, disengage, and scroll-position preservation.
 *
 * This is the most-used interaction in the whole application and the easiest to get wrong
 * (FE §4.3). The rules, in the order they matter:
 *
 * 1. **Auto-follow by default.** Pinned to the bottom; new content scrolls into view.
 * 2. **Scrolling up disengages immediately.** No delay, no threshold games — the user scrolled up
 *    because they want to read something.
 * 3. **While disengaged, position is preserved exactly.** The text being read must not move when
 *    new content arrives below it.
 * 4. **Returning to the bottom re-engages.**
 *
 * The distinction that makes this work is between scrolls the *user* caused and scrolls *we*
 * caused. A programmatic scroll-to-bottom fires the same `scroll` event a wheel gesture does, and
 * treating it as intent would disengage follow on every new segment. So user intent is recorded by
 * the input events that precede the scroll, and only an intentful scroll can disengage.
 */

import { emit } from "../core/bus.js";

export const FOLLOW_CHANGED = "scroll.follow.changed";
export const UNREAD_CHANGED = "scroll.unread.changed";

/** How close to the bottom still counts as "at the bottom", in pixels. */
const BOTTOM_THRESHOLD = 24;

/** How long after an input event a scroll is still attributed to the user. */
const INTENT_WINDOW_MS = 600;

export class ScrollController {
  constructor(scroller) {
    this.scroller = scroller;
    this.following = true;
    this.unread = 0;
    this._intentUntil = 0;
    this._pendingFrame = null;

    // Anything that expresses an intention to move: a wheel, a drag, a key, a scrollbar press.
    const markIntent = () => this._markIntent();
    scroller.addEventListener("wheel", markIntent, { passive: true });
    scroller.addEventListener("touchmove", markIntent, { passive: true });
    scroller.addEventListener("pointerdown", markIntent, { passive: true });
    scroller.addEventListener("keydown", markIntent);
    scroller.addEventListener("scroll", () => this._onScroll(), { passive: true });

    window.addEventListener("keydown", (event) => {
      if (NAVIGATION_KEYS.has(event.key) && scroller.contains(document.activeElement)) {
        markIntent();
      }
    });
  }

  /**
   * Call immediately *before* inserting content, then call the returned function after.
   *
   * While following, this scrolls to the new bottom. While disengaged, it holds `scrollTop`
   * exactly where it was.
   *
   * Holding the offset from the *top* is what preserves the view here, because segments are only
   * ever appended **below** the reader. Measuring from the bottom instead — the reflex, and what
   * this did first — moves the text by exactly the height of whatever arrived, which is precisely
   * the failure this method exists to prevent. If content ever starts being inserted above the
   * viewport, this is the line that has to change.
   */
  beginUpdate() {
    if (this.following) {
      return () => this.scrollToBottom();
    }

    const held = this.scroller.scrollTop;
    return () => {
      this.scroller.scrollTop = held;
    };
  }

  /** Record that content arrived, so the jump-to-live count is accurate. */
  noteArrival(count = 1) {
    if (this.following) return;
    this.unread += count;
    emit(UNREAD_CHANGED, { unread: this.unread });
  }

  /** Re-engage follow and scroll to the newest content. */
  jumpToLive() {
    this._setFollowing(true);
    this._clearUnread();
    this.scrollToBottom();
  }

  /**
   * Scroll to the bottom without marking it as user intent.
   *
   * Applied immediately, then corrected on the next frame once layout has settled. The immediate
   * assignment is not an optimisation: `requestAnimationFrame` does not fire while a tab is
   * backgrounded or otherwise not compositing, and relying on it alone leaves the transcript
   * stuck wherever it was when the user switched away.
   */
  scrollToBottom() {
    this.scroller.scrollTop = this.scroller.scrollHeight;

    cancelAnimationFrame(this._pendingFrame);
    this._pendingFrame = requestAnimationFrame(() => {
      this._pendingFrame = null;
      // Yield to the user. Between scheduling this frame and it running, they may have started
      // scrolling up — re-asserting the bottom here would drag them back mid-gesture, which is
      // the single most irritating thing this component could do.
      if (!this.following || performance.now() < this._intentUntil) return;
      this.scroller.scrollTop = this.scroller.scrollHeight;
    });
  }

  /** Scroll a specific element into view, disengaging follow — the user asked to go there. */
  scrollTo(element) {
    if (!element) return;
    this._setFollowing(false);
    element.scrollIntoView({ block: "center", behavior: "auto" });
  }

  get isAtBottom() {
    const { scrollTop, scrollHeight, clientHeight } = this.scroller;
    return scrollHeight - scrollTop - clientHeight <= BOTTOM_THRESHOLD;
  }

  // -- internals -------------------------------------------------------------------

  _markIntent() {
    this._intentUntil = performance.now() + INTENT_WINDOW_MS;
  }

  _onScroll() {
    const atBottom = this.isAtBottom;

    if (atBottom && !this.following) {
      // Returning to the bottom re-engages, whether the user scrolled there or we did.
      this._setFollowing(true);
      this._clearUnread();
      return;
    }

    if (!atBottom && this.following && performance.now() < this._intentUntil) {
      // Away from the bottom, and the user put us here. Disengage at once.
      this._setFollowing(false);
    }
  }

  _setFollowing(following) {
    if (this.following === following) return;
    this.following = following;
    emit(FOLLOW_CHANGED, { following });
  }

  _clearUnread() {
    if (this.unread === 0) return;
    this.unread = 0;
    emit(UNREAD_CHANGED, { unread: 0 });
  }
}

const NAVIGATION_KEYS = new Set([
  "PageUp",
  "PageDown",
  "ArrowUp",
  "ArrowDown",
  "Home",
  "End",
]);
