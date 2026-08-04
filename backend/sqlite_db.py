"""SQLite connection factory and schema/seed bootstrapping.

Kept deliberately thin: this module only knows how to open a correctly
configured connection and get the schema in place. All bot-domain queries
live in backend/repository.py.
"""

import sqlite3

from . import config


def connect() -> sqlite3.Connection:
    """Open (creating if needed) the BotVault SQLite database.

    isolation_level=None puts the connection in true autocommit mode so the
    repository layer's explicit BEGIN IMMEDIATE / COMMIT calls are exactly
    the transaction boundaries that run, rather than depending on sqlite3's
    implicit-transaction guessing.
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.SQLITE_PATH), isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Both PRAGMAs are per-connection, not persisted in the file -- must be
    # reissued every time a connection is opened. foreign_keys=ON is what
    # makes ON DELETE CASCADE actually fire (silently a no-op otherwise).
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    # Create the schema (if missing) then apply any additive migrations.
    # executescript() runs every CREATE TABLE/INDEX IF NOT EXISTS in one
    # shot -- a no-op after the first run, since the tables already exist.
    sql = config.SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive-only migrations for databases created by an
    earlier version of schema.sql. `CREATE TABLE IF NOT EXISTS` doesn't
    retrofit new columns onto a table that already exists, so any column
    added to bot_backtest/bot_version/etc. after the initial release needs
    an explicit ALTER TABLE here as well as in schema.sql (for fresh
    installs)."""
    # PRAGMA table_info() lists the actual columns currently on disk, which
    # is how we detect "this DB predates the source_filename column" without
    # needing a separate schema-version table.
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(bot_backtest)").fetchall()}
    if "source_filename" not in cols:
        conn.execute(
            "ALTER TABLE bot_backtest ADD COLUMN source_filename TEXT NOT NULL DEFAULT ''"
        )


def seed_strategies(conn: sqlite3.Connection) -> None:
    # INSERT OR IGNORE relies on strategy_type.strategy_name's UNIQUE
    # constraint -- running this on every startup is safe and cheap; after
    # the first run every insert here just silently no-ops.
    for strat in config.SEED_STRATEGIES:
        conn.execute(
            "INSERT OR IGNORE INTO strategy_type "
            "(strategy_name, market_type, strategy_description) VALUES (?, ?, ?)",
            (strat["strategy_name"], strat["market_type"], strat["strategy_description"]),
        )
