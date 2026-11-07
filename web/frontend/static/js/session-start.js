/**
 * Starting a recording: open the record sheet, then ask the server to start what it chose (D-075).
 *
 * Split out of `main.js` when the record sheet became the only way to start, and that file was
 * nearing its cap. It is the application page's start sequence and nothing else — the sheet
 * collects the answers, the server derives the mode from them, and this connects the two.
 */

import { CAPTURE_MODES, CAPTURE_SOURCES, IDLE, WINDOW } from "./core/modes.js";
import { mode } from "./stores/mode.js";
import { api } from "./transport/api.js";

/**
 * Open the record sheet, and start whatever it is confirmed with.
 *
 * Cancelling has captured nothing and starts nothing, which is why `arming` is a run state of its
 * own rather than the first moment of `recording`. `preset` preselects a source — a global shortcut
 * for "record a window" opens the sheet with a window chosen.
 */
export async function arm(sheet, { banners, settings }, preset = {}) {
  if (sheet.isOpen) return;

  mode.beginArming();
  const options = await sheet.show(preset);
  if (!options) {
    mode.setState(IDLE);
    return;
  }
  mode.arm(options);
  await start(options, { banners, settings });
}

/**
 * Open the record sheet because a global shortcut or the tray asked for it (Plan 5).
 *
 * `?arm=` rather than a second native dialog: the browser already has the sheet, and building
 * another would mean two implementations to keep in step. `?arm=window` or `&source=window`
 * preselects a window; any other mode opens the sheet as it was left.
 */
export async function armFromUrl(sheet, context) {
  const params = new URLSearchParams(window.location.search);
  const wanted = params.get("arm");
  if (!wanted || !CAPTURE_MODES.includes(wanted)) return;

  // Cleared from the address bar first, so a reload does not re-arm a recording the user has
  // since cancelled.
  window.history.replaceState({}, "", window.location.pathname);
  if (mode.action !== "arm") return;

  const source = params.get("source") ?? (wanted === WINDOW ? WINDOW : null);
  await arm(sheet, context, CAPTURE_SOURCES.includes(source) ? { source } : {});
}

/** Ask the server to start. The mode is derived there from `options`; none is sent beside them. */
async function start(options, { banners, settings }) {
  try {
    await api.startSession({ options });
  } catch (error) {
    mode.fail(error.message);

    // A source this build does not have, or options the server would not accept, are not audio
    // problems, and sending the user to the audio settings to fix them would be a wrong instruction
    // confidently given. The banner alone is the whole remedy.
    const notAudio = ["mode-unavailable", "options-conflict", "records-nothing"].includes(
      error.code
    );
    banners.show({
      code: error.code ?? "start-failed",
      severity: notAudio ? "warning" : (error.severity ?? "critical"),
      message: error.message,
      // A failure to start is otherwise nearly always the input source, and the remedy is a
      // setting. Opening the tab that fixes it beats a message telling the user to go and find it.
      remedy: notAudio ? undefined : "audio",
      remedy_label: notAudio ? undefined : "Open audio settings",
    });
    if (!notAudio) settings?.show("audio");
  }
}
