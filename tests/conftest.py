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
    data_dir = tmp_path / "appdata"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "SQLITE_PATH", data_dir / "botvault.sqlite3")
    return data_dir


@pytest.fixture
def sqlite_conn(patched_config):
    conn = sqlite_db.connect()
    sqlite_db.init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def lmdb_store(tmp_path):
    store = CodeStore(path=tmp_path / "lmdb")
    yield store
    store.close()


@pytest.fixture
def repo(sqlite_conn, lmdb_store):
    return BotRepository(sqlite_conn, lmdb_store)


@pytest.fixture
def strategy_id(sqlite_conn):
    cur = sqlite_conn.execute(
        "INSERT INTO strategy_type (strategy_name, market_type, strategy_description) "
        "VALUES ('test-strategy', '', '')"
    )
    return cur.lastrowid


@pytest.fixture
def seeded_repo(repo, sqlite_conn):
    sqlite_db.seed_strategies(sqlite_conn)
    return repo


@pytest.fixture
def write_file(tmp_path):
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
    def _make(name: str = "Bot", data: bytes = b"print('bot')", sid: int | None = None):
        path = write_file(data, name=f"{name}.py")
        return repo.create_bot(path, name, sid if sid is not None else strategy_id)

    return _make
