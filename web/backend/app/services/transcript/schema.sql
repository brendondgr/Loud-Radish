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
    words_json  TEXT
);

-- Range queries drive "summarise the last ten minutes" and the frontend's time navigation.
CREATE INDEX IF NOT EXISTS idx_segments_start ON segments (start);

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
