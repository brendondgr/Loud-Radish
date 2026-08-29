"""Package a finished recording as a self-contained web application, in a ZIP.

The application can already export a transcript as text, Markdown, SRT, VTT, or JSON. None of those
carry the video, and none of them can be asked a question. This one produces a folder that opens in
a browser with no server, no install, and no network: the recording, the transcript that follows it
like captions, and a panel that asks a local language model about both.

**Assembled server-side because only the server can read the video off disk.** Everything else in
the archive is static — the page, its stylesheet, its script — and is copied verbatim from
``template/`` rather than generated, so the exported application is an ordinary set of files a
person can open, read, and edit.

**The video is streamed into the archive rather than read into memory.** A talk is measured in
hundreds of megabytes and this runs in the same process as the speech model.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...config.schema import AppConfig
from ...models.session import SessionMetadata
from ..recording.layout import RecordingLayout
from ..transcript.store import TranscriptStore
from .payload import DATA_DIR, MEDIA_DIR, settings_payload, transcript_payload

logger = logging.getLogger(__name__)

#: The page and its assets, copied verbatim into every export. Order does not matter here; the
#: page's own `<script>` tags decide load order.
TEMPLATE_DIR = Path(__file__).parent / "template"
TEMPLATE_FILES = (
    "index.html",
    "theme.css",
    "app.css",
    "util.js",
    "layout.js",
    "player.js",
    "transcript.js",
    "assistant.js",
    "app.js",
)

#: Container extension to the MIME type a `<video>` element needs. Anything unrecognised is handed
#: over without a type, which makes the browser sniff it — worse than a correct type and much
#: better than a wrong one.
VIDEO_TYPES = {
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".ogg": "video/ogg",
    ".avi": "video/x-msvideo",
}

#: Read the video in chunks this size. Large enough that the loop is not the cost, small enough
#: that a two-hour recording never sits in memory.
CHUNK_BYTES = 1 << 20


class ExportError(RuntimeError):
    """The session cannot be exported. The message says which piece is missing."""


def video_type(path: Path) -> str:
    return VIDEO_TYPES.get(path.suffix.lower(), "")


def build_webapp(
    *,
    key: str,
    store: TranscriptStore,
    metadata: SessionMetadata | None,
    layout: RecordingLayout,
    config: AppConfig,
) -> bytes:
    """Return the ZIP for one session.

    Raises:
        ExportError: when there is no video, or the transcript is empty. Both are refusals rather
            than degraded exports: a "web application" with an empty transcript panel and no video
            is not the thing the user asked for, and shipping one under the same name disappoints
            quietly instead of explaining.
    """
    video = layout.existing_video()
    if video is None:
        raise ExportError(
            "This recording has no video, so there is nothing for the player to show. "
            "The transcript can still be exported as Markdown, SRT, or JSON."
        )

    transcript = transcript_payload(
        key=key,
        store=store,
        metadata=metadata,
        video_name=video.name,
        video_type=video_type(video),
    )
    if not transcript["segments"]:
        raise ExportError(
            "This recording has no transcript yet, so there would be nothing to read along with "
            "or ask about. Run a transcription pass over it first."
        )

    buffer = io.BytesIO()
    # Deflate the JSON and the page; the video is already compressed and re-compressing it costs
    # minutes of CPU to save nothing.
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in TEMPLATE_FILES:
            archive.writestr(f"{key}/{name}", (TEMPLATE_DIR / name).read_text(encoding="utf-8"))
        settings = settings_payload(config)
        archive.writestr(f"{key}/{DATA_DIR}/transcript.json", _json(transcript))
        archive.writestr(f"{key}/{DATA_DIR}/settings.json", _json(settings))
        # **The same two documents again, as a script.** Every browser refuses `fetch` on a
        # `file://` URL, and this page has to open by double-clicking it in a file manager. A
        # classic `<script>` tag is subject to no such rule. The page prefers the JSON files when
        # they are readable — so serving the folder makes an edit take effect — and falls back to
        # this when they are not.
        archive.writestr(f"{key}/{DATA_DIR}/bundle.js", _bundle(transcript, settings))
        archive.writestr(f"{key}/README.txt", _readme(key, transcript))
        _write_video(archive, f"{key}/{MEDIA_DIR}/{video.name}", video)

    logger.info("Exported session %s as a web application (%d bytes)", key, buffer.tell())
    return buffer.getvalue()


def _write_video(archive: zipfile.ZipFile, name: str, path: Path) -> None:
    """Stream the video in, so a two-hour recording never sits in memory."""
    info = zipfile.ZipInfo(name)
    # Stored, not deflated: WebM and MP4 are already compressed, and deflating them again spends
    # minutes of CPU for a fraction of a percent.
    info.compress_type = zipfile.ZIP_STORED
    with path.open("rb") as source, archive.open(info, "w") as target:
        for chunk in _chunks(source):
            target.write(chunk)


def _chunks(handle: Any) -> Iterator[bytes]:
    while True:
        chunk = handle.read(CHUNK_BYTES)
        if not chunk:
            return
        yield chunk


def _bundle(transcript: dict[str, Any], settings: dict[str, Any]) -> str:
    """The two documents as an assignment a classic script tag can load from a `file://` page."""
    payload = _json({"transcript": transcript, "settings": settings})
    banner = (
        "/* Generated. The same content as data/transcript.json and data/settings.json, in a\n"
        " * form a browser will load from a file:// page — `fetch` is refused there and a\n"
        " * script tag is not. Edit the JSON files rather than this one: the page prefers them\n"
        " * whenever it can read them, and falls back here only when the folder was opened\n"
        " * directly from a file manager.\n"
        " */\n"
    )
    return f"{banner}window.EXPORT_BUNDLE = {payload};\n"


def _json(payload: dict[str, Any]) -> str:
    # Indented and not ASCII-escaped: these are files a person is expected to open and edit, and
    # `settings.json` in particular is the documented way to point the page at a different model.
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _readme(key: str, transcript: dict[str, Any]) -> str:
    session = transcript["session"]
    return f"""{session["title"]}
{"=" * max(3, len(session["title"]))}

A self-contained web application for this recording, exported from the Live Seminar Transcriber.

To open it
----------
Open index.html in any browser. There is no server to run and no install step. Everything the page
needs is in this folder.

What is here
------------
index.html            The application. Open this.
theme.css, app.css    Its stylesheets.
util.js … app.js      Its scripts, loaded in the order index.html lists them.
media/                The video, exactly as it was recorded.
data/transcript.json  The transcript, summaries, glossary, and any conversation you already had.
data/settings.json    Which language model to ask, and how. Edit it in a text editor if you like.
data/bundle.js        The same two documents as a script, for when the page is opened from a file.

A note on those last three. Browsers refuse to let a page opened from a file read its own data
files, so opening index.html directly falls back to data/bundle.js — which means an edit to
settings.json will appear to do nothing. To make edits take effect, serve the folder:

    python3 -m http.server

and open the address it prints. Everything works either way; only editing needs the server.

Asking questions
----------------
The Q&A panel talks to a language model running on your own machine, over an OpenAI-compatible
endpoint — Ollama, LM Studio, llama.cpp, and vLLM all speak it. Set the endpoint and the model name
in the panel's settings; nothing is sent anywhere else, and no key was exported with this archive.

If a request fails with a network error, the model server is probably refusing the page's origin.
For Ollama, start it with OLLAMA_ORIGINS="*" (or the specific origin you are serving this from).

Layout
------
Every panel can be collapsed, moved to the other column, or closed. Closing the Q&A panel puts the
video and the transcript side by side and gives the video most of the screen. Reopen anything you
closed from the bar at the top. The arrangement is remembered in this browser.

Session {key}
{session["segments"]} segments, {session["words"]} words.
"""
