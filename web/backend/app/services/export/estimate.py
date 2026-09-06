"""What an export will cost, before anyone commits to it (D-037).

Reported: "I want to see the estimated storage the export will consume, calculated *before* I commit
to it, and how that estimate changes as I adjust quality settings." That is a prediction, so it has
to exist independently of the encode it predicts.

**Anchored to the source, not to a table.** A flat "720p costs N MB an hour" is wrong by a factor of
three depending on what is on screen: measured on this project's own capture encoder, one setting
produced 4909 kbps at 1080 × 1064 and 1038 kbps at 640 × 360. What makes a prediction survive that
is asking the recording itself how compressible it is — its own bits per pixel per frame — and then
scaling that by the output's pixel rate and by how much less the new codec and quality will spend.

**It returns a range, and says so.** A constant-quality encode's size depends on content the
estimator has not watched. The measurements below bracket a still slide deck at one end; a talk that
cuts between cameras sits at the other. A single number presented as exact would be wrong in a way
that matters, because the whole point is deciding whether a file is small enough to send.

## The measurements behind the constants

Taken from `data/recordings/20260904-155600-08ab28c2d732` — the reported seminar, VP8 at
2560 × 1532 and 15 fps — over a 120-second slice of real footage, re-encoded with `libx264
-preset veryfast`:

    source           2560x1532  15 fps   14.11 MB / 120 s   →  423 MB/hour
    High     1080p   1804x1080  15 fps    5.81 MB / 120 s   →  174 MB/hour   2.33 s to encode
    Balanced  720p   1204x720   15 fps    2.40 MB / 120 s   →   72 MB/hour   1.47 s to encode
    Small     540p    902x540   10 fps    1.79 MB / 120 s   →   54 MB/hour   1.16 s to encode

The source's own video bits per pixel per frame works out at 0.0149. Expressed as a fraction of
that, the three encodes came to 0.67, 0.33 and 0.32 — the first two exactly six CRF points apart and
therefore a factor of two, which is the rule of thumb holding. The third does not halve again,
because a mostly-static slide has a floor below which there is nothing left to remove. The model
follows the halving rule and is therefore **conservative at the small end**: it under-predicted
`Small` by about a third on this recording, which is the right direction for a figure someone is
using to decide whether something will fit.

Encode time fits `a x source megapixels + b x output megapixels` — a decode cost that does not
depend on the output size, plus an encode cost that does. **Both constants are this machine's**, a
32-core CPU, and a slower one will take longer; `scripts/calibrate_export_estimate.py` re-derives
them against any recording on any machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from .profile import EncodePlan, SourceProfile

#: Bits per pixel per frame that H.264 spends relative to what the source spent, at CRF 23.
#: Fitted to the three measurements above: 0.475 x 2^((23 - crf) / 6) gives 0.67 at CRF 20 and
#: 0.34 at CRF 26, against 0.67 and 0.33 measured.
H264_EFFICIENCY_AT_REF: Final = 0.475
REF_CRF: Final = 23.0

#: VP9 buys roughly a further third at the same CRF, and its CRF scale sits higher — which is why
#: the WebM preset asks for 32 where the H.264 ones ask for 26.
CODEC_EFFICIENCY: Final[dict[str, float]] = {
    "h264": 1.0,
    "hevc": 0.65,
    "vp9": 0.70,
    "av1": 0.60,
}

#: No encoder produces a video track thinner than this, whatever the arithmetic says. Below it the
#: prediction stops describing a file and starts describing a limit of the model.
MIN_VIDEO_BPS: Final = 24_000.0

#: How wide the range is, either side of the point estimate. Content the estimator has not watched
#: is the dominant term, and these brackets are what that uncertainty honestly looks like.
RANGE_LOW: Final = 0.6
RANGE_HIGH: Final = 1.9

#: The audio half is a requested bitrate rather than a prediction, but a container is not free and
#: a constant-bitrate encoder is not exact either: an audio-only export of the reported recording
#: measured 21.6 MB against a requested 21.2 MB. A hair either side, so that the one plan whose
#: size is nearly certain does not present a figure the file then misses by two per cent.
AUDIO_RANGE_LOW: Final = 0.97
AUDIO_RANGE_HIGH: Final = 1.06

#: Seconds per megapixel of *source*, decoded. Independent of what is being written.
DECODE_S_PER_MPX: Final = 1.33e-4

#: Seconds per megapixel of *output*, encoded, at `-preset veryfast`.
ENCODE_S_PER_MPX: Final = 3.8e-4

#: Copying streams is I/O, not arithmetic. Measured against the mux, which remuxes a two-hour
#: recording in seconds.
COPY_S_PER_GB: Final = 6.0

#: Packaging: reading the media back and writing it into a ZIP, stored rather than deflated.
PACKAGE_S_PER_GB: Final = 8.0


@dataclass(frozen=True)
class Estimate:
    """What one plan would produce, and roughly how long it would take.

    ``low_bytes`` and ``high_bytes`` are the honest answer; ``bytes`` is the point estimate inside
    them, offered because an interface has to draw one number somewhere.
    """

    bytes: int
    low_bytes: int
    high_bytes: int
    seconds: float
    #: True when the figure is arithmetic rather than a prediction — a copy, whose size is known.
    exact: bool = False
    #: Empty when the estimate stands. Otherwise says why it could not be made.
    problem: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "low_bytes": self.low_bytes,
            "high_bytes": self.high_bytes,
            "seconds": round(self.seconds, 1),
            "exact": self.exact,
            "problem": self.problem,
        }


def estimate(plan: EncodePlan, source: SourceProfile) -> Estimate:
    """Predict the size and duration of encoding ``source`` under ``plan``."""
    if not source.readable:
        return Estimate(0, 0, 0, 0.0, problem=source.problem or "The recording could not be read.")

    if plan.copy:
        # Not a prediction. The bytes are on disk and the answer is to look at them.
        return Estimate(
            bytes=source.size_bytes,
            low_bytes=source.size_bytes,
            high_bytes=source.size_bytes,
            seconds=_copy_seconds(source.size_bytes),
            exact=True,
        )

    audio_bps = plan.audio_bitrate_k * 1000.0 if source.has_audio else 0.0
    video_bps = 0.0 if plan.audio_only else _video_bps(plan, source)

    total = (video_bps + audio_bps) * source.duration_s / 8.0
    return Estimate(
        bytes=int(total),
        # The two halves carry different uncertainties and are bracketed separately. The video is
        # a prediction about content nobody has watched; the audio is a requested bitrate that a
        # container puts a couple of per cent on top of. Applying the video's range to the audio
        # would make an audio-only export look like a guess when it very nearly is not.
        low_bytes=int(
            (video_bps * RANGE_LOW + audio_bps * AUDIO_RANGE_LOW) * source.duration_s / 8.0
        ),
        high_bytes=int(
            (video_bps * RANGE_HIGH + audio_bps * AUDIO_RANGE_HIGH) * source.duration_s / 8.0
        ),
        seconds=_encode_seconds(plan, source),
    )


def _video_bps(plan: EncodePlan, source: SourceProfile) -> float:
    """The video track's predicted bitrate, from the source's own compressibility."""
    source_rate = _pixel_rate(source.width, source.height, source.frame_rate)
    output_rate = _pixel_rate(
        plan.resolved_width(source),
        plan.resolved_height(source),
        plan.resolved_frame_rate(source),
    )
    if source_rate <= 0.0 or output_rate <= 0.0:
        return 0.0

    # What this particular recording spent per pixel per frame. The audio is subtracted first: at
    # the small end it is most of the file, and counting it as picture would make every estimate
    # scale with a quantity that does not change.
    source_audio_bps = _source_audio_bps(source)
    source_video_bps = max(0.0, source.bitrate - source_audio_bps)
    source_bpp = source_video_bps / source_rate
    if source_bpp <= 0.0:
        return MIN_VIDEO_BPS

    efficiency = H264_EFFICIENCY_AT_REF * (2.0 ** ((REF_CRF - plan.crf) / 6.0))
    efficiency *= CODEC_EFFICIENCY.get(plan.video_codec, 1.0)
    return max(MIN_VIDEO_BPS, source_bpp * efficiency * output_rate)


def _source_audio_bps(source: SourceProfile) -> float:
    """What the recording's own audio track costs.

    The mux writes Opus at 64 kbps and nothing else writes audio into a recording, so this is a
    known number rather than a measured one — and `ffprobe` reports per-stream bitrates only for
    containers that store them, which WebM does not.
    """
    return 64_000.0 if source.has_audio else 0.0


def _pixel_rate(width: int, height: int, frame_rate: float) -> float:
    return max(0.0, width * height * frame_rate)


def _encode_seconds(plan: EncodePlan, source: SourceProfile) -> float:
    source_mpx = (
        _pixel_rate(source.width, source.height, source.frame_rate) * source.duration_s / 1e6
    )
    output_mpx = (
        _pixel_rate(
            plan.resolved_width(source),
            plan.resolved_height(source),
            plan.resolved_frame_rate(source),
        )
        * source.duration_s
        / 1e6
    )
    work = DECODE_S_PER_MPX * source_mpx + ENCODE_S_PER_MPX * output_mpx
    # Even an audio-only export has to walk the whole container, and a one-second answer for an
    # hour of work reads as a bug rather than as speed.
    return max(1.0, work)


def _copy_seconds(size_bytes: int) -> float:
    return max(1.0, COPY_S_PER_GB * size_bytes / 1e9)


def package_seconds(size_bytes: int) -> float:
    """Roughly how long writing ``size_bytes`` of media into the archive takes."""
    return max(1.0, PACKAGE_S_PER_GB * size_bytes / 1e9)
