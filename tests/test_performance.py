"""Lightweight, generously-thresholded performance regression checks.

These are canaries, not SLAs: thresholds are set well above what a normal
dev machine should take, so they only fire on a real regression (e.g. an
accidental N+1 query, an O(n^2) rewrite, or a per-file synchronous flush
loop), not on ordinary machine-to-machine variance.

Run just these with `pytest -m performance`; exclude them with
`pytest -m "not performance"`.
"""

import os
import random
import time
import uuid
import zipfile

import pytest

from backend.hashing import sha256_bytes
from backend.lmdb_store import CodeStore

pytestmark = pytest.mark.performance


def _bulk_insert_bots(sqlite_conn, strategy_id, count: int):
    """Populate `count` bots (+ a head bot_version each, some with a second
    version) via direct executemany -- bypasses BotRepository.create_bot's
    per-row transaction/LMDB overhead so setup cost doesn't pollute the
    timed measurement."""
    sqlite_conn.execute("BEGIN")
    sqlite_conn.executemany(
        "INSERT INTO bot (bot_name, strategy_id) VALUES (?, ?)",
        [(f"PerfBot{i}", strategy_id) for i in range(count)],
    )
    bot_ids = [r["bot_id"] for r in sqlite_conn.execute("SELECT bot_id FROM bot").fetchall()]

    versions = []
    for idx, bot_id in enumerate(bot_ids):
        h1 = f"hash-{idx}-v1"
        versions.append((bot_id, 1, None, "in development", h1, h1, "bot.py", ""))
        if idx % 3 == 0:
            h2 = f"hash-{idx}-v2"
            versions.append((bot_id, 2, None, "in development", h2, h2, "bot.py", ""))
    sqlite_conn.executemany(
        """INSERT INTO bot_version
           (bot_id, version_number, parent_version_id, status_tag,
            code_key, code_hash, source_filename, version_note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        versions,
    )
    sqlite_conn.execute("COMMIT")
    return bot_ids


def test_search_bots_performance_at_scale(repo, sqlite_conn, strategy_id):
    # search_bots() is a single window-function query over bot_version --
    # this is the test most likely to catch an accidental N+1 (e.g. a
    # future refactor that queries per-bot in a loop instead of one JOIN).
    _bulk_insert_bots(sqlite_conn, strategy_id, 1500)

    def timed(call, repeats=5):
        # min() of several repeats, not mean/median -- the standard
        # anti-flake trick for wall-clock assertions: OS scheduling noise
        # only ever adds time, so the fastest run is the truest measurement.
        return min(_time_once(call) for _ in range(repeats))

    default_elapsed = timed(lambda: repo.search_bots())
    assert default_elapsed < 0.5, f"search_bots() took {default_elapsed:.3f}s over 1500 bots"

    filtered_elapsed = timed(
        lambda: repo.search_bots(query="PerfBot", sort_key="version", sort_dir="desc")
    )
    assert filtered_elapsed < 0.5, (
        f"filtered/sorted search_bots() took {filtered_elapsed:.3f}s over 1500 bots"
    )


def _time_once(call) -> float:
    start = time.perf_counter()
    call()
    return time.perf_counter() - start


def test_lmdb_put_throughput(tmp_path):
    store = CodeStore(path=tmp_path / "lmdb_put_perf")
    try:
        blobs = [(f"key-{i}", i.to_bytes(4, "big") + os.urandom(2044)) for i in range(2000)]

        start = time.perf_counter()
        for key, data in blobs:
            store.put_code(key, data)
        elapsed = time.perf_counter() - start

        assert elapsed < 8.0, f"2000 sequential put_code() calls took {elapsed:.3f}s"
    finally:
        store.close()


def test_lmdb_get_throughput(tmp_path):
    store = CodeStore(path=tmp_path / "lmdb_get_perf")
    try:
        keys = [f"key-{i}" for i in range(2000)]
        for key in keys:
            store.put_code(key, os.urandom(2048))  # setup, not timed

        shuffled = keys[:]
        random.shuffle(shuffled)

        start = time.perf_counter()
        for key in shuffled:
            store.get_code(key)
        elapsed = time.perf_counter() - start

        assert elapsed < 2.0, f"2000 get_code() calls took {elapsed:.3f}s"
    finally:
        store.close()


def test_export_vault_performance_at_scale(repo, sqlite_conn, lmdb_store, strategy_id, tmp_path):
    # 50 real code blobs, reused across 300 bots (realistic content-addressed
    # reuse) -- keeps LMDB setup cheap while still exercising env.copy().
    code_hashes = []
    for _ in range(50):
        data = os.urandom(2048)
        h = sha256_bytes(data)
        lmdb_store.put_code(h, data)
        code_hashes.append(h)

    sqlite_conn.execute("BEGIN")
    sqlite_conn.executemany(
        "INSERT INTO bot (bot_name, strategy_id) VALUES (?, ?)",
        [(f"ExportBot{i}", strategy_id) for i in range(300)],
    )
    bot_ids = [r["bot_id"] for r in sqlite_conn.execute("SELECT bot_id FROM bot").fetchall()]
    sqlite_conn.executemany(
        """INSERT INTO bot_version
           (bot_id, version_number, parent_version_id, status_tag,
            code_key, code_hash, source_filename, version_note)
           VALUES (?, 1, NULL, 'in development', ?, ?, 'bot.py', '')""",
        [(bid, code_hashes[i % len(code_hashes)], code_hashes[i % len(code_hashes)])
         for i, bid in enumerate(bot_ids)],
    )
    sqlite_conn.execute("COMMIT")

    # 200 backtest docs (~20KB each, unique -- not deduped) attached to bots.
    sqlite_conn.execute("BEGIN")
    bt_rows = []
    for i in range(200):
        key = uuid.uuid4().hex
        lmdb_store.put_backtest_doc(key, os.urandom(20 * 1024))
        bt_rows.append((bot_ids[i % len(bot_ids)], key, "report.csv", "", "", ""))
    sqlite_conn.executemany(
        """INSERT INTO bot_backtest
           (bot_id, doc_key, source_filename, start_period, end_period, backtest_note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        bt_rows,
    )
    sqlite_conn.execute("COMMIT")

    dest_zip = str(tmp_path / "perf_vault.zip")
    start = time.perf_counter()
    repo.export_vault(dest_zip)
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0, f"export_vault() over 300 bots + 200 backtests took {elapsed:.3f}s"
    assert os.path.getsize(dest_zip) > 0
    with zipfile.ZipFile(dest_zip) as zf:
        names = zf.namelist()
        assert "botvault.sqlite3" in names
        assert any(n.startswith("botvault_lmdb/") for n in names)
