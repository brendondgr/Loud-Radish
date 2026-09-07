-- Transcript store schema (BE §8).
--
-- One SQLite file per session. Chosen because it survives a crash, gives full-text search over the
-- transcript for free, and needs no server — a 90-minute session held only in memory is
-- unrecoverable if the process dies at minute 80.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- Session-level metadata. Exactly one row, id 1.
CREATE TABLE IF NOT EXISTS session (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    session_id      TEXT    NOT NULL,
    title           TEXT    NOT NULL DEFAULT '',
    venue           TEXT    NOT NULL DEFAULT '',
    speaker         TEXT    NOT NULL DEFAULT '',
    started_at      TEXT    NOT NULL,
    ended_at        TEXT,
    session_prompt  TEXT    NOT NULL DEFAULT '',
    -- JSON snapshot of the configuration the session ran with.
    config_json     TEXT    NOT NULL DEFAULT '{}'
);

-- Committed segments. Append-only: rows are inserted and never updated, which is the storage-level
-- expression of constraint C4.
CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY,
    text        TEXT    NOT NULL,
    start       REAL    NOT NULL,
    end         REAL    NOT NULL,
    wall_clock  TEXT    NOT NULL,
    confidence  REAL,
    model_id    TEXT    NOT NULL DEFAULT '',
    speaker     TEXT,
    -- Word-level detail as JSON, for click-to-seek and low-confidence marking. Optional.
    words_json  TEXT,
    -- Which transcription pass produced this (D-022). 0 is the live one; 1 is the post-capture
    -- pass over the recorded audio. Both are kept: the live transcript is what the user watched
    -- and what any chat citation points into, so replacing it would invalidate a conversation
    -- that already happened. Defaulted so a database written before this column reads as live.
    revision    INTEGER NOT NULL DEFAULT 0
);

-- Range queries drive "summarise the last ten minutes" and the frontend's time navigation.
CREATE INDEX IF NOT EXISTS idx_segments_start ON segments (start);

-- Nearly every read filters on revision, and a session with two passes has twice the rows.
CREATE INDEX IF NOT EXISTS idx_segments_revision ON segments (revision, id);

-- Full-text search over the transcript, kept in step by triggers. `content=` makes this an external
-- content table: the text is not stored twice.
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5 (
    text,
    content='segments',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS segments_fts_insert AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts (rowid, text) VALUES (new.id, new.text);
END;

-- Present for completeness. Segments are append-only, so in practice neither fires.
CREATE TRIGGER IF NOT EXISTS segments_fts_delete AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts (segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS segments_fts_update AFTER UPDATE ON segments BEGIN
    INSERT INTO segments_fts (segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO segments_fts (rowid, text) VALUES (new.id, new.text);
END;

-- Rolling summaries, forming a running outline of the talk (BE §9.2).
CREATE TABLE IF NOT EXISTS summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    start       REAL    NOT NULL,
    end         REAL    NOT NULL,
    text        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

-- Minute-by-minute polished blocks: the transcript rewritten for reading (D-018).
--
-- Additive, never destructive. The segments a block covers stay exactly as they were written, so a
-- bad rewrite costs one redundant row rather than a corrupted record, and export still emits the
-- verbatim text.
CREATE TABLE IF NOT EXISTS polished_blocks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    start           REAL    NOT NULL,
    end             REAL    NOT NULL,
    text            TEXT    NOT NULL,
    -- JSON array of the segment ids this block was built from.
    source_ids_json TEXT    NOT NULL DEFAULT '[]',
    created_at      TEXT    NOT NULL
);

-- Glossary terms, with the timestamp of first use (BE §9.3).
CREATE TABLE IF NOT EXISTS glossary (
    term        TEXT PRIMARY KEY,
    definition  TEXT NOT NULL,
    first_seen  REAL NOT NULL,
    created_at  TEXT NOT NULL
);

-- The user's conversation with the assistant, so it survives a browser reload.
CREATE TABLE IF NOT EXISTS chat_messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    role                TEXT    NOT NULL,
    text                TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    -- Transcript position the answer was based on, so staleness is visible (BE §11.4).
    context_timestamp   REAL,
    -- JSON: cited timestamps, quoted selection, which context tiers were used.
    meta_json           TEXT
);

-- Where a transcription pass over a finished recording has got to (D-045).
--
-- D-021 said deliberately that a pass is *not* persisted: a server restart loses the job, keeps
-- the recording, and the next start lists it as untranscribed with a button to run it again. That
-- reasoning was about crash recovery and it does not survive a user who pressed pause on purpose:
-- re-running a forty-minute recording costs twenty-seven minutes of CPU to recover work that had
-- already been done.
--
-- Here rather than in a file beside the audio, for two reasons. D-037's report asked for *fewer*
-- files in a recording folder; and a checkpoint that travels with the transcript it is a
-- checkpoint of cannot be separated from it by a move or a partial copy.
--
-- One row per (revision), because one pass produces one revision (D-022): the live pass is 0 and
-- the post-capture pass is 1, and a recording never has two passes at the same revision.
CREATE TABLE IF NOT EXISTS transcription_passes (
    revision            INTEGER PRIMARY KEY,
    -- The audio being transcribed. Named so a resume can find it after a restart, and because on
    -- a failure it is the only remaining copy of what was said.
    source_path         TEXT    NOT NULL,
    total_seconds       REAL    NOT NULL,
    -- running | paused | done | cancelled | failed
    state               TEXT    NOT NULL,
    -- Where the next window starts. Windows are atomic, so this is always a window boundary and a
    -- resume loses at most one window of work.
    next_start_s        REAL    NOT NULL DEFAULT 0,
    last_segment_id     INTEGER NOT NULL DEFAULT 0,
    -- The biasing prompt the pass was started with, so a resume produces comparable text.
    prompt              TEXT    NOT NULL DEFAULT '',
    updated_at          TEXT    NOT NULL
);
