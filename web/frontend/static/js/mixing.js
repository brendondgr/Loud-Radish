/**
 * Routing the server's word about the mix into the mix store (D-081).
 *
 * Split out of `main.js`, which is near its cap, the way `viewing.js` is: everything here is wiring,
 * and none of it is needed by anything else at boot.
 *
 * **Reconciled on every connect.** The socket is a latency optimisation and `GET /api/session/mix`
 * is the answer; a page that only learned the mix from events it may have missed would show a
 * microphone as live that someone muted from another tab.
 */

import { on } from "./core/bus.js";
import { mix } from "./stores/mix.js";
import { api } from "./transport/api.js";
import {
  CONNECTED,
  CONNECTION_CHANGED,
  MIX_CHANGED,
  MIX_LEVELS,
  SESSION_CAPTURE_ENDED,
  SESSION_STARTED,
  SESSION_STATE,
} from "./transport/events.js";

export function wireMix() {
  on(MIX_CHANGED, (payload) => mix.set(payload));
  on(MIX_LEVELS, (payload) => mix.setLevels(payload?.levels));
  on(SESSION_STARTED, (payload) => mix.set(payload?.mix));
  on(SESSION_STATE, (state) => mix.set(state?.mix));
  // Capture has ended the moment this arrives; finalising and transcribing can take a while, and
  // offering a volume control for a microphone that is already closed would be a control that lies.
  on(SESSION_CAPTURE_ENDED, () => mix.set(null));
  on(CONNECTION_CHANGED, ({ state }) => {
    if (state === CONNECTED) void reconcile();
  });
}

async function reconcile() {
  try {
    mix.set(await api.mix());
  } catch {
    // 409 is the ordinary answer when nothing is recording.
    mix.set(null);
  }
}
