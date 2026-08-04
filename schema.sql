-- BotVault SQLite schema.
--
-- Derived from the project ERD (strategy_type, bot, bot_version,
-- bot_backtest) with two deliberate deviations, both confirmed with the
-- project owner:
--   * bot_performance is omitted entirely -- performance data now lives
--     inside backtest documents in LMDB, not a relational table.
--   * bot_version.status_tag was originally a BOOLEAN ("is this version
--     running"). It is repurposed here into the GUI's 3-way status enum
--     (in development / finished / shelved); bot_version.version_note is
--     exactly what the GUI's NOTES box edits. Both are scoped to a bot's
--     current head version (MAX(version_number) per bot_id).
--
-- PRAGMA foreign_keys=ON and PRAGMA journal_mode=WAL are set per-connection
-- in backend/sqlite_db.py, not here (PRAGMAs are not persisted in the file).

CREATE TABLE IF NOT EXISTS strategy_type (
    strategy_id          INTEGER PRIMARY KEY,
    strategy_name        TEXT NOT NULL UNIQUE,
    market_type          TEXT,
    strategy_description TEXT
);

CREATE TABLE IF NOT EXISTS bot (
    bot_id       INTEGER PRIMARY KEY,
    bot_name     TEXT NOT NULL CHECK (length(trim(bot_name)) > 0),
    strategy_id  INTEGER NOT NULL REFERENCES strategy_type(strategy_id),
    date_created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS bot_version (
    version_id        INTEGER PRIMARY KEY,
    bot_id            INTEGER NOT NULL REFERENCES bot(bot_id) ON DELETE CASCADE,
    version_number    INTEGER NOT NULL CHECK (version_number > 0),
    parent_version_id INTEGER REFERENCES bot_version(version_id),
    status_tag        TEXT NOT NULL CHECK (status_tag IN ('in development', 'finished', 'shelved')),
    code_key          TEXT NOT NULL,      -- LMDB key in the "code" sub-db (== code_hash today)
    code_hash         TEXT NOT NULL,      -- sha256 hex digest, re-verified on every read
    source_filename   TEXT NOT NULL,      -- original filename, restores the .py/.cs extension on export
    version_note      TEXT NOT NULL DEFAULT '',
    date_created      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (bot_id, version_number),
    CHECK (parent_version_id IS NULL OR parent_version_id <> version_id)
);

CREATE TABLE IF NOT EXISTS bot_backtest (
    backtest_id     INTEGER PRIMARY KEY,
    bot_id          INTEGER NOT NULL REFERENCES bot(bot_id) ON DELETE CASCADE,
    doc_key         TEXT NOT NULL,           -- fresh uuid4 per row in the "backtest_docs" sub-db, NOT content-addressed
    source_filename TEXT NOT NULL DEFAULT '', -- restores the original extension on export
    start_period    TEXT,
    end_period      TEXT,
    backtest_note   TEXT NOT NULL DEFAULT '',
    date_created    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_bot_bot_name               ON bot(bot_name);
CREATE INDEX IF NOT EXISTS idx_bot_strategy_id             ON bot(strategy_id);
CREATE INDEX IF NOT EXISTS idx_bot_version_bot_id_verdesc  ON bot_version(bot_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_bot_version_code_hash       ON bot_version(code_hash);
CREATE INDEX IF NOT EXISTS idx_bot_backtest_bot_id         ON bot_backtest(bot_id);
