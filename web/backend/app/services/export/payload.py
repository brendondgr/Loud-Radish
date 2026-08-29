"""What the exported web application is given: a transcript, and the settings to ask about it.

Two JSON documents, deliberately separate. ``transcript.json`` is the recording — it never changes
once written, and a second export of the same session produces the same bytes. ``settings.json`` is
how the exported page *behaves*: which model to ask, how warm, how much context, which quick
questions to offer. Separating them means a user can hand someone else a recording and keep their
own endpoint, or edit a JSON file in a text editor to point the page at a different model without
touching anything that describes the talk.

**No credential is ever written into either.** The exported page asks for an API key in its own
panel and keeps it in that browser's local storage. Exporting a talk should never be a way to hand
someone an API key, and a ZIP is exactly the sort of thing that gets forwarded.
"""

from __future__ import annotations

from typing import Any

from ...config.schema import AppConfig
from ...models.session import SessionMetadata
from ..context.prompts import SYSTEM_PROMPT
from ..transcript.store import TranscriptStore

#: Where the media lives inside the archive, relative to ``index.html``.
MEDIA_DIR = "media"
DATA_DIR = "data"

#: The exported page's own instruction. Close to the live application's, minus the two sentences
#: about a talk that is still happening — the recording has finished, the reader can scrub back,
#: and telling the model otherwise makes it apologise for things it can see.
EXPORT_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "You are helping someone follow a talk while it is being given. They are listening and cannot "
    "re-read what was said, so answer briefly and directly.",
    "You are helping someone study a recorded talk. They have the video in front of them and can "
    "jump to any moment you cite, so answer briefly and directly and cite generously.",
).replace(
    '"the speaker has not covered that yet" is a useful answer',
    '"the speaker does not cover that" is a useful answer',
)


def transcript_payload(
    *,
    key: str,
    store: TranscriptStore,
    metadata: SessionMetadata | None,
    video_name: str,
    video_type: str,
) -> dict[str, Any]:
    """Everything the exported page needs in order to render the talk.

    ``latest_segments`` rather than every row: a session with both a live and a post-capture pass
    holds two transcripts of the same audio, and concatenating them says the whole talk twice
    (D-022). The final pass is the one worth shipping.
    """
    segments = store.latest_segments()
    stats = store.stats()

    return {
        "version": 1,
        "session": {
            "key": key,
            "title": (metadata.title if metadata else "") or "Recorded talk",
            "speaker": metadata.speaker if metadata else "",
            "venue": metadata.venue if metadata else "",
            "started_at": metadata.started_at.isoformat() if metadata else "",
            "ended_at": (metadata.ended_at.isoformat() if metadata and metadata.ended_at else ""),
            "mode": metadata.mode if metadata else "",
            "duration_seconds": round(stats.duration_seconds, 2),
            "segments": stats.segment_count,
            "words": stats.word_count,
        },
        "media": {
            "src": f"{MEDIA_DIR}/{video_name}",
            "type": video_type,
        },
        # Only what the page draws or cites. Word-level tokens are deliberately dropped: they
        # multiply the file size several-fold and nothing in the exported page reads them.
        "segments": [
            {
                "id": segment.id,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text,
                "speaker": segment.speaker or "",
            }
            for segment in segments
        ],
        "summaries": [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text}
            for s in store.summaries()
        ],
        "glossary": [
            {"term": t.term, "definition": t.definition, "first_seen": round(t.first_seen, 2)}
            for t in store.glossary()
        ],
        # The conversation that already happened, so the exported page opens where the user left
        # off rather than pretending the talk was never discussed.
        "chat": [
            {
                "role": message.role,
                "text": message.text,
                "context_timestamp": message.context_timestamp,
            }
            for message in store.chat_history()
        ],
    }


def settings_payload(config: AppConfig) -> dict[str, Any]:
    """The Q&A configuration the exported page starts with, and the user can then edit.

    Seeded from the application's own local-model settings whichever mode it is in. A page exported
    from an installation configured against a hosted API still opens pointing at a local endpoint,
    because the alternative is a page that silently expects a key it was correctly refused.
    """
    llm = config.llm
    return {
        "version": 1,
        "llm": {
            # OpenAI-compatible, which Ollama, LM Studio, llama.cpp and vLLM all speak. The
            # exported page has no server of its own, so it calls this from the browser.
            "endpoint": llm.local.endpoint,
            "model": llm.local.model,
            "temperature": llm.generation.temperature,
            "max_output_tokens": llm.generation.max_output_tokens,
            "context_window": llm.local.context_window,
            # Present and empty on purpose: it documents the field the page will fill from its own
            # storage, without ever carrying a value out of this machine.
            "api_key": "",
        },
        "context": {
            # How much transcript to send. Characters rather than tokens: the exported page has no
            # tokeniser, and a character budget it can enforce beats a token budget it cannot.
            "max_transcript_chars": max(2000, config.context.token_budget * 3),
            "include_summaries": config.context.summary_enabled,
            "include_glossary": config.context.glossary_enabled,
        },
        "prompts": {"system": EXPORT_SYSTEM_PROMPT},
        "quick_actions": [
            {"id": action.id, "label": action.label, "prompt": action.prompt}
            for action in config.quick_actions
        ],
        "provenance": {
            "asr_model": config.asr.model,
            "asr_backend": config.asr.backend,
            "language": config.asr.language or "",
        },
    }
