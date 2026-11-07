/**
 * The details column's middle sections (D-086): this recording, and how it is captured.
 *
 * The video section is `RecordingMonitor`'s and the glossary is `GlossaryPanel`'s; this draws the
 * two in between from the stores, so it says the same as the header and the transcript:
 *
 * - **This recording** — when it started, how long it is, its words and the questions asked about
 *   it: the session being recorded or last recorded, or the past recording on the main screen.
 * - **Capture** — the source, when it transcribes, and the speech model, with a gear that opens the
 *   capture settings. While nothing runs it describes what the last recording was set up to do,
 *   which is what *Start recording* will offer first.
 */

import { on } from "../core/bus.js";
import { $, setText } from "../core/dom.js";
import { timestamp, wallClock } from "../core/format.js";
import { when } from "../core/recording-facts.js";
import * as prefs from "../core/storage.js";
import { CHAT_CHANGED, chat } from "../stores/chat.js";
import { HEALTH_CHANGED, health } from "../stores/health.js";
import { MODE_CHANGED, mode } from "../stores/mode.js";
import { SESSION_CHANGED, session } from "../stores/session.js";
import { TRANSCRIPT_CHANGED, transcript } from "../stores/transcript.js";
import { api } from "../transport/api.js";

const SOURCES = { microphone: "Microphone", system: "Computer sound", window: "A window" };

export class DetailsPane {
  /**
   * @param {HTMLElement} root
   * @param {object} hooks
   * @param {(tab: string) => void} hooks.onOpenSettings
   * @param {object} hooks.monitor - the `RecordingMonitor`, shown a viewed recording's picture
   */
  constructor(root, { onOpenSettings, monitor } = {}) {
    this.root = root;
    if (!root) return;
    this.monitor = monitor;
    this.viewing = null;
    this.fields = Object.fromEntries(
      ["started", "length", "words", "questions", "source", "timing", "model"].map((name) => [
        name,
        $(`[data-details-${name}]`, root),
      ]),
    );
    $("[data-details-settings]", root)?.addEventListener("click", () => onOpenSettings?.("audio"));

    for (const topic of [SESSION_CHANGED, MODE_CHANGED, HEALTH_CHANGED, CHAT_CHANGED]) {
      on(topic, () => this.render());
    }
    // A transcript grows by a segment at a time for two hours; counting words on each one is
    // cheap, but there is no reason to do it more than once a frame.
    on(TRANSCRIPT_CHANGED, () => this._soon());
    this.render();
  }

  /** The past recording on the main screen (D-076), or `null`. */
  setViewing(viewing) {
    this.viewing = viewing ?? null;
    this.monitor?.showPoster(this.viewing ? api.thumbnailUrl(this.viewing.key) : null);
    this.render();
  }

  _soon() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.render();
    });
  }

  render() {
    if (!this.root) return;
    this._renderRecording();
    this._renderCapture();
  }

  _renderRecording() {
    const segments = transcript.segments;
    const started = this.viewing?.started_at ?? (session.startedAt ? session.startedAt.toISOString() : "");
    const last = segments.at(-1);
    const length = last ? last.end : 0;
    const words = segments.reduce((total, segment) => total + countWords(segment.text), 0);
    const questions = chat.messages.filter((message) => message.role === "user").length;

    setText(this.fields.started, started ? when(started) : "—");
    this.fields.started?.setAttribute("title", started ? wallClock(started) : "");
    setText(this.fields.length, length ? timestamp(length) : "—");
    setText(this.fields.words, words ? words.toLocaleString() : "—");
    setText(this.fields.questions, String(questions));
  }

  _renderCapture() {
    if (this.viewing) {
      setText(this.fields.source, "A past recording");
      setText(this.fields.timing, "Already transcribed");
    } else {
      const options = mode.options ?? session.options ?? prefs.get("lastRecordOptions");
      setText(this.fields.source, options ? describeSource(options) : "Chosen when you start");
      setText(this.fields.timing, options ? describeTiming(options) : "—");
    }
    const configured = session.config?.asr?.model;
    setText(this.fields.model, health.modelId || configured || "—");
  }
}

function countWords(text) {
  const trimmed = (text ?? "").trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

function describeSource(options) {
  const source = SOURCES[options.source] ?? "—";
  const inputs = options.audio_inputs ?? [];
  if (options.source !== "microphone" && inputs.includes("microphone")) return `${source} + microphone`;
  return options.video && options.source === "window" ? `${source}, with video` : source;
}

function describeTiming(options) {
  if (options.live_transcription && options.post_transcription) return "While recording, and after";
  if (options.post_transcription) return "After it ends";
  if (options.live_transcription) return "While recording";
  return "Not transcribed";
}
