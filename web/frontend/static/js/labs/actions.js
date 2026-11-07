/**
 * What can be done with a recording, and doing it (D-080).
 *
 * One list per recording, so a card's quick buttons, its ⋯ menu and the detail panel offer the
 * same things under the same names. Each action is either a link (downloads, which the browser
 * handles) or a `run` that calls the API.
 *
 * Every action here is real — the lab is for judging a design against the user's own recordings,
 * and a button that does nothing cannot be judged.
 */

import { deletionWarning } from "../core/recording-facts.js";
import { api, ApiError, post } from "../transport/api.js";

const PRIMARY_LABELS = {
  main: "Bring to main screen",
  open: "Open in app",
  import: "Import",
  ask: "Ask about it",
};

/** `[format, menu label, short label]` — the short one is for a row of buttons. */
export const TRANSCRIPT_FORMATS = [
  ["markdown", "Markdown", "Markdown"],
  ["text", "Plain text", "Text"],
  ["srt", "Subtitles (SRT)", "SRT"],
  ["vtt", "Subtitles (WebVTT)", "WebVTT"],
  ["json", "JSON", "JSON"],
];

const enc = encodeURIComponent;

export const transcriptUrl = (key, fmt = "markdown") => `/api/sessions/${enc(key)}/export?fmt=${fmt}`;

/**
 * The actions for one recording.
 *
 * @param {object} session - a row from `/api/sessions`
 * @param {object} context - `{ settings, busy }`; `busy` is why nothing can be opened right now.
 */
export function actionsFor(session, { settings, busy = "" }) {
  const key = session.key;
  const media = session.media ?? {};
  const list = [];

  if (media.openable) {
    list.push({
      id: "primary",
      kind: "primary",
      label: PRIMARY_LABELS[settings.primaryLabel] ?? PRIMARY_LABELS.main,
      icon: settings.primaryLabel === "ask" ? "ask" : "main",
      run: "open",
      disabled: busy,
    });
  } else if (media.transcribable) {
    list.push({ id: "primary", kind: "primary", label: "Transcribe…", icon: "wand", run: "transcribe", disabled: busy });
  } else {
    list.push({ id: "primary", kind: "primary", label: "Can't open", icon: "main", disabled: media.reason || "Nothing to open." });
  }

  if (!session.orphan && session.segments > 0) {
    list.push({ id: "transcript", label: "Download transcript", icon: "transcript", href: transcriptUrl(key) });
  }
  if (media.video) {
    list.push({ id: "video", label: "Download video", icon: "video", href: `/api/labs/sessions/${enc(key)}/file/video` });
  }
  if (media.audio_file) {
    list.push({ id: "audio", label: "Download audio", icon: "audio", href: `/api/labs/sessions/${enc(key)}/file/audio` });
  }
  if (media.exportable) {
    list.push({ id: "webapp", label: "Export web app", icon: "webapp", href: `/api/sessions/${enc(key)}/webapp` });
  }
  if (session.orphan || media.recording_bytes > 0) {
    list.push({ id: "folder", label: "Open folder", icon: "folder", run: "folder" });
  }
  list.push({ id: "delete", kind: "danger", label: "Delete", icon: "trash", run: "delete" });
  return list;
}

/**
 * Carry out an action that is not a plain link.
 *
 * @returns {Promise<"deleted"|"opened"|null>} what changed, so the page can redraw.
 */
export async function runAction(action, session, { consent, report }) {
  const key = session.key;
  try {
    if (action.run === "open") {
      await api.openRecording(key);
      window.location.assign("/");
      return "opened";
    }
    if (action.run === "transcribe") {
      const started = await consent.show(key);
      if (started) window.location.assign("/");
      return started ? "opened" : null;
    }
    if (action.run === "folder") {
      const result = await post(`/api/labs/sessions/${enc(key)}/reveal`);
      if (!result?.opened && result?.path) {
        await navigator.clipboard?.writeText(result.path).catch(() => {});
        report(`No file manager answered. The folder's path is copied: ${result.path}`);
      } else {
        report("Opened the folder in your file manager.");
      }
      return null;
    }
    if (action.run === "delete") {
      // The same warning the Recordings page gives, naming what goes with it.
      if (!window.confirm(deletionWarning(session))) return null;
      await api.deleteSession(key);
      report(`Deleted "${session.title}".`);
      return "deleted";
    }
  } catch (error) {
    report(error instanceof ApiError ? error.message : "That didn't work.");
  }
  return null;
}
