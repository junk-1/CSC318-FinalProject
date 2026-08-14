"""Tests for BotRepository.delete_bot.

Deletion is the riskiest operation in the codebase: it has to remove SQL
rows (via cascade), garbage-collect LMDB blobs, and get both right at once
without either leaking storage or -- worse -- deleting a blob that a
*different* bot still depends on, since code storage is content-addressed
and hashes can legitimately be shared across bots.
"""

import pytest

from backend.exceptions import NotFoundError


def test_delete_bot_removes_bot_row(repo, sqlite_conn, make_bot):
    bot = make_bot()
    repo.delete_bot(bot["bot_id"])
    row = sqlite_conn.execute("SELECT 1 FROM bot WHERE bot_id = ?", (bot["bot_id"],)).fetchone()
    assert row is None


def test_delete_bot_cascades_bot_version_and_bot_backtest_rows(repo, sqlite_conn, make_bot):
    # Verified via a direct SQL COUNT, not repo.get_versions()/list_backtests(),
    # so this actually proves the ON DELETE CASCADE fired rather than just
    # that the repository's own read methods handle a missing bot gracefully.
    bot = make_bot()
    repo.create_backtest(bot["bot_id"], b"doc bytes", "report.txt")

    repo.delete_bot(bot["bot_id"])

    v_count = sqlite_conn.execute(
        "SELECT COUNT(*) FROM bot_version WHERE bot_id = ?", (bot["bot_id"],)
    ).fetchone()[0]
    bt_count = sqlite_conn.execute(
        "SELECT COUNT(*) FROM bot_backtest WHERE bot_id = ?", (bot["bot_id"],)
    ).fetchone()[0]
    assert v_count == 0
    assert bt_count == 0


def test_delete_bot_missing_bot_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.delete_bot(99999)


def test_delete_bot_gcs_code_blob_when_unreferenced(repo, lmdb_store, make_bot):
    # The common case: nothing else points at this hash, so the blob should
    # actually be removed from LMDB, not just orphaned.
    bot = make_bot(data=b"unique content")
    code_hash = repo.get_versions(bot["bot_id"])[0]["code_hash"]

    repo.delete_bot(bot["bot_id"])

    assert lmdb_store.has_code(code_hash) is False


def test_delete_bot_keeps_code_blob_when_shared_by_another_bot(
    repo, lmdb_store, strategy_id, write_file
):
    # The sharpest test in this file: two DIFFERENT bots created from
    # byte-identical file content share one LMDB blob (content-addressed
    # storage). Deleting one bot must NOT pull that blob out from under the
    # sibling bot that still references the same hash -- naive unconditional
    # deletion here would silently corrupt bot2's stored code.
    shared_data = b"shared code"
    bot1 = repo.create_bot(write_file(shared_data, name="bot1.py"), "Bot1", strategy_id)
    bot2 = repo.create_bot(write_file(shared_data, name="bot2.py"), "Bot2", strategy_id)
    code_hash = repo.get_versions(bot1["bot_id"])[0]["code_hash"]

    repo.delete_bot(bot1["bot_id"])

    assert lmdb_store.has_code(code_hash) is True
    bot2_version_id = repo.get_versions(bot2["bot_id"])[0]["version_id"]
    data, _ = repo.get_code(bot2_version_id)
    assert data == shared_data


def test_delete_bot_gcs_all_versions_hashes_not_just_head(repo, lmdb_store, make_bot, write_file):
    # A bot with multiple versions has multiple distinct code_hash values in
    # its history -- delete_bot must GC every one of them, not just the
    # current head's.
    bot = make_bot(data=b"v1")
    repo.add_version(bot["bot_id"], write_file(b"v2", name="v2.py"))
    hashes = [v["code_hash"] for v in repo.get_versions(bot["bot_id"])]
    assert len(hashes) == 2

    repo.delete_bot(bot["bot_id"])

    for h in hashes:
        assert lmdb_store.has_code(h) is False


def test_delete_bot_deletes_all_backtest_docs_unconditionally(repo, lmdb_store, make_bot):
    # Unlike code blobs, backtest doc_keys are fresh uuid4s and never
    # shared between rows, so there's no reference-counting needed here --
    # every backtest doc belonging to the deleted bot is simply removed.
    bot = make_bot()
    bt = repo.create_backtest(bot["bot_id"], b"doc", "r.txt")
    assert lmdb_store.get_backtest_doc(bt["doc_key"]) is not None

    repo.delete_bot(bot["bot_id"])

    assert lmdb_store.get_backtest_doc(bt["doc_key"]) is None


def test_cascade_requires_foreign_keys_pragma_on(repo, sqlite_conn, make_bot):
    # Regression guard: SQLite's ON DELETE CASCADE is a silent no-op if
    # PRAGMA foreign_keys isn't set to ON for the connection -- this pins
    # down that connect() actually enables it, so the cascade test above
    # can't start silently passing for the wrong reason if that pragma is
    # ever accidentally dropped.
    assert sqlite_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    bot = make_bot()
    repo.delete_bot(bot["bot_id"])

    v_count = sqlite_conn.execute(
        "SELECT COUNT(*) FROM bot_version WHERE bot_id = ?", (bot["bot_id"],)
    ).fetchone()[0]
    assert v_count == 0
