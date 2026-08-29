/**
 * The HTTP client.
 *
 * Every call returns parsed JSON or throws an `ApiError` carrying the backend's error envelope, so
 * a caller can show the message the backend wrote — which already says what to do — rather than
 * inventing its own.
 */

/** An HTTP failure with the backend's envelope attached. */
export class ApiError extends Error {
  constructor(status, body) {
    const envelope = body?.detail?.error ?? body?.error;
    super(envelope?.message || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = envelope?.code || "http-error";
    this.severity = envelope?.severity || "warning";
    this.body = body;
  }
}

async function request(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    // The server process is gone, not merely unhappy. Say so in those terms.
    throw new ApiError(0, {
      error: {
        code: "unreachable",
        message: "The recorder is not responding. Check that it is still running.",
        severity: "critical",
      },
      cause,
    });
  }

  if (response.status === 204) return null;

  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

export const get = (path) => request("GET", path);
export const post = (path, body = {}) => request("POST", path, body);
export const put = (path, body = {}) => request("PUT", path, body);
export const patch = (path, body = {}) => request("PATCH", path, body);
export const del = (path) => request("DELETE", path);

/**
 * Send a file as multipart form data.
 *
 * Separate from `request` because the two cannot share a body: setting `Content-Type` by hand on a
 * `FormData` body omits the multipart boundary the browser generates, and the server then rejects
 * a request that looks perfectly well-formed from here.
 */
async function upload(path, file) {
  const form = new FormData();
  form.append("file", file);

  let response;
  try {
    response = await fetch(path, { method: "POST", body: form });
  } catch (cause) {
    throw new ApiError(0, {
      error: {
        code: "unreachable",
        message: "The recorder is not responding. Check that it is still running.",
        severity: "critical",
      },
      cause,
    });
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

/** The endpoints, named so no call site builds a path by hand. */
export const api = {
  health: () => get("/api/health"),

  session: () => get("/api/session"),
  captureState: () => get("/api/capture/state"),
  startSession: (metadata = {}) => post("/api/session/start", metadata),
  stopSession: () => post("/api/session/stop"),

  devices: () => get("/api/audio/devices"),
  selectDevice: (body) => post("/api/audio/device", body),
  // Opens the device for a moment and reports what arrived. Enumerating one proves only that the
  // host knows about it, not that a microphone is plugged in, unmuted, and at a usable level.
  testDevice: (deviceId) => post("/api/audio/test", { device_id: deviceId }),

  // The recording library. `audio.file_path` is a path on the server, which a browser cannot
  // resolve from a file picker — so the server lists what it has and accepts uploads into it.
  audioFiles: () => get("/api/audio/files"),
  uploadAudioFile: (file) => upload("/api/audio/files", file),
  deleteAudioFile: (path) => del(`/api/audio/files?path=${encodeURIComponent(path)}`),

  // Recordings this machine captured, as opposed to the library above. A recording only survives
  // here because a pass failed, was interrupted, or because audio retention is on (D-021).
  recordings: () => get("/api/recordings"),
  transcribeRecording: (name) => post(`/api/recordings/${encodeURIComponent(name)}/transcribe`),
  deleteRecording: (name) => del(`/api/recordings/${encodeURIComponent(name)}`),

  captureState: () => get("/api/capture/state"),
  transcriptRevisions: () => get("/api/transcript/revisions"),
  transcriptAt: (revision) => get(`/api/transcript/at/${revision}`),

  asrModels: () => get("/api/asr/models"),
  loadModel: (body) => post("/api/asr/load", body),
  unloadModel: () => post("/api/asr/unload"),
  setPrompt: (body) => post("/api/asr/prompt", body),

  llmConfig: () => get("/api/llm/config"),
  llmStatus: () => get("/api/llm/status"),
  llmModels: () => get("/api/llm/models"),
  // Takes the unsaved form values, so the button tests the address on screen rather than the one
  // last saved — which is the whole point of pressing it after typing a new one.
  testLlm: (overrides = {}) => post("/api/llm/test", overrides),
  storeLlmCredential: (provider, value) => put("/api/llm/credential", { provider, value }),
  clearLlmCredential: (provider) => del(`/api/llm/credential/${encodeURIComponent(provider)}`),

  config: () => get("/api/config"),
  patchConfig: (changes, layer = "runtime") => patch("/api/config", { changes, layer }),
  applyPreset: (name) => post("/api/config/preset", { name }),
  saveConfig: () => post("/api/config/save"),

  // Chat. `chatSend` returns as soon as the request is accepted; the answer arrives as
  // `chat.delta` frames on the WebSocket and ends with `chat.done`.
  chatSend: (body) => post("/api/chat/send", body),
  chatCancel: () => post("/api/chat/cancel"),
  chatHistory: () => get("/api/chat/history"),
  clearChatHistory: () => del("/api/chat/history"),
  quickActions: () => get("/api/chat/quick-actions"),
  markRead: (position) => post("/api/chat/read", { position }),

  since: (segmentId, limit = 500) => get(`/api/transcript/since/${segmentId}?limit=${limit}`),
  range: (start, end) => get(`/api/transcript/range?start=${start}&end=${end}`),
  search: (query, limit = 50) =>
    get(`/api/transcript/search?q=${encodeURIComponent(query)}&limit=${limit}`),
  // Empty whenever no language model has been available, which is the signal to keep showing
  // raw segments rather than an error.
  polished: () => get("/api/transcript/polished"),
  summaries: () => get("/api/transcript/summaries"),
  glossary: () => get("/api/transcript/glossary"),
  exportUrl: (fmt) => `/api/transcript/export?fmt=${encodeURIComponent(fmt)}`,

  // Past sessions. The live transcript routes read the *running* session's store and stop
  // answering the moment it ends, which is exactly when a finished talk needs retrieving.
  sessions: () => get("/api/sessions"),
  pastSession: (key) => get(`/api/sessions/${encodeURIComponent(key)}`),
  deleteSession: (key) => del(`/api/sessions/${encodeURIComponent(key)}`),
  sessionExportUrl: (key, fmt) =>
    `/api/sessions/${encodeURIComponent(key)}/export?fmt=${encodeURIComponent(fmt)}`,
  /** The self-contained web application, as a ZIP. Only offered when a session holds all three. */
  sessionWebappUrl: (key) => `/api/sessions/${encodeURIComponent(key)}/webapp`,
};
