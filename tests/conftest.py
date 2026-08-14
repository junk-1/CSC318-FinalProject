"""Shared pytest fixtures for the BotVault backend test suite.

Isolation strategy: SQLite is isolated by monkeypatching backend.config's
DATA_DIR/SQLITE_PATH into a tmp_path before backend.sqlite_db.connect() is
called (the approach STATE.md itself recommends). LMDB is isolated by
passing an explicit path= to CodeStore -- NOT by monkeypatching
config.LMDB_DIR, because CodeStore.__init__'s default arg value is bound
once at import time, so patching the config attribute afterward would
silently have no effect.
"""

import pytest

from backend import config, sqlite_db
from backend.lmdb_store import CodeStore
from backend.repository import BotRepository


@pytest.fixture
def patched_config(tmp_path, monkeypatch):
    """Redirects config.DATA_DIR/SQLITE_PATH into tmp_path, before any test
    calls sqlite_db.connect() -- this is the seam that keeps every test's
    database file-isolated and off the real %LOCALAPPDATA%\\BotVault\\ path."""
    data_dir = tmp_path / "appdata"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "SQLITE_PATH", data_dir / "botvault.sqlite3")
    return data_dir


@pytest.fixture
def sqlite_conn(patched_config):
    """A real connection (schema applied, no seeding) against the isolated
    temp database. Closed on teardown -- required before pytest cleans up
    tmp_path, otherwise Windows leaves the file handle locked."""
    conn = sqlite_db.connect()
    sqlite_db.init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def lmdb_store(tmp_path):
    """A real CodeStore in its own temp directory, independent of tmp_path
    used by sqlite_conn -- LMDB isolation is handled by the explicit path=
    argument, never by monkeypatching config.LMDB_DIR (see module
    docstring: that default is bound at import time and patching it later
    would silently do nothing)."""
    store = CodeStore(path=tmp_path / "lmdb")
    yield store
    store.close()


@pytest.fixture
def repo(sqlite_conn, lmdb_store):
    """Bare BotRepository -- no seeded strategies, so tests that need one
    ask for the `strategy_id` or `seeded_repo` fixture explicitly."""
    return BotRepository(sqlite_conn, lmdb_store)


@pytest.fixture
def strategy_id(sqlite_conn):
    """One deterministic strategy row, inserted directly via raw SQL --
    deliberately bypasses repo.create_strategy() so that tests for every
    OTHER method don't implicitly depend on create_strategy's own
    correctness (test_repository_strategies.py is the only file that
    exercises create_strategy itself)."""
    cur = sqlite_conn.execute(
        "INSERT INTO strategy_type (strategy_name, market_type, strategy_description) "
        "VALUES ('test-strategy', '', '')"
    )
    return cur.lastrowid


@pytest.fixture
def seeded_repo(repo, sqlite_conn):
    """Same as `repo`, but with the app's default seed strategies applied --
    for tests specifically about that seed list (see test_migrations.py's
    seed_strategies tests for the seeding logic itself)."""
    sqlite_db.seed_strategies(sqlite_conn)
    return repo


@pytest.fixture
def write_file(tmp_path):
    """Factory: write_file(data, name=None) -> path string. create_bot()/
    add_version() take a file_path, not raw bytes, so every test exercising
    them needs a real file on disk first. Auto-generates a unique filename
    when none is given, since a test calling this more than once in the
    same tmp_path would otherwise silently overwrite the first file."""
    counter = {"n": 0}

    def _write(data: bytes, name: str | None = None) -> str:
        if name is None:
            counter["n"] += 1
            name = f"file{counter['n']}.py"
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)

    return _write


@pytest.fixture
def make_bot(repo, strategy_id, write_file):
    """Factory: make_bot(name="Bot", data=b"...", sid=None) -> the dict
    create_bot() returns. Wraps write_file()+create_bot() together to cut
    that two-step boilerplate out of every test that just needs "a bot"
    and doesn't care about the specifics of how it got created."""
    def _make(name: str = "Bot", data: bytes = b"print('bot')", sid: int | None = None):
        path = write_file(data, name=f"{name}.py")
        return repo.create_bot(path, name, sid if sid is not None else strategy_id)

    return _make
