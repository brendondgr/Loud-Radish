/**
 * A tiny publish/subscribe bus.
 *
 * Stores publish here and components subscribe. That indirection is what keeps a component from
 * holding a reference to another component: the transcript pane does not know the status bar
 * exists, and neither knows about the socket.
 */

const listeners = new Map();

/**
 * Subscribe to a topic.
 *
 * @param {string} topic
 * @param {(payload: any) => void} handler
 * @returns {() => void} an unsubscribe function
 */
export function on(topic, handler) {
  if (!listeners.has(topic)) listeners.set(topic, new Set());
  listeners.get(topic).add(handler);
  return () => off(topic, handler);
}

/** Unsubscribe a handler. */
export function off(topic, handler) {
  listeners.get(topic)?.delete(handler);
}

/**
 * Publish to a topic.
 *
 * A throwing subscriber is logged and skipped rather than allowed to stop the others. One broken
 * component must not take the transcript down with it.
 */
export function emit(topic, payload) {
  const handlers = listeners.get(topic);
  if (!handlers) return;
  for (const handler of [...handlers]) {
    try {
      handler(payload);
    } catch (error) {
      console.error(`Subscriber to "${topic}" failed:`, error);
    }
  }
}

/** Remove every subscription. Used by tests. */
export function reset() {
  listeners.clear();
}
