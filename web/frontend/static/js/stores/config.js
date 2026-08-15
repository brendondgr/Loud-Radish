/**
 * The configuration store.
 *
 * The backend owns configuration (FE §8.1). This store holds a *cache* of what the backend last
 * said, never a parallel notion of the truth: every write goes to the server and the server's
 * response replaces the cache wholesale. So a value the backend clamped, coerced, or refused shows
 * up in the interface immediately, rather than the form and the pipeline quietly disagreeing.
 *
 * That is why `patch` re-seeds from the response instead of optimistically applying the change.
 * Optimism here would mean a settings panel that shows 40 while the pipeline runs at 30.
 */

import { emit } from "../core/bus.js";
import { api } from "../transport/api.js";

export const CONFIG_CHANGED = "config:changed";

/** Read a dotted path out of a nested object. */
export function readPath(source, path) {
  let cursor = source;
  for (const part of path.split(".")) {
    if (cursor === null || typeof cursor !== "object" || !(part in cursor)) return undefined;
    cursor = cursor[part];
  }
  return cursor;
}

class ConfigStore {
  constructor() {
    /** The resolved configuration, exactly as the backend last returned it. */
    this.data = {};
    /** Named profiles, for the preset control. */
    this.presets = [];
    /** True once a load has succeeded, so components can tell "empty" from "not yet fetched". */
    this.loaded = false;
    /** Set when the last write reported a cost beyond `live`, for the footer to explain. */
    this.lastConsequence = "";
    this.lastHotSwap = "live";
  }

  /** Fetch the full configuration. Safe to call repeatedly. */
  async load() {
    const { config, presets } = await api.config();
    this.data = config;
    this.presets = presets ?? [];
    this.loaded = true;
    emit(CONFIG_CHANGED, this);
    return this;
  }

  /** Read one dotted path from the cache. */
  get(path) {
    return readPath(this.data, path);
  }

  /**
   * Write dotted-path changes and adopt whatever the backend resolved.
   *
   * Returns the patch response, so a caller can warn about a disruptive change *after* the fact
   * as well as before — the two are different messages and both are wanted.
   */
  async patch(changes) {
    const response = await api.patchConfig(changes);
    this.data = response.config;
    this.lastHotSwap = response.hot_swap;
    this.lastConsequence = response.consequence;
    emit(CONFIG_CHANGED, this);
    return response;
  }

  /** Apply a named profile. */
  async applyPreset(name) {
    const response = await api.applyPreset(name);
    this.data = response.config;
    this.lastHotSwap = response.hot_swap;
    this.lastConsequence = response.consequence;
    emit(CONFIG_CHANGED, this);
    return response;
  }

  /** Persist runtime changes to the config file, so they survive a restart. */
  async save() {
    return api.saveConfig();
  }
}

export const config = new ConfigStore();
