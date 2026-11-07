/**
 * What can be done with a recording, and doing it (D-085).
 *
 * One list per recording, so a card's hover buttons, its ⋯ menu and the compact window offer the
 * same things under the same names. Each action is a link (downloads, which the browser handles)
 * or a `run` the page carries out.
 */

import { deletionWarning } from "../core/recording-facts.js";
import { api, ApiError } from "../transport/api.js";

/** `[format, menu label, short label]`; the short one is for a row of buttons. */
export const TRANSCRIPT_FORMATS = [
  ["markdown", "Markdown", "Markdown"],
  ["text", "Plain text", "Text"],
  ["srt", "Subtitles (SRT)", "SRT"],
  ["vtt", "Subtitles (WebVTT)", "WebVTT"],
  ["json", "JSON", "JSON"],
];

export const transcriptUrl = (key, fmt = "markdown", revision) =>
  api.sessionExportUrl(key, fmt, revision);

/**
 * The actions for one recording.
 *
 * @param {object} session - a row from `/api/sessions`
 * @param {object} context - `{ busy, running }`: why nothing can be opened right now, and whether
 *   this is the recording being made.
 */
export function actionsFor(session, { busy = "", running = false } = {}) {
  const key = session.key;
  const media = session.media ?? {};
  const list = [];

  if (session.problem) {
    list.push({ id: "primary", kind: "primary", label: "Can't open", icon: "main", disabled: session.problem });
  } else if (media.openable) {
    list.push({ id: "primary", kind: "primary", label: "Bring to main screen", icon: "main", run: "open", disabled: busy });
  } else if (media.transcribable) {
    list.push({ id: "primary", kind: "primary", label: "Transcribe…", icon: "wand", run: "transcribe", disabled: busy });
  } else {
    list.push({ id: "primary", kind: "primary", label: "Can't open", icon: "main", disabled: media.reason || "Nothing to open." });
  }

  if (!session.orphan && session.segments > 0) {
    list.push({ id: "transcript", label: "Download transcript", icon: "transcript", href: transcriptUrl(key) });
  }
  if (session.chat_messages) {
    list.push({ id: "chat", label: "Download conversation", icon: "ask", href: api.sessionChatUrl(key, "markdown") });
  }
  if (media.video) {
    list.push({ id: "video", label: "Download video", icon: "video", href: api.fileUrl(key, "video") });
  }
  if (media.audio_file) {
    list.push({ id: "audio", label: "Download audio", icon: "audio", href: api.fileUrl(key, "audio") });
  }
  if (media.exportable) {
    list.push({ id: "webapp", label: "Export web app…", icon: "webapp", run: "webapp" });
  }
  if (session.orphan || media.recording_bytes > 0) {
    list.push({ id: "folder", label: "Open folder", icon: "folder", run: "folder" });
  }
  list.push({
    id: "delete",
    kind: "danger",
    label: "Delete…",
    icon: "trash",
    run: "delete",
    disabled: running ? "Stop the recording before deleting it." : "",
  });
  return list;
}

/**
 * Carry out an action that is not a plain link.
 *
 * @param {object} page - `{ consent, exportWindow, report }`
 * @returns {Promise<"deleted"|"opened"|null>} what changed, so the page can redraw.
 */
export async function runAction(action, session, { consent, exportWindow, report }) {
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
    if (action.run === "webapp") {
      await exportWindow(session);
      return null;
    }
    if (action.run === "folder") {
      const result = await api.revealFolder(key);
      if (!result?.opened && result?.path) {
        await navigator.clipboard?.writeText(result.path).catch(() => {});
        report(`No file manager answered. The folder's path is copied: ${result.path}`);
      } else {
        report("Opened the folder in your file manager.");
      }
      return null;
    }
    if (action.run === "delete") {
      // The same warning everywhere a recording can be deleted, naming what goes with it.
      if (!window.confirm(deletionWarning(session))) return null;
      await api.deleteSession(key);
      report("Deleted the recording.");
      return "deleted";
    }
  } catch (error) {
    report(error instanceof ApiError ? error.message : "That didn't work.");
  }
  return null;
}
