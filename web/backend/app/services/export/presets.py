"""The named export plans, and the one place their numbers live.

Five, spanning the range between "exactly what was recorded" and "small enough to email":

* **Original** — no re-encode at all. Exact, instant, and what the export has always done.
* **High** — 1080p, the source frame rate, CRF 20. For keeping.
* **Balanced** — 720p, 15 fps, CRF 26. The default, and the one the reported complaint wanted.
* **Small** — 540p, 10 fps, CRF 32. For sending to someone on a phone.
* **Audio only** — no picture at all, for when the talk is the point.

**H.264 in MP4 throughout, and that was going to be a choice until it was measured.** Capture writes
VP8 in WebM because that is what this machine's GStreamer can encode — `x264enc` is not installed.
`ffmpeg` here is a different matter and has `libx264`, `libx265`, `libvpx-vp9` and `libsvtav1`, so
the export is not bound by the capture's constraint. MP4 plays everywhere WebM does and in several
places it does not, and the stated purpose of these files is being sent to other people.

A sixth preset offered the same 720p at 15 fps as VP9 in WebM, for anywhere MP4 is unwelcome. It
was removed after `scripts/calibrate_export_estimate.py` was pointed at a real recording: against
H.264's **35.6 MB in 22 seconds**, VP9 produced **42.1 MB in 231 seconds** — a sixth larger and
ten times slower, and that already with `-cpu-used 4 -row-mt 1`, without which it took 448. An
option offered beside others that are faster and smaller, under a name that promises the "same
size", is a trap rather than a choice. Anyone who needs WebM has **Original**, which is the WebM
the capture wrote.

**Every number here is mirrored into `web/frontend/static/js/core/export-presets.js`** and held
identical by `tests/utils/test_export_preset_vocabulary.py`. The two sides disagreeing about what
"Balanced" means is a fault a test can make impossible, in the way `core/modes.js` already is.
"""

from __future__ import annotations

from typing import Final

from .profile import EncodePlan

ORIGINAL: Final = EncodePlan(
    id="original",
    label="Original",
    hint="Exactly what was recorded, copied. Instant, and the largest.",
    container="webm",
    video_codec="",
    audio_codec="",
    crf=0,
    max_height=0,
    max_frame_rate=0.0,
    copy=True,
)

HIGH: Final = EncodePlan(
    id="high",
    label="High",
    hint="1080p at the recorded frame rate. For keeping, or for showing on a large screen.",
    crf=20,
    max_height=1080,
    max_frame_rate=0.0,
    audio_bitrate_k=128,
)

BALANCED: Final = EncodePlan(
    id="balanced",
    label="Balanced",
    hint="720p at 15 fps. Slides stay readable and the file is a fraction of the recording.",
    crf=26,
    max_height=720,
    max_frame_rate=15.0,
    audio_bitrate_k=96,
)

SMALL: Final = EncodePlan(
    id="small",
    label="Small",
    hint="540p at 10 fps. For sending over a slow connection or watching on a phone.",
    crf=32,
    max_height=540,
    max_frame_rate=10.0,
    audio_bitrate_k=64,
)

AUDIO_ONLY: Final = EncodePlan(
    id="audio",
    label="Audio only",
    hint="No picture. The smallest thing that still carries the whole talk.",
    container="m4a",
    video_codec="",
    crf=0,
    max_height=0,
    max_frame_rate=0.0,
    audio_bitrate_k=96,
    audio_only=True,
)

#: In the order the export window offers them: biggest and most faithful first, because that is the
#: order someone weighing quality against size reads them in.
PRESETS: Final[tuple[EncodePlan, ...]] = (ORIGINAL, HIGH, BALANCED, SMALL, AUDIO_ONLY)

#: What an export uses when nobody chose. Not `ORIGINAL`: the whole point of the report behind this
#: was that the untouched recording is too large to share.
DEFAULT_PRESET: Final = BALANCED


def by_id(preset_id: str) -> EncodePlan | None:
    """The plan with this id, or ``None``. Never raises — callers turn it into a 422 themselves."""
    for plan in PRESETS:
        if plan.id == preset_id:
            return plan
    return None
