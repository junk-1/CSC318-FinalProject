"""BotRepository -- the single facade the GUI talks to.

Combines the SQLite (relational metadata) and LMDB (code + backtest blob)
layers. The frontend/ GUI layer never touches sqlite_db.py / lmdb_store.py
directly.

Write ordering convention used throughout: LMDB writes happen BEFORE the
SQLite row that references them (a crash mid-operation leaves at worst a
harmless orphaned blob, never a row pointing at missing data). Deletes go
the other way -- SQLite row removed first, then the now-unreferenced blob
is garbage collected.
"""

import os
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path

from . import config, sqlite_db
from .exceptions import ExportError, IntegrityError, NotFoundError, ValidationError
from .hashing import sha256_bytes
from .lmdb_store import CodeStore

# Shared base query: for every bot, join in its strategy name and its
# HEAD version's status/version/notes (the row with the highest
# version_number for that bot_id). The ROW_NUMBER() window function computes
# "which version is the head" once, in a single pass over bot_version, then
# `rn = 1` picks it out -- this is what makes a bot's row in the main table
# reflect its most recent upload without a separate query per bot.
_HEAD_JOIN_SQL = """
    WITH head AS (
        SELECT bv.*,
               ROW_NUMBER() OVER (PARTITION BY bv.bot_id ORDER BY bv.version_number DESC) AS rn
        FROM bot_version bv
    )
    SELECT
        b.bot_id         AS bot_id,
        b.bot_name       AS name,
        st.strategy_name AS strategy,
        h.status_tag     AS status,
        h.version_number AS version,
        h.version_note   AS notes
    FROM bot b
    JOIN strategy_type st ON st.strategy_id = b.strategy_id
    JOIN head h ON h.bot_id = b.bot_id AND h.rn = 1
"""

_SEARCH_SQL_WHERE = _HEAD_JOIN_SQL + """
    WHERE (:status = 'all' OR h.status_tag = :status)
      AND (:query = '' OR b.bot_name LIKE :like ESCAPE '\\' OR st.strategy_name LIKE :like ESCAPE '\\')
"""

_BOT_BY_ID_SQL = _HEAD_JOIN_SQL + " WHERE b.bot_id = :bot_id"

# Whitelisted sort columns for search_bots -- never interpolate a raw
# caller-supplied string into ORDER BY. sort_key/sort_dir from the GUI are
# looked up here, not used directly in SQL.
_SORT_COLUMNS = {
    "name":     "b.bot_name COLLATE NOCASE",
    "strategy": "st.strategy_name COLLATE NOCASE",
    "status":   "h.status_tag COLLATE NOCASE",
    "version":  "h.version_number",
    "notes":    "h.version_note COLLATE NOCASE",
}


class BotRepository:
    """Facade over the SQLite connection + LMDB store -- see module docstring."""

    def __init__(self, conn: sqlite3.Connection, code_store: CodeStore):
        # Both handles are opened by open_repository() below, not here.
        self._conn = conn
        self._store = code_store

    def close(self) -> None:
        # Called from the GUI's WM_DELETE_WINDOW handler on shutdown.
        self._conn.close()
        self._store.close()

    # ==================================================================
    # bots / search
    # ==================================================================

    def search_bots(self, query: str = "", status: str = "all",
                     sort_key: str = "name", sort_dir: str = "asc") -> list[dict]:
        # Escape SQL LIKE's own wildcard characters in the user's search
        # text so typing a literal "%" or "_" searches for that character
        # instead of matching everything / any single character.
        like = ""
        if query:
            like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        # sort_key/sort_dir come straight from a GUI header click -- looked
        # up against the whitelist above rather than trusted directly, so
        # there's no way to smuggle arbitrary SQL into ORDER BY.
        order_expr = _SORT_COLUMNS.get(sort_key, _SORT_COLUMNS["name"])
        direction = "DESC" if sort_dir == "desc" else "ASC"
        # b.bot_name is always the tiebreaker so equal-valued rows (e.g. two
        # bots both "finished") don't jump around between reloads.
        sql = (_SEARCH_SQL_WHERE
               + f" ORDER BY {order_expr} {direction}, b.bot_name COLLATE NOCASE")
        rows = self._conn.execute(
            sql, {"status": status, "query": query, "like": like}
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_bot_row(self, bot_id: int) -> dict:
        # Same head-join shape as search_bots, just filtered to one bot --
        # used to hand back a freshly created/edited bot in the exact dict
        # shape the GUI table already expects.
        row = self._conn.execute(_BOT_BY_ID_SQL, {"bot_id": bot_id}).fetchone()
        if row is None:
            raise NotFoundError(f"Bot {bot_id} not found.")
        return dict(row)

    def _get_head_version(self, bot_id: int) -> sqlite3.Row | None:
        # The raw bot_version row (not the joined/renamed search_bots shape)
        # for whichever version currently has the highest version_number.
        # Used internally by every method that reads/writes "the" status,
        # notes, or code for a bot.
        return self._conn.execute(
            "SELECT * FROM bot_version WHERE bot_id = ? ORDER BY version_number DESC LIMIT 1",
            (bot_id,),
        ).fetchone()

    def create_bot(self, file_path: str, bot_name: str, strategy_id: int) -> dict:
        """Create a new bot from a picked file, as version 1 of its history."""
        bot_name = bot_name.strip()
        if not bot_name:
            raise ValidationError("Bot name cannot be blank.")
        path = Path(file_path)
        if not path.is_file():
            raise ValidationError(f"File not found: {file_path}")

        # Hash + store the code BEFORE touching SQLite: if something fails
        # partway through the transaction below, the worst case is a
        # harmless orphaned blob in LMDB, never a bot_version row pointing
        # at code that was never actually written.
        data = path.read_bytes()
        file_hash = sha256_bytes(data)
        self._store.put_code(file_hash, data)

        # bot + its version-1 head row are inserted together in one
        # transaction so a failure (e.g. a bad strategy_id) never leaves a
        # bot with no versions.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self._conn.execute(
                "INSERT INTO bot (bot_name, strategy_id) VALUES (?, ?)",
                (bot_name, strategy_id),
            )
            bot_id = cur.lastrowid
            self._conn.execute(
                """INSERT INTO bot_version
                   (bot_id, version_number, parent_version_id, status_tag,
                    code_key, code_hash, source_filename, version_note)
                   VALUES (?, 1, NULL, ?, ?, ?, ?, '')""",
                (bot_id, config.DEFAULT_STATUS_TAG, file_hash, file_hash, path.name),
            )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError as e:
            # Most likely cause: strategy_id doesn't exist (FK violation).
            self._conn.execute("ROLLBACK")
            raise ValidationError(f"Invalid strategy_id {strategy_id}.") from e
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        return self._get_bot_row(bot_id)

    def add_version(self, bot_id: int, file_path: str) -> dict:
        """Called from BotDetailDialog's "Upload New Version" button. See
        module docstring for the dedup rule."""
        head = self._get_head_version(bot_id)
        if head is None:
            raise NotFoundError(f"Bot {bot_id} not found.")
        path = Path(file_path)
        if not path.is_file():
            raise ValidationError(f"File not found: {file_path}")

        data = path.read_bytes()
        file_hash = sha256_bytes(data)

        # Dedup rule: re-uploading the exact same bytes as the CURRENT head
        # is a no-op (no new row, no LMDB write) -- otherwise every
        # accidental re-upload of unchanged code would create a phantom
        # version. Uploading something that matches an OLDER, non-head
        # version (e.g. reverting) still creates a new head, since that's a
        # deliberate action with its own place in the history.
        if file_hash == head["code_hash"]:
            return dict(head)

        self._store.put_code(file_hash, data)

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # status_tag is carried forward from the previous head rather
            # than reset -- uploading new code shouldn't silently relabel a
            # bot back to "in development". version_note starts blank since
            # notes are scoped to a specific version, not the bot as a whole.
            self._conn.execute(
                """INSERT INTO bot_version
                   (bot_id, version_number, parent_version_id, status_tag,
                    code_key, code_hash, source_filename, version_note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '')""",
                (bot_id, head["version_number"] + 1, head["version_id"],
                 head["status_tag"], file_hash, file_hash, path.name),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        return dict(self._get_head_version(bot_id))

    def delete_bot(self, bot_id: int) -> None:
        # Removes the bot's SQL rows (cascading) and GCs its LMDB blobs.
        exists = self._conn.execute("SELECT 1 FROM bot WHERE bot_id = ?", (bot_id,)).fetchone()
        if exists is None:
            raise NotFoundError(f"Bot {bot_id} not found.")

        # Collect the LMDB keys this bot's rows reference BEFORE deleting
        # the rows -- once the DELETE below cascades, there's no SQL left to
        # tell us what to garbage-collect.
        code_hashes = [r["code_hash"] for r in self._conn.execute(
            "SELECT DISTINCT code_hash FROM bot_version WHERE bot_id = ?", (bot_id,)
        ).fetchall()]
        doc_keys = [r["doc_key"] for r in self._conn.execute(
            "SELECT doc_key FROM bot_backtest WHERE bot_id = ?", (bot_id,)
        ).fetchall()]

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # cascades to bot_version + bot_backtest via ON DELETE CASCADE
            self._conn.execute("DELETE FROM bot WHERE bot_id = ?", (bot_id,))
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        # GC code blobs only if no OTHER bot_version (any bot) still uses
        # them -- content-addressed storage means two different bots can
        # legitimately share a hash, so this bot's delete must not yank a
        # blob out from under a sibling bot that still references it.
        for h in code_hashes:
            still_referenced = self._conn.execute(
                "SELECT 1 FROM bot_version WHERE code_hash = ? LIMIT 1", (h,)
            ).fetchone()
            if still_referenced is None:
                self._store.delete_code(h)

        # Backtest doc_keys are fresh uuid4s, never shared between rows, so
        # there's nothing to reference-count -- every key here is safe to
        # delete unconditionally.
        for k in doc_keys:
            self._store.delete_backtest_doc(k)

    def set_status(self, bot_id: int, status: str) -> None:
        # Always writes to the HEAD version's status_tag -- this is what the
        # GUI's STATUS dropdown edits (see the schema-decision note in
        # STATE.md for why status lives on bot_version, not bot).
        if status not in config.STATUS_TAGS:
            raise ValidationError(f"Invalid status: {status!r}")
        head = self._get_head_version(bot_id)
        if head is None:
            raise NotFoundError(f"Bot {bot_id} not found.")
        self._conn.execute(
            "UPDATE bot_version SET status_tag = ? WHERE version_id = ?",
            (status, head["version_id"]),
        )

    def set_notes(self, bot_id: int, notes: str) -> None:
        # Same head-version pattern as set_status -- what the GUI's NOTES
        # box edits is really this bot's current head version's note.
        head = self._get_head_version(bot_id)
        if head is None:
            raise NotFoundError(f"Bot {bot_id} not found.")
        self._conn.execute(
            "UPDATE bot_version SET version_note = ? WHERE version_id = ?",
            (notes, head["version_id"]),
        )

    def rename_bot(self, bot_id: int, new_name: str) -> None:
        # bot_name lives on the `bot` row itself (not per-version), so
        # renaming applies retroactively to every version's history too --
        # there's only ever one name for a bot at a time.
        new_name = new_name.strip()
        if not new_name:
            raise ValidationError("Bot name cannot be blank.")
        exists = self._conn.execute("SELECT 1 FROM bot WHERE bot_id = ?", (bot_id,)).fetchone()
        if exists is None:
            raise NotFoundError(f"Bot {bot_id} not found.")
        self._conn.execute("UPDATE bot SET bot_name = ? WHERE bot_id = ?", (new_name, bot_id))

    def set_strategy(self, bot_id: int, strategy_id: int) -> None:
        # Reassigns a bot to a different strategy_type.
        exists = self._conn.execute("SELECT 1 FROM bot WHERE bot_id = ?", (bot_id,)).fetchone()
        if exists is None:
            raise NotFoundError(f"Bot {bot_id} not found.")
        try:
            self._conn.execute(
                "UPDATE bot SET strategy_id = ? WHERE bot_id = ?", (strategy_id, bot_id)
            )
        except sqlite3.IntegrityError as e:
            # strategy_id doesn't exist -- bot.strategy_id has a FK to
            # strategy_type.strategy_id.
            raise ValidationError(f"Invalid strategy_id {strategy_id}.") from e

    # ==================================================================
    # versions / code
    # ==================================================================

    def get_versions(self, bot_id: int) -> list[dict]:
        """Full version history, newest first -- powers the VERSION HISTORY
        list in BotDetailDialog."""
        rows = self._conn.execute(
            "SELECT * FROM bot_version WHERE bot_id = ? ORDER BY version_number DESC",
            (bot_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_code(self, version_id: int) -> tuple[bytes, str]:
        """Fetch a version's code back out, re-verifying it against its
        stored hash on every read. This -- not just storing the hash at
        upload time -- is what actually satisfies "stored bots are verified
        against hash": a mismatch here means the blob was corrupted or
        tampered with after being written."""
        row = self._conn.execute(
            "SELECT code_key, code_hash, source_filename FROM bot_version WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Version {version_id} not found.")
        blob = self._store.get_code(row["code_key"])
        if blob is None:
            raise IntegrityError(
                f"Code blob missing for version {version_id} (key {row['code_key']})."
            )
        if sha256_bytes(blob) != row["code_hash"]:
            raise IntegrityError(
                f"Hash mismatch for version {version_id} -- stored code may be corrupted."
            )
        return blob, row["source_filename"]

    # ==================================================================
    # strategies
    # ==================================================================

    def list_strategies(self) -> list[dict]:
        """Populates the strategy dropdowns in AddBotDialog and
        BotDetailDialog's header."""
        rows = self._conn.execute(
            "SELECT * FROM strategy_type ORDER BY strategy_name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]

    def create_strategy(self, strategy_name: str, market_type: str = "",
                         strategy_description: str = "") -> dict:
        """Backs AddBotDialog's inline "+ New Strategy..." sub-form."""
        strategy_name = strategy_name.strip()
        if not strategy_name:
            raise ValidationError("Strategy name cannot be blank.")
        try:
            cur = self._conn.execute(
                "INSERT INTO strategy_type (strategy_name, market_type, strategy_description) "
                "VALUES (?, ?, ?)",
                (strategy_name, market_type, strategy_description),
            )
        except sqlite3.IntegrityError as e:
            # strategy_type.strategy_name has a UNIQUE constraint.
            raise ValidationError(f"Strategy '{strategy_name}' already exists.") from e
        row = self._conn.execute(
            "SELECT * FROM strategy_type WHERE strategy_id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)

    # ==================================================================
    # backtests
    # ==================================================================

    def create_backtest(self, bot_id: int, doc_bytes: bytes, source_filename: str,
                         start_period: str = "", end_period: str = "", note: str = "") -> dict:
        exists = self._conn.execute("SELECT 1 FROM bot WHERE bot_id = ?", (bot_id,)).fetchone()
        if exists is None:
            raise NotFoundError(f"Bot {bot_id} not found.")
        # Unlike code, backtest docs are NOT content-addressed: two backtest
        # runs are unlikely to produce byte-identical documents, so a fresh
        # uuid4 per row is simpler than hash-based dedup that would rarely
        # actually dedupe anything.
        doc_key = uuid.uuid4().hex
        self._store.put_backtest_doc(doc_key, doc_bytes)
        cur = self._conn.execute(
            """INSERT INTO bot_backtest
               (bot_id, doc_key, source_filename, start_period, end_period, backtest_note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bot_id, doc_key, source_filename, start_period, end_period, note),
        )
        row = self._conn.execute(
            "SELECT * FROM bot_backtest WHERE backtest_id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)

    def list_backtests(self, bot_id: int) -> list[dict]:
        """Newest first -- powers the backtest list in BotDetailDialog."""
        rows = self._conn.execute(
            "SELECT * FROM bot_backtest WHERE bot_id = ? ORDER BY date_created DESC", (bot_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_backtest_doc(self, backtest_id: int) -> tuple[bytes, str]:
        """Fetch a backtest document back out for the "Export" button --
        the (bytes, source_filename) pair mirrors get_code()'s shape so the
        original file extension can be restored."""
        row = self._conn.execute(
            "SELECT doc_key, source_filename FROM bot_backtest WHERE backtest_id = ?",
            (backtest_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Backtest {backtest_id} not found.")
        blob = self._store.get_backtest_doc(row["doc_key"])
        if blob is None:
            raise NotFoundError(f"Backtest document missing for backtest {backtest_id}.")
        return blob, row["source_filename"]

    def delete_backtest(self, backtest_id: int) -> None:
        # SQL row first, then the LMDB blob -- consistent with the
        # delete-order convention in the module docstring.
        row = self._conn.execute(
            "SELECT doc_key FROM bot_backtest WHERE backtest_id = ?", (backtest_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Backtest {backtest_id} not found.")
        self._conn.execute("DELETE FROM bot_backtest WHERE backtest_id = ?", (backtest_id,))
        self._store.delete_backtest_doc(row["doc_key"])

    # ==================================================================
    # export
    # ==================================================================

    def export_vault(self, dest_zip_path: str) -> None:
        """Back up the whole vault (SQLite + LMDB) into one portable zip --
        the "Recoverability: user can export vault" NFR. Everything is
        staged in a temp dir first and the final zip is written via a
        temp-file-then-rename so a crash partway through never leaves a
        corrupt/partial archive sitting at the path the user picked."""
        dest_dir = os.path.dirname(dest_zip_path) or "."
        if not os.path.isdir(dest_dir) or not os.access(dest_dir, os.W_OK):
            raise ExportError(f"Cannot write to {dest_dir}")

        with tempfile.TemporaryDirectory(prefix="botvault_export_") as tmp:
            # sqlite3's Online Backup API, NOT a raw file copy -- correct
            # even while the live connection is open and in WAL mode, where
            # a plain file copy could miss pages still sitting in the
            # separate -wal file.
            backup_sqlite_path = os.path.join(tmp, "botvault.sqlite3")
            dest_conn = sqlite3.connect(backup_sqlite_path)
            try:
                self._conn.backup(dest_conn)
            except sqlite3.OperationalError as e:
                raise ExportError(f"Vault is busy, try again: {e}") from e
            finally:
                dest_conn.close()

            # env.copy(..., compact=True) is LMDB's own safe hot-backup call
            # (see CodeStore.copy_to) -- the destination dir must exist and
            # be empty first, which os.makedirs + a fresh tempdir guarantees.
            backup_lmdb_dir = os.path.join(tmp, "botvault_lmdb")
            os.makedirs(backup_lmdb_dir, exist_ok=True)
            self._store.copy_to(backup_lmdb_dir)

            # Zip into a ".part" file first, then atomically rename over the
            # real destination -- os.replace() is atomic on Windows too, so
            # there's never a half-written file visible at dest_zip_path.
            tmp_zip = dest_zip_path + ".part"
            try:
                with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.write(backup_sqlite_path, arcname="botvault.sqlite3")
                    for root, _, files in os.walk(backup_lmdb_dir):
                        for f in files:
                            full = os.path.join(root, f)
                            arc = os.path.join(
                                "botvault_lmdb", os.path.relpath(full, backup_lmdb_dir)
                            )
                            zf.write(full, arcname=arc)
                os.replace(tmp_zip, dest_zip_path)
            except OSError as e:
                if os.path.exists(tmp_zip):
                    os.remove(tmp_zip)
                raise ExportError(f"Failed to write export archive: {e}") from e
        # TemporaryDirectory's context manager removes the staged
        # sqlite/lmdb copies here automatically, success or failure.


def open_repository() -> BotRepository:
    """Construct a ready-to-use repository: opens/creates the SQLite DB,
    applies the schema (+ any pending migrations), seeds default
    strategies, and opens the LMDB env. This is the single entry point
    App.py calls -- it never constructs BotRepository directly."""
    conn = sqlite_db.connect()
    sqlite_db.init_schema(conn)
    sqlite_db.seed_strategies(conn)
    store = CodeStore()
    return BotRepository(conn, store)
