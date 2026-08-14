"""The transcript store and its export formats (BE §8, §17).

* ``store``  — the append-only SQLite session file, with FTS5 search
* ``export`` — plain text, Markdown, SRT, VTT, and JSON

The schema lives alongside in ``schema.sql`` rather than as strings in Python, so it can be read as
one document.
"""

from .export import FORMAT_INFO, ExportFormat, export, format_timestamp, to_markdown, to_text
from .store import TranscriptStore

__all__ = [
    "FORMAT_INFO",
    "ExportFormat",
    "TranscriptStore",
    "export",
    "format_timestamp",
    "to_markdown",
    "to_text",
]
