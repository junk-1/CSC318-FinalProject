"""LMDB (NoSQL) blob storage: bot code and backtest documents.

One environment, two named sub-databases -- simpler than two separate env
directories, and LMDB's single-writer model is a non-issue here since every
call happens synchronously on the Tkinter GUI thread.

Code is stored content-addressed (key = sha256 hex digest of the bytes),
which gives free, correct dedup: put() with overwrite=False is a silent
no-op if that exact content already exists, whether from this bot's own
prior version or a completely different bot that happens to share code.
Backtest documents are NOT content-addressed (fresh uuid4 key per row) --
see backend/repository.py for why.
"""

from pathlib import Path

import lmdb

from . import config
from .exceptions import StorageFullError


class CodeStore:
    """Thin wrapper around one LMDB environment with two named sub-dbs."""

    def __init__(self, path: Path = config.LMDB_DIR):
        # LMDB won't create the directory itself.
        path.mkdir(parents=True, exist_ok=True)
        # map_size is fixed for the life of this env (can't grow without
        # closing/reopening) -- sized generously in config.py since it's
        # just reserved address space, not actual disk usage up front.
        self._env = lmdb.open(
            str(path),
            map_size=config.LMDB_MAP_SIZE,
            max_dbs=config.LMDB_MAX_DBS,
            subdir=True,
        )
        # Two independent named sub-databases inside the one environment --
        # code and backtest_docs never collide even if a code hash and a
        # backtest uuid happened to be identical strings.
        self._code_db = self._env.open_db(config.CODE_SUBDB)
        self._backtest_db = self._env.open_db(config.BACKTEST_SUBDB)

    def close(self) -> None:
        # Flushes and releases the environment's memory map/file handles.
        self._env.close()

    def copy_to(self, dest_dir: str) -> None:
        """Safe hot-backup: dest_dir must already exist and be empty."""
        # env.copy(..., compact=True) is LMDB's documented mechanism for
        # backing up a *live, open* environment -- unlike copying the raw
        # data.mdb file, this can't race a concurrent write.
        self._env.copy(dest_dir, compact=True)

    # ---- code blobs --------------------------------------------------

    def put_code(self, key: str, data: bytes) -> None:
        try:
            # overwrite=False is the dedup mechanism: if this exact hash is
            # already stored (from this bot or a different one), the write
            # silently does nothing instead of re-writing identical bytes.
            with self._env.begin(write=True, db=self._code_db) as txn:
                txn.put(key.encode("utf-8"), data, overwrite=False)
        except lmdb.MapFullError as e:
            raise StorageFullError(
                "Local bot storage is full (LMDB map_size exhausted)."
            ) from e

    def get_code(self, key: str) -> bytes | None:
        # Read-only transaction (no write=True) -- LMDB allows any number of
        # concurrent readers, so this never blocks on a writer.
        with self._env.begin(db=self._code_db) as txn:
            return txn.get(key.encode("utf-8"))

    def has_code(self, key: str) -> bool:
        # Cheap existence check, reused by dedup logic in repository.py.
        return self.get_code(key) is not None

    def delete_code(self, key: str) -> None:
        # txn.delete() on a missing key is a harmless no-op, not an error --
        # callers don't need to has_code() first.
        with self._env.begin(write=True, db=self._code_db) as txn:
            txn.delete(key.encode("utf-8"))

    # ---- backtest documents -------------------------------------------
    # Same put/get/delete shape as the code blobs above, just against the
    # separate backtest_docs sub-db. Keys here are fresh uuid4s (see
    # repository.create_backtest), not content hashes, so overwrite=False
    # is just a defensive guard against a near-impossible uuid collision
    # rather than an actual dedup mechanism.

    def put_backtest_doc(self, key: str, data: bytes) -> None:
        # Same write pattern as put_code(), against the backtest_docs sub-db.
        try:
            with self._env.begin(write=True, db=self._backtest_db) as txn:
                txn.put(key.encode("utf-8"), data, overwrite=False)
        except lmdb.MapFullError as e:
            raise StorageFullError(
                "Local bot storage is full (LMDB map_size exhausted)."
            ) from e

    def get_backtest_doc(self, key: str) -> bytes | None:
        # Read-only lookup by uuid4 key; None if the row's doc was never
        # written or was already deleted.
        with self._env.begin(db=self._backtest_db) as txn:
            return txn.get(key.encode("utf-8"))

    def delete_backtest_doc(self, key: str) -> None:
        # No-op on a missing key, same as delete_code() above.
        with self._env.begin(write=True, db=self._backtest_db) as txn:
            txn.delete(key.encode("utf-8"))
