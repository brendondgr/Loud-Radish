"""Pipeline services — where the real work happens.

Each sub-package owns one stage of the architecture in
``docs/plans/live-seminar-transcriber.md``:

* ``audio``      — capture, canonical format, ring buffer, preprocessing (BE §4)
* ``vad``        — voice activity detection and pause events (BE §5)
* ``asr``        — the pluggable speech-model interface, Seam A (BE §6)
* ``streaming``  — buffering, LocalAgreement commit policy, trimming, guards (BE §7)
* ``transcript`` — the append-only store, search, and export (BE §8, §17)
* ``context``    — rolling summaries, glossary, chunk index (BE §9)
* ``llm``        — the pluggable language-model interface, Seam B (BE §10)
* ``chat``       — context assembly and chat orchestration (BE §11)
* ``session``    — worker wiring, queues, backpressure, metrics (BE §14, §16)

Nothing here knows about HTTP. Routes call services; services never import routes.
"""
