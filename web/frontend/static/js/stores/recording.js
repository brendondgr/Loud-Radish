/**
 * The current recording, and the transcription pass that follows it (D-021).
 *
 * Two different things with one store because they are two halves of one sequence the user reads as
 * a single operation: capture, then transcribe. Splitting them would mean two stores whose combined
 * state every consumer has to reassemble.
 *
 * The pass's state is **authoritative from the server**. It outlives the page — a reload during a
 * half-hour transcription must resume showing progress, not start again from nothing — so nothing
 * here is inferred locally.
 */

import { emit } from "../core/bus.js";

export const RECORDING_CHANGED = "store.recording.changed";

class RecordingStore {
  constructor() {
    /** Seconds captured so far, while a recording is being written. */
    this.duration = 0;
    /** Size on disk, in bytes. */
    this.bytes = 0;
    /** `running` | `done` | `failed`, or null when no pass has run this session. */
    this.state = null;
    this.progress = 0;
    this.transcribedSeconds = 0;
    this.totalSeconds = 0;
    this.segments = 0;
    this.error = "";
  }

  /** A recording is being written. */
  setRecording({ duration_s, bytes }) {
    this.duration = duration_s ?? 0;
    this.bytes = bytes ?? 0;
    emit(RECORDING_CHANGED, this);
  }

  /** Adopt a `transcription.*` payload, whichever of the three it was. */
  setTranscription(payload) {
    if (!payload) return;
    this.state = payload.state ?? null;
    this.progress = payload.progress ?? 0;
    this.transcribedSeconds = payload.transcribed_seconds ?? 0;
    this.totalSeconds = payload.total_seconds ?? 0;
    this.segments = payload.segments ?? 0;
    this.error = payload.error ?? "";
    // The measured character of the audio, carried on the same payload so an empty transcript can
    // explain itself without the interface making a second request to find out why.
    this.audio = payload.audio ?? null;
    emit(RECORDING_CHANGED, this);
  }

  /** Adopt what `GET /api/session` reported on load or reconnect. */
  hydrate(state) {
    if (state?.recording) {
      this.duration = state.recording.duration_s ?? 0;
      this.bytes = state.recording.bytes ?? 0;
    }
    if (state?.transcription) {
      this.setTranscription(state.transcription);
      return;
    }
    emit(RECORDING_CHANGED, this);
  }

  /** Clear everything at the start of a new session. */
  reset() {
    this.duration = 0;
    this.bytes = 0;
    this.state = null;
    this.progress = 0;
    this.transcribedSeconds = 0;
    this.totalSeconds = 0;
    this.segments = 0;
    this.error = "";
    this.audio = null;
    emit(RECORDING_CHANGED, this);
  }

  get isTranscribing() {
    return this.state === "running";
  }

  /**
   * The pass finished and found nothing to transcribe.
   *
   * Distinct from a failure and from an ordinary finish. An empty transcript with no explanation
   * reads as a crash — a five-minute recording that produced two segments looks identical whether
   * the transcriber broke or whether the recording was music. The server measures the audio and
   * says which, and this is how the interface knows to say so.
   */
  get foundNoSpeech() {
    return this.state === "done_no_speech";
  }

  /** One line explaining an empty transcript, drawn from the measured audio. */
  get noSpeechReason() {
    const audio = this.audio ?? {};
    if (audio.likely_content === "likely_music_or_game") {
      return "The audio was captured correctly but does not sound like speech.";
    }
    if (audio.audible_pct !== undefined && audio.audible_pct < 5) {
      return "The recording is silent — check that the right audio source was captured.";
    }
    return "No speech was detected in this recording.";
  }

  /** Progress as a whole percentage, for a label. */
  get percent() {
    return Math.round(this.progress * 100);
  }

  /** Size on disk in a unit a person reads, e.g. "14.2 MB". */
  get sizeLabel() {
    if (this.bytes < 1024) return `${this.bytes} B`;
    if (this.bytes < 1024 * 1024) return `${(this.bytes / 1024).toFixed(0)} KB`;
    return `${(this.bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
}

export const recording = new RecordingStore();
