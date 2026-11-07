/**
 * How a past recording is described, wherever it is described (D-077).
 *
 * The recordings page's rows and the recording window both say when a recording was made, what it
 * holds and which transcript passes it has. They used to be one file's private helpers; with two
 * readers, a second copy would be the first thing to drift.
 */

import { el } from "./dom.js";

/**
 * Named the way the transcript pane names them: "Final" means something to a reader and
 * "revision 1" does not. Anything beyond the two the application produces today falls back to a
 * number rather than being hidden.
 */
const REVISION_LABELS = { 0: "Live", 1: "Final" };
export const revisionLabel = (revision) => REVISION_LABELS[revision] ?? `Version ${revision}`;

export const FORMATS = [
  ["markdown", "Markdown"],
  ["text", "Plain text"],
  ["srt", "SRT"],
  ["vtt", "WebVTT"],
  ["json", "JSON"],
];

export function when(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

export function size(bytes) {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** The three things a recording can hold, in a fixed order so a column of rows scans. */
const MEDIA = [
  ["video", "Video"],
  ["audio", "Audio"],
  ["transcript", "Transcript"],
];

/**
 * What a chip's tooltip says.
 *
 * Audio gets a sentence of its own because "yes" has two meanings worth telling apart: the
 * separate recording is still on disk, or a successful transcription pass deleted it and the sound
 * now lives in the video. Both are audio; only one of them can be transcribed again.
 */
function chipTitle(key, label, present, media) {
  if (!present) return `No ${label.toLowerCase()}`;
  if (key !== "audio") return `${label} is available`;
  return media.audio_file
    ? "The recorded audio is still on disk and can be transcribed again"
    : "The audio is in the video — the separate recording was removed once it was transcribed";
}

/**
 * What this recording holds — video, audio, transcript — as three chips.
 *
 * All three are always drawn, present or not. A row that showed only what it had would need
 * reading rather than scanning, because "video · transcript" and "video · audio" have the same
 * shape and different meanings.
 */
export function mediaChips(media = {}) {
  return el("div", {
    className: "session__media",
    attrs: { "aria-label": "What this recording holds" },
    children: MEDIA.map(([key, label]) => {
      const present = Boolean(media[key]);
      return el("span", {
        className: "media-chip",
        text: label,
        attrs: { "data-present": String(present), title: chipTitle(key, label, present, media) },
      });
    }),
  });
}

/** What deleting this recording takes with it, said before it is taken. */
export function deletionWarning(session) {
  const media = session.media ?? {};
  const parts = [];
  if (!session.orphan) parts.push("the transcript, summaries and glossary");
  if (session.chat_messages) parts.push("your conversation with the assistant");
  if (media.video) parts.push("the video");
  if (media.audio) parts.push("the audio");
  const list =
    parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  return `Delete "${session.title}"? ${list ? `This removes ${list}. ` : ""}It cannot be undone.`;
}
