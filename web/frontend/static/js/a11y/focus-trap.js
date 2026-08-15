/**
 * A focus trap for modal dialogs.
 *
 * Three things a dialog must do, all of which are invisible when they work and obvious when they
 * do not: move focus in, keep Tab inside, and put focus back where it came from on close. Skipping
 * the last one strands a keyboard user at the top of the document with no idea where they are.
 *
 * The candidate list is recomputed on every Tab rather than cached. The settings dialog changes
 * shape constantly — tabs swap panels, a device list arrives from the network, a field appears when
 * a mode changes — and a cached list would send focus to an element no longer on screen.
 */

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** Visible, focusable descendants of `root`, in document order. */
function focusable(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter((node) => {
    if (node.hasAttribute("hidden") || node.getAttribute("aria-hidden") === "true") return false;
    // `offsetParent` is null for anything display:none, including everything inside a hidden
    // panel — which is exactly what the inactive settings tabs are.
    return node.offsetParent !== null || node === document.activeElement;
  });
}

export class FocusTrap {
  constructor(root) {
    this.root = root;
    this.previous = null;
    this._onKeydown = this._onKeydown.bind(this);
    this.active = false;
  }

  /** Remember what had focus, move it inside, and start intercepting Tab. */
  activate(initial = null) {
    if (this.active) return;
    this.active = true;
    this.previous = document.activeElement;
    document.addEventListener("keydown", this._onKeydown, true);

    // Focused synchronously. The caller reveals the dialog before activating the trap, so the
    // element is already laid out — and deferring to `requestAnimationFrame` would tie the trap to
    // a frame that a backgrounded tab never produces, leaving focus on `<body>`.
    const target = initial ?? focusable(this.root)[0] ?? this.root;
    target?.focus?.();
  }

  /** Stop intercepting and return focus to wherever it was. */
  release() {
    if (!this.active) return;
    this.active = false;
    document.removeEventListener("keydown", this._onKeydown, true);

    // Only if it is still in the document — the element may have been replaced while the dialog
    // was open, and focusing a detached node silently focuses <body> instead.
    if (this.previous?.isConnected) this.previous.focus();
    this.previous = null;
  }

  _onKeydown(event) {
    if (event.key !== "Tab") return;

    const candidates = focusable(this.root);
    if (candidates.length === 0) {
      event.preventDefault();
      return;
    }

    const first = candidates[0];
    const last = candidates[candidates.length - 1];
    const active = document.activeElement;

    // Focus outside the dialog entirely — a click on the page behind it, or a browser control —
    // is pulled back in rather than left to wander.
    if (!this.root.contains(active)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
      return;
    }

    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }
}
