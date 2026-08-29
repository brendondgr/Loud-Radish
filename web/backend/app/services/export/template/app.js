/* Boot: read the recording, build the four parts, and connect the video's clock to the transcript.
 *
 * **Two ways in, on purpose.** `data/transcript.json` and `data/settings.json` are the documented
 * files — editable in a text editor, and what a user changes to point this page at a different
 * model. But every browser refuses `fetch` on a `file://` URL, and this page has to open by
 * double-clicking it in a file manager. So the same two documents are also written into
 * `data/bundle.js`, which a classic `<script>` tag loads under any protocol.
 *
 * The fetched files win when they are readable, so serving the folder makes an edit take effect
 * immediately; the bundle is the fallback that makes the page work at all when opened from a file.
 * Both are written by the same export, so they never disagree until someone edits one.
 */

async function readJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} could not be read (${response.status}).`);
  return response.json();
}

/** The transcript and the settings, preferring the editable JSON and falling back to the bundle. */
async function loadExport() {
  const bundle = window.EXPORT_BUNDLE;
  try {
    const [transcript, settings] = await Promise.all([
      readJson("data/transcript.json"),
      readJson("data/settings.json"),
    ]);
    return { transcript, settings, source: "files" };
  } catch (error) {
    if (!bundle) throw error;
    return { transcript: bundle.transcript, settings: bundle.settings, source: "bundle" };
  }
}

function renderIdentity(session) {
  document.title = session.title;
  $("[data-title]").textContent = session.title;
  $("[data-meta]").textContent = [
    session.speaker,
    clock(session.duration_seconds),
    `${session.words} words`,
    `${session.segments} segments`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function reportBootFailure(error) {
  const banner = $("[data-boot-error]");
  banner.hidden = false;
  banner.textContent =
    `This recording could not be loaded: ${error.message} The data folder should sit beside ` +
    "index.html and contain transcript.json, settings.json, and bundle.js.";
}

async function boot() {
  let loaded;
  try {
    loaded = await loadExport();
  } catch (error) {
    reportBootFailure(error);
    return;
  }

  const { transcript: data, settings } = loaded;
  window.EXPORT_KEY = data.session.key || "session";
  renderIdentity(data.session);

  const layout = new Layout();
  const player = new Player(layout.panel("video"), data.media);
  const transcript = new Transcript(layout.panel("transcript"), data.segments, player);
  player.onSeek = (seconds) => transcript.at(seconds);

  new Assistant(layout.panel("qa"), { data, settings, player });
}

void boot();
