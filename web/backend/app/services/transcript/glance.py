"""What a recording is about, in a line, without a language model (D-082).

The recordings lab tries out cards that name a recording by its content rather than by the moment
it started — 316 recordings titled "Session on <date>" cannot be told apart. The real version will
ask the model for a four-to-six-word title once a transcript is finished; until then this gives
the lab something honest to show: the transcript's opening words, its first summary, and the words
it uses most.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .store import TranscriptStore

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")

#: Function words, fillers and the things every talk says. Not exhaustive; a keyword that slips
#: through is a weak title, not a wrong one.
_STOP = frozenset(
    """
    about above after again against all also although always among and another any anyone anything
    are aren't around because been before being below between both but can can't cannot could
    couldn't did didn't does doesn't doing don't done down during each either else enough even ever
    every few for from further get gets getting give go goes going gonna gotta got had hasn't have
    haven't having he'd he'll he's her here hers herself him himself his how however i'd i'll i'm
    i've into isn't it's its itself just kind know let let's like little look lot lots made make
    makes making many may maybe might mine more most much must myself need never next nor not
    nothing now off okay once one only onto other others our ours ourselves out over own pretty
    probably put quite rather really right said same say says see seems she'd she'll she's should
    shouldn't since some something somewhere still such sure take than that that's the their theirs
    them themselves then there there's these they they'd they'll they're they've thing things think
    this those though through thus together too toward under until upon use used using very want
    wanted was wasn't way we'd we'll we're we've well went were weren't what what's whatever when
    where whether which while who who's whole whom whose why will with within without won't would
    wouldn't yeah yes yet you you'd you'll you're you've your yours yourself yourselves actually
    basically going thank thanks today gonna wanna stuff guys hello um uh hmm sort talk talking
    says saying first second third time times back come comes coming great good bad new old
    """.split()
)


@dataclass(frozen=True)
class Glance:
    opening: str = ""
    summary: str = ""
    keywords: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"opening": self.opening, "summary": self.summary, "keywords": list(self.keywords)}


def glance(path: Path, *, opening_words: int = 32, keywords: int = 4) -> Glance:
    """Read one session and describe it. An unreadable file is an empty glance, not an error."""
    try:
        with TranscriptStore(path) as store:
            segments = store.segments_for(None) or []
            summaries = store.summaries()
    except Exception:  # noqa: BLE001 - the lab lists what it can
        return Glance()

    text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    words = text.split()
    opening = " ".join(words[:opening_words]) + ("…" if len(words) > opening_words else "")
    summary = _first_sentence(summaries[0].text) if summaries else ""
    return Glance(opening=opening, summary=summary, keywords=_keywords(text, keywords))


def _first_sentence(text: str, limit: int = 160) -> str:
    text = re.sub(r"[#*_>`]+", "", text).strip()
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def _keywords(text: str, count: int) -> list[str]:
    tally: Counter[str] = Counter()
    for match in _WORD.finditer(text):
        word = match.group().lower().strip("'-")
        if len(word) > 3 and word not in _STOP:
            tally[word] += 1
    # Seen once is noise in a long talk; in a short one it is all there is.
    floor = 2 if sum(tally.values()) > 120 else 1
    ranked = [word for word, seen in tally.most_common(count * 3) if seen >= floor]
    return [word.capitalize() for word in ranked[:count]]
