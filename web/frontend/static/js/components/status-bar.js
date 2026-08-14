/**
 * The status bar.
 *
 * Real-time factor gets special treatment because it is the pipeline's most important health
 * number. Below 1.0 it turns red **and** gains a warning icon — a user who does not understand the
 * number still understands red, but colour alone is not an accessible signal (FE §6.2, §13).
 */

import { on } from "../core/bus.js";
import { $, $$, setAttr, setText, toggle } from "../core/dom.js";
import { latency, realtimeFactor } from "../core/format.js";
import { HEALTH_CHANGED, health } from "../stores/health.js";

const CONNECTION_LABELS = {
  connected: "Connected",
  connecting: "Reconnecting…",
  disconnected: "Disconnected",
};

export class StatusBar {
  constructor(root) {
    this.bars = $$(".level-meter__bar", root);
    this.vadDot = $("[data-vad-dot]", root);
    this.vadLabel = $("[data-vad-label]", root);
    this.rtf = $("[data-rtf]", root);
    this.rtfIcon = $("[data-rtf-icon]", root);
    this.latency = $("[data-latency]", root);
    this.source = $("[data-source-label]", root);
    this.connectionDot = $("[data-connection-dot]", root);
    this.connectionLabel = $("[data-connection-label]", root);

    on(HEALTH_CHANGED, () => this.render());
    this.render();
  }

  render() {
    this._renderLevel();
    this._renderSpeech();
    this._renderThroughput();
    this._renderConnection();
    setText(this.source, health.source || "");
  }

  _renderLevel() {
    // Each bar lights at a progressively higher level, so the meter reads as a scale rather than
    // five copies of the same number.
    this.bars.forEach((bar, index) => {
      const threshold = index * 0.18;
      const filled = Math.max(0, Math.min(1, (health.level.rms - threshold) / 0.25));
      bar.style.height = `${3 + filled * 9}px`;
      bar.style.opacity = filled > 0.05 ? "1" : "0.25";
    });
  }

  _renderSpeech() {
    setAttr(this.vadDot, "data-state", health.speaking ? "active" : "idle");
    setText(this.vadLabel, health.speaking ? "Speaking" : "Silent");
  }

  _renderThroughput() {
    const hasData = health.rtf > 0;
    setText(this.rtf, hasData ? realtimeFactor(health.rtf) : "—");
    setAttr(this.rtf, "data-health", !hasData ? "unknown" : health.fallingBehind ? "bad" : "good");
    toggle(this.rtfIcon, health.fallingBehind);
    setText(this.latency, health.commitLatency ? latency(health.commitLatency) : "—");
  }

  _renderConnection() {
    const state = health.connection;
    setAttr(this.connectionDot, "data-state", state === "connected" ? "active" : "error");
    setText(this.connectionLabel, CONNECTION_LABELS[state] ?? state);
  }
}
