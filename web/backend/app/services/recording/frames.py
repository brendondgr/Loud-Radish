"""One still frame from a recording's video, as a JPEG (D-082).

The recordings lab shows each recording as a card with a picture of what was on screen, and lets a
pointer drag across the card to look at other moments. The capture pipeline already writes one
``preview.jpg`` per window recording; this produces the others on request.

**Seeking is by seconds, not by fraction.** Window recordings are WebM files written live, which
carry no duration in their header — PyAV reports 0 for them — so a caller passes the time it wants,
worked out from the session's own duration.

PyAV rather than ffmpeg, which is not installed on every machine this runs on (D-078).
"""

from __future__ import annotations

import io
import logging
import threading
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

#: Frames already made, keyed by file, its modification time, the moment and the width. Small
#: enough to hold a few hundred cards' worth (a 480-pixel JPEG is 10–20 KB).
_CACHE: OrderedDict[tuple[str, float, float, int], bytes] = OrderedDict()
_CACHE_LIMIT = 400
_LOCK = threading.Lock()


def frame_jpeg(video: Path, seconds: float, width: int = 480) -> bytes | None:
    """The frame at ``seconds`` (or the last one before it) as a JPEG ``width`` pixels wide.

    ``None`` when the file has no video stream or cannot be decoded; a card then falls back to the
    stored preview or to its placeholder rather than showing a broken image.
    """
    try:
        stamp = video.stat().st_mtime
    except OSError:
        return None
    key = (str(video), stamp, round(seconds, 1), width)
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]

    body = _decode(video, max(0.0, seconds), width)
    if body is None:
        return None
    with _LOCK:
        _CACHE[key] = body
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return body


def _decode(video: Path, seconds: float, width: int) -> bytes | None:
    try:
        import av
    except ImportError:  # pragma: no cover - av is a direct dependency
        return None

    try:
        with av.open(str(video)) as source:
            if not source.streams.video:
                return None
            stream = source.streams.video[0]
            if seconds > 0:
                source.seek(int(seconds * av.time_base), any_frame=False)
            frame = None
            for decoded in source.decode(stream):
                frame = decoded
                if decoded.time is None or decoded.time >= seconds - 0.05:
                    break
            if frame is None:
                return None
            return _encode(frame, width)
    except Exception:  # noqa: BLE001 - a frame is decoration; never fail a page over one
        logger.debug("No frame at %.1fs from %s", seconds, video, exc_info=True)
        return None


def _encode(frame, width: int) -> bytes:  # noqa: ANN001 - av.VideoFrame
    import av

    width = max(64, min(width, frame.width))
    width -= width % 2
    height = max(2, round(frame.height * width / frame.width / 2) * 2)
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mjpeg") as output:
        stream = output.add_stream("mjpeg", rate=1)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuvj420p"
        image = frame.reformat(width=width, height=height, format="yuvj420p")
        for packet in stream.encode(image):
            output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return buffer.getvalue()
