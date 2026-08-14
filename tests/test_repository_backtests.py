import pytest

from backend.exceptions import NotFoundError


def test_create_backtest_success(repo, make_bot):
    bot = make_bot()
    bt = repo.create_backtest(
        bot["bot_id"], b"report bytes", "report.csv",
        start_period="2024-01-01", end_period="2024-06-01", note="good run",
    )
    assert bt["source_filename"] == "report.csv"
    assert bt["start_period"] == "2024-01-01"
    assert bt["end_period"] == "2024-06-01"
    assert bt["backtest_note"] == "good run"


def test_create_backtest_missing_bot_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.create_backtest(99999, b"data", "f.csv")


def test_list_backtests_ordered_newest_first(repo, sqlite_conn, make_bot):
    bot = make_bot()
    bt1 = repo.create_backtest(bot["bot_id"], b"a", "a.csv")
    bt2 = repo.create_backtest(bot["bot_id"], b"b", "b.csv")

    # Force distinct, known-ordered timestamps -- two inserts in the same
    # test can land on the same fractional-second tick and make ordering
    # by wall-clock date_created flaky otherwise.
    sqlite_conn.execute(
        "UPDATE bot_backtest SET date_created = '2024-01-01T00:00:00.000Z' WHERE backtest_id = ?",
        (bt1["backtest_id"],),
    )
    sqlite_conn.execute(
        "UPDATE bot_backtest SET date_created = '2024-06-01T00:00:00.000Z' WHERE backtest_id = ?",
        (bt2["backtest_id"],),
    )

    results = repo.list_backtests(bot["bot_id"])
    assert [r["backtest_id"] for r in results] == [bt2["backtest_id"], bt1["backtest_id"]]


def test_list_backtests_empty_for_bot_with_none(repo, make_bot):
    bot = make_bot()
    assert repo.list_backtests(bot["bot_id"]) == []


def test_get_backtest_doc_roundtrip(repo, make_bot):
    bot = make_bot()
    bt = repo.create_backtest(bot["bot_id"], b"doc content", "report.csv")

    data, filename = repo.get_backtest_doc(bt["backtest_id"])
    assert data == b"doc content"
    assert filename == "report.csv"


def test_get_backtest_doc_missing_backtest_id_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.get_backtest_doc(99999)


def test_get_backtest_doc_missing_blob_raises_not_found_not_integrity_error(
    repo, lmdb_store, make_bot
):
    # Contrast with get_code(): backtest docs are never hash-reverified, so
    # a missing blob is just "not found", not treated as a corruption signal.
    bot = make_bot()
    bt = repo.create_backtest(bot["bot_id"], b"doc", "r.csv")
    lmdb_store.delete_backtest_doc(bt["doc_key"])

    with pytest.raises(NotFoundError):
        repo.get_backtest_doc(bt["backtest_id"])


def test_delete_backtest_removes_row_and_blob(repo, sqlite_conn, lmdb_store, make_bot):
    bot = make_bot()
    bt = repo.create_backtest(bot["bot_id"], b"doc", "r.csv")

    repo.delete_backtest(bt["backtest_id"])

    row = sqlite_conn.execute(
        "SELECT 1 FROM bot_backtest WHERE backtest_id = ?", (bt["backtest_id"],)
    ).fetchone()
    assert row is None
    assert lmdb_store.get_backtest_doc(bt["doc_key"]) is None


def test_delete_backtest_missing_id_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.delete_backtest(99999)


def test_backtest_docs_not_deduped_across_identical_content(repo, make_bot):
    bot = make_bot()
    bt1 = repo.create_backtest(bot["bot_id"], b"same bytes", "a.csv")
    bt2 = repo.create_backtest(bot["bot_id"], b"same bytes", "b.csv")

    assert bt1["doc_key"] != bt2["doc_key"]
