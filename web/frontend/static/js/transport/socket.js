/**
 * The WebSocket client.
 *
 * The socket is disposable. On every connect it says which segment it last received and the server
 * replays the rest, so a dropped connection needs no reasoning about what was missed.
 *
 * **The transcript is never cleared on disconnect.** The session is intact on the server, and
 * wiping the display is alarming and unnecessary (FE §9.2).
 */

import { emit } from "../core/bus.js";
import { CONNECTED, CONNECTING, CONNECTION_CHANGED, DISCONNECTED } from "./events.js";

/** Backoff schedule, in milliseconds. Caps quickly: this is a local server, not the internet. */
const BACKOFF_MS = [250, 500, 1000, 2000, 4000, 8000];

/** Keepalive interval. Detects a half-open socket that no error will report. */
const PING_INTERVAL_MS = 20000;

export class TranscriptSocket {
  constructor({ url } = {}) {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    this.url = url || `${scheme}://${window.location.host}/ws`;
    this.socket = null;
    this.attempt = 0;
    this.state = DISCONNECTED;
    this.closedByUs = false;
    this.pingTimer = null;
    this.retryTimer = null;

    /** The last committed segment id seen, sent on reconnect so the server replays the rest. */
    this.lastSegmentId = null;
  }

  /** Open the connection, retrying until it succeeds or `close()` is called. */
  connect() {
    this.closedByUs = false;
    this._setState(this.attempt === 0 ? CONNECTING : CONNECTING);

    try {
      this.socket = new WebSocket(this.url);
    } catch {
      this._scheduleRetry();
      return;
    }

    this.socket.addEventListener("open", () => {
      this.attempt = 0;
      this._setState(CONNECTED);
      this._send({ type: "hello", since: this.lastSegmentId });
      this._startPing();
    });

    this.socket.addEventListener("message", (event) => {
      let frame;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return;
      }
      if (frame?.event) emit(frame.event, frame.data ?? {});
    });

    this.socket.addEventListener("close", () => {
      this._stopPing();
      if (this.closedByUs) {
        this._setState(DISCONNECTED);
        return;
      }
      this._setState(DISCONNECTED);
      this._scheduleRetry();
    });

    // An error is always followed by a close, so recovery is handled there.
    this.socket.addEventListener("error", () => this.socket?.close());
  }

  /** Record the newest committed segment id, so a reconnect replays only what follows. */
  noteSegment(id) {
    if (typeof id === "number" && (this.lastSegmentId === null || id > this.lastSegmentId)) {
      this.lastSegmentId = id;
    }
  }

  /** Reconnect immediately, cancelling any pending backoff. */
  reconnectNow() {
    clearTimeout(this.retryTimer);
    this.attempt = 0;
    this.socket?.close();
    this.connect();
  }

  /** Close and stop retrying. */
  close() {
    this.closedByUs = true;
    clearTimeout(this.retryTimer);
    this._stopPing();
    this.socket?.close();
  }

  // -- internals -------------------------------------------------------------------

  _send(payload) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }

  _scheduleRetry() {
    const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)];
    this.attempt += 1;
    clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }

  _startPing() {
    this._stopPing();
    this.pingTimer = setInterval(() => this._send({ type: "ping" }), PING_INTERVAL_MS);
  }

  _stopPing() {
    clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  _setState(state) {
    if (this.state === state) return;
    this.state = state;
    emit(CONNECTION_CHANGED, { state, attempt: this.attempt });
  }
}
