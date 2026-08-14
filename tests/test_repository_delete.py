import pytest

from backend.exceptions import NotFoundError


def test_delete_bot_removes_bot_row(repo, sqlite_conn, make_bot):
    bot = make_bot()
    repo.delete_bot(bot["bot_id"])
    row = sqlite_conn.execute("SELECT 1 FROM bot WHERE bot_id = ?", (bot["bot_id"],)).fetchone()
    assert row is None


def test_delete_bot_cascades_bot_version_and_bot_backtest_rows(repo, sqlite_conn, make_bot):
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
    bot = make_bot(data=b"unique content")
    code_hash = repo.get_versions(bot["bot_id"])[0]["code_hash"]

    repo.delete_bot(bot["bot_id"])

    assert lmdb_store.has_code(code_hash) is False


def test_delete_bot_keeps_code_blob_when_shared_by_another_bot(
    repo, lmdb_store, strategy_id, write_file
):
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
    bot = make_bot(data=b"v1")
    repo.add_version(bot["bot_id"], write_file(b"v2", name="v2.py"))
    hashes = [v["code_hash"] for v in repo.get_versions(bot["bot_id"])]
    assert len(hashes) == 2

    repo.delete_bot(bot["bot_id"])

    for h in hashes:
        assert lmdb_store.has_code(h) is False


def test_delete_bot_deletes_all_backtest_docs_unconditionally(repo, lmdb_store, make_bot):
    bot = make_bot()
    bt = repo.create_backtest(bot["bot_id"], b"doc", "r.txt")
    assert lmdb_store.get_backtest_doc(bt["doc_key"]) is not None

    repo.delete_bot(bot["bot_id"])

    assert lmdb_store.get_backtest_doc(bt["doc_key"]) is None


def test_cascade_requires_foreign_keys_pragma_on(repo, sqlite_conn, make_bot):
    # Regression guard: cascading delete silently does nothing if this
    # pragma is ever accidentally dropped from sqlite_db.connect().
    assert sqlite_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    bot = make_bot()
    repo.delete_bot(bot["bot_id"])

    v_count = sqlite_conn.execute(
        "SELECT COUNT(*) FROM bot_version WHERE bot_id = ?", (bot["bot_id"],)
    ).fetchone()[0]
    assert v_count == 0
