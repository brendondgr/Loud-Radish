/**
 * "Open a recording…" on the main screen (D-076).
 *
 * Lists the recordings on disk with the one action each can take — bring it here, transcribe it
 * first, or the reason it can do neither. The answer is the server's (`media.openable`,
 * `.transcribable`, `.reason`), the same one the Recordings page's rows read, so the two places
 * cannot disagree about a recording.
 *
 * It only chooses. What happens next is the caller's: `onOpen(session)` and
 * `onTranscribe(session)`.
 */

import { FocusTrap } from "../a11y/focus-trap.js";
import { $, el, setText } from "../core/dom.js";
import { mediaChips, when } from "../core/recording-facts.js";
import { api, ApiError } from "../transport/api.js";

/** The row's one action, as a label and whether it can be taken. Shared with the Recordings page. */
export function primaryAction(session, { busy = "" } = {}) {
  const media = session.media ?? {};
  if (session.problem) return { kind: "none", label: "Can't open", reason: session.problem };
  if (media.openable) {
    return { kind: "open", label: "Bring to main screen", reason: busy };
  }
  if (media.transcribable) return { kind: "transcribe", label: "Transcribe…", reason: busy };
  return { kind: "none", label: "Can't open", reason: media.reason || "Nothing to open." };
}

/** Why the row's action cannot be taken, as a line of text, or nothing. */
export function reasonLine(session, busy = "") {
  const { reason } = primaryAction(session, { busy });
  return reason ? el("p", { className: "picker__reason", text: reason }) : null;
}

/** A button for that action, disabled with its reason when it cannot be taken. */
export function actionButton(session, handlers, options = {}) {
  const action = primaryAction(session, options);
  const button = el("button", {
    className: action.kind === "none" ? "button" : "button button--primary",
    text: action.label,
    attrs: {
      type: "button",
      disabled: action.kind === "none" || Boolean(action.reason),
      title: action.reason || null,
      "aria-label": `${action.label.replace("…", "")}: ${session.title}`,
    },
  });
  button.addEventListener("click", () => {
    if (action.kind === "open") handlers.onOpen?.(session);
    if (action.kind === "transcribe") handlers.onTranscribe?.(session);
  });
  return button;
}

export class RecordingPicker {
  constructor(root, { onOpen, onTranscribe, busyReason = () => "" } = {}) {
    this.root = root;
    this.handlers = { onOpen, onTranscribe };
    this.busyReason = busyReason;
    if (!root) return;
    this.dialog = $("[data-picker-dialog]", root);
    this.list = $("[data-picker-list]", root);
    this.status = $("[data-picker-status]", root);
    this.trap = new FocusTrap(this.dialog ?? root);

    for (const button of root.querySelectorAll("[data-picker-close]")) {
      button.addEventListener("click", () => this.close());
    }
    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        this.close();
      }
    });
    // Every "Open a recording…" control on the page, wherever its template put it.
    document.addEventListener("click", (event) => {
      if (event.target.closest?.("[data-open-recording]")) void this.show();
    });
  }

  get isOpen() {
    return Boolean(this.root) && !this.root.hidden;
  }

  async show() {
    if (!this.root || this.isOpen) return;
    this.root.hidden = false;
    this.trap.activate(this.root.querySelector(".modal__close"));
    setText(this.status, "Loading recordings…");
    this.list?.replaceChildren();
    try {
      const body = await api.sessions();
      this.render(body.sessions ?? [], body.running_key ?? "");
    } catch (error) {
      setText(this.status, error instanceof ApiError ? error.message : "Recordings could not be read.");
    }
  }

  close() {
    if (!this.isOpen) return;
    this.root.hidden = true;
    this.trap.release();
  }

  render(sessions, runningKey) {
    const shown = sessions.filter((session) => session.key !== runningKey);
    setText(this.status, shown.length ? "" : "Nothing recorded yet.");
    const busy = this.busyReason();
    const choose = (handler) => (session) => {
      this.close();
      handler?.(session);
    };
    const handlers = {
      onOpen: choose(this.handlers.onOpen),
      onTranscribe: choose(this.handlers.onTranscribe),
    };
    this.list?.replaceChildren(
      ...shown.map((session) =>
        el("li", {
          className: "picker__item",
          children: [
            el("div", {
              className: "picker__text",
              children: [
                el("p", { className: "picker__title", text: session.title }),
                el("p", { className: "picker__meta", text: when(session.started_at) }),
                mediaChips(session.media),
                // Said in text as well as in the button's title: a disabled button cannot be
                // focused, so its tooltip is out of reach of a keyboard or a screen reader.
                reasonLine(session, busy),
              ],
            }),
            actionButton(session, handlers, { busy }),
          ],
        }),
      ),
    );
  }
}
