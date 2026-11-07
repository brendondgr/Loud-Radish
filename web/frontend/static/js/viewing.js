/**
 * A past recording on the main screen (D-076): the banner, the picker, and the approval dialog.
 *
 * **Opening a recording is a reload with a different store loaded.** The server puts the chosen
 * recording's store in the slot the last session is kept in, so the transcript, the conversation
 * and the glossary are re-read through exactly the routes a page reload uses — `reload`, handed in
 * by `main.js`. No pane learns anything new.
 *
 * The view changes in four ways — opened here, opened from the Recordings page, a transcription
 * finishing with "open when done", and ending (Close, a new session, a delete) — and all four
 * arrive as `session.viewing`. The HTTP responses are adopted too, so a page whose socket is
 * reconnecting is not left showing the wrong recording; `adopt` ignores the repeat.
 */

import { on } from "./core/bus.js";
import { $, setText, toggle } from "./core/dom.js";
import { when } from "./core/recording-facts.js";
import { RecordingPicker } from "./components/recording-picker.js";
import { TranscribeConsent } from "./components/transcribe-consent.js";
import { recording } from "./stores/recording.js";
import { session } from "./stores/session.js";
import { api, ApiError } from "./transport/api.js";
import { SESSION_VIEWING } from "./transport/events.js";

/**
 * Wire the main screen's viewing controls.
 *
 * @param {object} options
 * @param {() => Promise<void>} options.reload - re-read the transcript, chat and glossary.
 * @param {(message: string) => void} options.report - show a refusal to the user.
 */
export function wireViewing({ reload, report }) {
  const banner = $("[data-viewing-banner]");
  const name = $("[data-viewing-name]", banner ?? document);
  const date = $("[data-viewing-date]", banner ?? document);
  let current = null;

  const render = () => {
    toggle(banner, Boolean(current));
    if (!current) return;
    setText(name, current.title || "a recording");
    const day = when(current.started_at);
    setText(date, day ? ` · ${day}` : "");
  };

  /** Take the server's word for what is on screen; reload only when that actually changed. */
  const adopt = async (viewing) => {
    const changed = (viewing?.key ?? null) !== (current?.key ?? null);
    current = viewing ?? null;
    render();
    if (changed) await reload();
  };

  const busyReason = () => {
    if (session.running) return "Stop the current recording first.";
    if (recording.isTranscribing) return "Wait for the running transcription to finish.";
    return "";
  };

  const consent = new TranscribeConsent($("[data-transcribe-consent]"));

  const open = async (chosen) => {
    try {
      const state = await api.openRecording(chosen.key);
      await adopt(state?.viewing ?? null);
    } catch (error) {
      report(error instanceof ApiError ? error.message : "That recording could not be opened.");
    }
  };

  new RecordingPicker($("[data-recording-picker]"), {
    onOpen: (chosen) => void open(chosen),
    // Progress then shows the way any post-capture pass does, and with "open when done" ticked
    // the recording arrives here by `session.viewing` when the pass finishes.
    onTranscribe: (chosen) => void consent.show(chosen.key),
    busyReason,
  });

  $("[data-viewing-close]", banner ?? document)?.addEventListener("click", async () => {
    try {
      const state = await api.closeRecording();
      await adopt(state?.viewing ?? null);
    } catch (error) {
      report(error instanceof ApiError ? error.message : "The recording could not be closed.");
    }
  });

  on(SESSION_VIEWING, (payload) => void adopt(payload?.viewing ?? null));

  /** The state the page loaded with. Not a change, so nothing is re-read. */
  return {
    hydrate(viewing) {
      current = viewing ?? null;
      render();
    },
  };
}
