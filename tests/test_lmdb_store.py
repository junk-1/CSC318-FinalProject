"""Tests for CodeStore -- the raw LMDB wrapper -- independent of
BotRepository. These exercise the storage layer's own contract (put/get/
delete semantics, dedup-by-overwrite=False, sub-db isolation, backup, and
capacity errors) so repository-level tests can assume it behaves correctly
rather than re-proving it.
"""

import pytest

from backend import config
from backend.exceptions import StorageFullError
from backend.lmdb_store import CodeStore


# ---- code sub-db ---------------------------------------------------------

def test_put_and_get_code_roundtrip(lmdb_store):
    lmdb_store.put_code("k1", b"hello world")
    assert lmdb_store.get_code("k1") == b"hello world"


def test_get_code_missing_key_returns_none(lmdb_store):
    # get_code() returns None for a missing key rather than raising -- it's
    # BotRepository.get_code() that turns a None here into IntegrityError.
    assert lmdb_store.get_code("does-not-exist") is None


def test_put_code_same_key_same_data_is_silent_noop(lmdb_store):
    # This is what makes content-addressed dedup free: re-storing the exact
    # same content under its own hash key just does nothing, silently.
    lmdb_store.put_code("k1", b"data")
    lmdb_store.put_code("k1", b"data")
    assert lmdb_store.get_code("k1") == b"data"


def test_put_code_overwrite_false_ignores_new_data_under_existing_key(lmdb_store):
    # Pins down put()'s overwrite=False semantics precisely: if a key is
    # reused with DIFFERENT bytes (shouldn't happen in practice since keys
    # are content hashes, but worth verifying), the ORIGINAL bytes win --
    # the write is silently dropped, not merged or erroring.
    lmdb_store.put_code("k1", b"original")
    lmdb_store.put_code("k1", b"different bytes entirely")
    assert lmdb_store.get_code("k1") == b"original"


def test_delete_code_removes_blob(lmdb_store):
    lmdb_store.put_code("k1", b"data")
    lmdb_store.delete_code("k1")
    assert lmdb_store.get_code("k1") is None


def test_delete_code_missing_key_is_noop(lmdb_store):
    # Callers (e.g. delete_bot's GC loop) don't have to has_code() first.
    lmdb_store.delete_code("never-existed")  # must not raise


def test_has_code_true_and_false(lmdb_store):
    # Cheap existence check used by dedup logic elsewhere.
    assert lmdb_store.has_code("k1") is False
    lmdb_store.put_code("k1", b"data")
    assert lmdb_store.has_code("k1") is True


# ---- backtest_docs sub-db ------------------------------------------------

def test_backtest_doc_put_get_roundtrip(lmdb_store):
    # Same put/get/delete shape as the code sub-db above, just against the
    # separate backtest_docs sub-db (see independence test below).
    lmdb_store.put_backtest_doc("doc1", b"backtest report bytes")
    assert lmdb_store.get_backtest_doc("doc1") == b"backtest report bytes"


def test_delete_backtest_doc_missing_key_is_noop(lmdb_store):
    lmdb_store.delete_backtest_doc("never-existed")  # must not raise


# ---- sub-db independence -------------------------------------------------

def test_code_and_backtest_subdbs_are_independent(lmdb_store):
    # Uses the SAME key string in both sub-dbs to prove they never collide
    # even if a code hash and a backtest uuid happened to be identical text.
    lmdb_store.put_code("shared-key", b"code bytes")
    lmdb_store.put_backtest_doc("shared-key", b"doc bytes")

    assert lmdb_store.get_code("shared-key") == b"code bytes"
    assert lmdb_store.get_backtest_doc("shared-key") == b"doc bytes"

    lmdb_store.delete_code("shared-key")
    assert lmdb_store.get_code("shared-key") is None
    assert lmdb_store.get_backtest_doc("shared-key") == b"doc bytes"


# ---- hot backup ------------------------------------------------------

def test_copy_to_produces_independently_readable_backup(lmdb_store, tmp_path):
    # copy_to() backs this export_vault: opening a brand-new CodeStore
    # against the copied directory proves it's a real, independent,
    # openable environment -- not just a file that happens to exist.
    lmdb_store.put_code("k1", b"code data")
    lmdb_store.put_backtest_doc("d1", b"doc data")

    backup_dir = tmp_path / "lmdb_backup"
    backup_dir.mkdir()
    lmdb_store.copy_to(str(backup_dir))

    restored = CodeStore(path=backup_dir)
    try:
        assert restored.get_code("k1") == b"code data"
        assert restored.get_backtest_doc("d1") == b"doc data"
    finally:
        restored.close()


# ---- storage exhaustion ---------------------------------------------------

def test_put_code_raises_storagefullerror_when_map_exhausted(tmp_path, monkeypatch):
    # Shrinks map_size to something writes can actually exhaust in a test,
    # then confirms the raw lmdb.MapFullError is translated into the
    # domain-level StorageFullError the GUI knows how to catch.
    monkeypatch.setattr(config, "LMDB_MAP_SIZE", 32 * 1024)
    store = CodeStore(path=tmp_path / "lmdb_full")
    try:
        with pytest.raises(StorageFullError):
            for i in range(100):
                store.put_code(f"key-{i}", b"x" * 4096)
    finally:
        store.close()
