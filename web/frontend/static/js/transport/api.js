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
  startSession: (metadata = {}) => post("/api/session/start", metadata),
  stopSession: () => post("/api/session/stop"),

  devices: () => get("/api/audio/devices"),
  selectDevice: (body) => post("/api/audio/device", body),

  // The recording library. `audio.file_path` is a path on the server, which a browser cannot
  // resolve from a file picker — so the server lists what it has and accepts uploads into it.
  audioFiles: () => get("/api/audio/files"),
  uploadAudioFile: (file) => upload("/api/audio/files", file),
  deleteAudioFile: (path) => del(`/api/audio/files?path=${encodeURIComponent(path)}`),

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

  since: (segmentId, limit = 500) => get(`/api/transcript/since/${segmentId}?limit=${limit}`),
  range: (start, end) => get(`/api/transcript/range?start=${start}&end=${end}`),
  search: (query, limit = 50) =>
    get(`/api/transcript/search?q=${encodeURIComponent(query)}&limit=${limit}`),
  summaries: () => get("/api/transcript/summaries"),
  glossary: () => get("/api/transcript/glossary"),
  exportUrl: (fmt) => `/api/transcript/export?fmt=${encodeURIComponent(fmt)}`,
};
