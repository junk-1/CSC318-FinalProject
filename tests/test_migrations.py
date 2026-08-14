import sqlite3

from backend import config, sqlite_db


# ---- init_schema -----------------------------------------------------

def test_init_schema_creates_all_tables(sqlite_conn):
    tables = {
        r["name"]
        for r in sqlite_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"strategy_type", "bot", "bot_version", "bot_backtest"} <= tables


def test_init_schema_is_idempotent(sqlite_conn):
    sqlite_db.init_schema(sqlite_conn)  # must not raise on a second call


def test_fresh_schema_already_has_source_filename_column(sqlite_conn):
    cols = {r["name"] for r in sqlite_conn.execute("PRAGMA table_info(bot_backtest)").fetchall()}
    assert "source_filename" in cols


# ---- connect() pragmas -----------------------------------------------

def test_connect_sets_foreign_keys_pragma_on(sqlite_conn):
    assert sqlite_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_sets_wal_journal_mode(sqlite_conn):
    mode = sqlite_conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


# ---- seed_strategies ----------------------------------------------------

def test_seed_strategies_inserts_all_and_is_idempotent(sqlite_conn):
    sqlite_db.seed_strategies(sqlite_conn)
    sqlite_db.seed_strategies(sqlite_conn)  # second call must not duplicate
    count = sqlite_conn.execute("SELECT COUNT(*) FROM strategy_type").fetchone()[0]
    assert count == len(config.SEED_STRATEGIES)


# ---- _migrate() (old-shape simulation, independent of init_schema) ------

def _old_shape_connection() -> sqlite3.Connection:
    """A minimal DB mirroring a pre-source_filename install of bot_backtest,
    for testing _migrate() in isolation from the current schema.sql."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE strategy_type (
            strategy_id   INTEGER PRIMARY KEY,
            strategy_name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE bot (
            bot_id      INTEGER PRIMARY KEY,
            bot_name    TEXT NOT NULL,
            strategy_id INTEGER NOT NULL REFERENCES strategy_type(strategy_id)
        );
        CREATE TABLE bot_backtest (
            backtest_id   INTEGER PRIMARY KEY,
            bot_id        INTEGER NOT NULL REFERENCES bot(bot_id) ON DELETE CASCADE,
            doc_key       TEXT NOT NULL,
            start_period  TEXT,
            end_period    TEXT,
            backtest_note TEXT NOT NULL DEFAULT ''
        );
        """
    )
    return conn


def test_migrate_adds_missing_source_filename_column():
    conn = _old_shape_connection()
    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(bot_backtest)").fetchall()}
    assert "source_filename" not in cols_before

    sqlite_db._migrate(conn)

    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(bot_backtest)").fetchall()}
    assert "source_filename" in cols_after

    conn.execute("INSERT INTO strategy_type (strategy_name) VALUES ('s')")
    conn.execute("INSERT INTO bot (bot_name, strategy_id) VALUES ('b', 1)")
    conn.execute("INSERT INTO bot_backtest (bot_id, doc_key) VALUES (1, 'd1')")
    row = conn.execute(
        "SELECT source_filename FROM bot_backtest WHERE doc_key = 'd1'"
    ).fetchone()
    assert row["source_filename"] == ""
    conn.close()


def test_migrate_is_idempotent():
    conn = _old_shape_connection()
    sqlite_db._migrate(conn)
    sqlite_db._migrate(conn)  # must not raise (no duplicate ALTER)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(bot_backtest)").fetchall()]
    assert cols.count("source_filename") == 1
    conn.close()
