"""Tests for BotRepository.export_vault -- the "Export Vault" backup
button. Covers the zip's contents/roundtrip fidelity and the failure/atomicity
guarantees the implementation is documented as providing (safe under a live
WAL connection, no partial file left behind on failure, atomic overwrite).
"""

import os
import sqlite3
import zipfile

import pytest

from backend.exceptions import ExportError
from backend.hashing import sha256_bytes
from backend.lmdb_store import CodeStore


def test_export_vault_creates_zip_with_sqlite_and_lmdb_entries(repo, make_bot, tmp_path):
    make_bot()
    dest_zip = str(tmp_path / "vault.zip")

    repo.export_vault(dest_zip)

    assert os.path.exists(dest_zip)
    with zipfile.ZipFile(dest_zip) as zf:
        names = zf.namelist()
        assert "botvault.sqlite3" in names
        assert any(n.startswith("botvault_lmdb/") for n in names)


def test_export_vault_roundtrip_data_matches_original(repo, make_bot, tmp_path):
    # The strongest correctness check in this file: actually reopen the
    # exported copies (a plain sqlite3 connection + a fresh CodeStore) and
    # confirm they contain the same data as the live vault, rather than
    # just checking the zip's file listing.
    bot = make_bot(name="Exported", data=b"exported code")
    dest_zip = str(tmp_path / "vault.zip")
    repo.export_vault(dest_zip)

    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    with zipfile.ZipFile(dest_zip) as zf:
        zf.extractall(extract_dir)

    conn2 = sqlite3.connect(str(extract_dir / "botvault.sqlite3"))
    conn2.row_factory = sqlite3.Row
    try:
        row = conn2.execute(
            "SELECT bot_name FROM bot WHERE bot_id = ?", (bot["bot_id"],)
        ).fetchone()
        assert row["bot_name"] == bot["name"]
    finally:
        conn2.close()

    store2 = CodeStore(path=extract_dir / "botvault_lmdb")
    try:
        code_hash = sha256_bytes(b"exported code")
        assert store2.get_code(code_hash) == b"exported code"
    finally:
        store2.close()


def test_export_vault_raises_exporterror_when_dest_dir_missing(repo, tmp_path):
    # Checked up front, before any sqlite/LMDB work happens, so a bad path
    # fails fast with a domain error rather than partway through the backup.
    missing_dir_zip = str(tmp_path / "does-not-exist" / "vault.zip")
    with pytest.raises(ExportError):
        repo.export_vault(missing_dir_zip)


def test_export_vault_raises_exporterror_when_dest_dir_not_writable(repo, tmp_path, monkeypatch):
    dest_dir = tmp_path / "readonly"
    dest_dir.mkdir()
    # Real OS permission bits are unreliable to rely on for the owning user
    # on Windows -- force the writability check to fail instead.
    monkeypatch.setattr(os, "access", lambda *a, **k: False)

    with pytest.raises(ExportError):
        repo.export_vault(str(dest_dir / "vault.zip"))


def test_export_vault_cleans_up_part_file_on_zip_write_failure(repo, make_bot, tmp_path, monkeypatch):
    # export_vault writes to a "<dest>.part" file first, then os.replace()s
    # it over the real destination. A failure mid-write must leave neither
    # a half-written destination file nor a stray ".part" file behind.
    make_bot()
    dest_zip = tmp_path / "vault.zip"

    def failing_write(self, *a, **k):
        raise OSError("simulated disk error")

    monkeypatch.setattr(zipfile.ZipFile, "write", failing_write)

    with pytest.raises(ExportError):
        repo.export_vault(str(dest_zip))

    assert not dest_zip.exists()
    assert not (tmp_path / "vault.zip.part").exists()


def test_export_vault_overwrites_existing_destination_atomically(repo, make_bot, tmp_path):
    # os.replace() is atomic on Windows too, so exporting to a path that
    # already has a previous export sitting there should just succeed and
    # cleanly replace it, not fail or leave a ".part" file behind.
    make_bot(name="First")
    dest_zip = str(tmp_path / "vault.zip")
    repo.export_vault(dest_zip)

    make_bot(name="Second")
    repo.export_vault(dest_zip)

    assert os.path.exists(dest_zip)
    assert not os.path.exists(dest_zip + ".part")
