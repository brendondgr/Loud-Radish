"""Context assembly, rolling summaries, and glossary extraction.

Everything here answers one question: given a talk that will not fit in any context window, which
part of it should the model see for *this* question?
"""

from .assembler import (
    AssembledContext,
    ContextRequest,
    TranscriptSource,
    assemble,
    effective_budget,
    timestamp,
)
from .worker import ContextWorker, parse_terms

__all__ = [
    "AssembledContext",
    "ContextRequest",
    "ContextWorker",
    "TranscriptSource",
    "assemble",
    "effective_budget",
    "parse_terms",
    "timestamp",
]
