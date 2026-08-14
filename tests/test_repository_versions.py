"""Tests for BotRepository.add_version, get_versions, and get_code.

The interesting behavior here is the dedup/revert rule in add_version (see
module docstring in repository.py) and the integrity re-verification in
get_code -- both are easy to silently regress without a test catching it.
"""

import pytest

from backend.exceptions import IntegrityError, NotFoundError, ValidationError


# ---- add_version dedup / revert rules ------------------------------------

def test_add_version_same_hash_as_head_is_noop(repo, lmdb_store, make_bot, write_file, monkeypatch):
    # Re-uploading byte-identical content to the current head must be a
    # true no-op: no new bot_version row AND no LMDB write attempted (not
    # just "no write that matters" -- put_code should never even be called).
    bot = make_bot(data=b"same content")

    calls = []
    monkeypatch.setattr(lmdb_store, "put_code", lambda key, data: calls.append(key))

    result = repo.add_version(bot["bot_id"], write_file(b"same content", name="dup.py"))

    assert calls == []  # no LMDB write attempted
    assert len(repo.get_versions(bot["bot_id"])) == 1
    assert result["version_number"] == 1


def test_add_version_different_content_creates_new_head_version(repo, make_bot, write_file):
    bot = make_bot(data=b"v1")
    repo.add_version(bot["bot_id"], write_file(b"v2", name="v2.py"))

    versions = repo.get_versions(bot["bot_id"])
    assert len(versions) == 2
    assert versions[0]["version_number"] == 2  # newest first


def test_add_version_carries_forward_status_tag_from_previous_head(repo, make_bot, write_file):
    # Uploading new code shouldn't silently relabel a "finished" bot back
    # to "in development" -- status_tag carries forward from the old head.
    bot = make_bot(data=b"v1")
    repo.set_status(bot["bot_id"], "finished")
    repo.add_version(bot["bot_id"], write_file(b"v2", name="v2.py"))

    head = repo.get_versions(bot["bot_id"])[0]
    assert head["status_tag"] == "finished"


def test_add_version_resets_version_note_to_blank_on_new_version(repo, make_bot, write_file):
    # Unlike status, notes are scoped to a specific version, not the bot as
    # a whole -- a new version starts with a blank note, not the old one.
    bot = make_bot(data=b"v1")
    repo.set_notes(bot["bot_id"], "some note")
    repo.add_version(bot["bot_id"], write_file(b"v2", name="v2.py"))

    head = repo.get_versions(bot["bot_id"])[0]
    assert head["version_note"] == ""


def test_add_version_matching_older_nonhead_version_still_creates_new_version(
    repo, make_bot, write_file
):
    # The deliberate exception to the dedup rule above: matching an OLDER,
    # non-head version (e.g. an intentional revert) still creates a new
    # head rather than being silently deduped, since it's a distinct
    # deliberate action with its own place in the history.
    bot = make_bot(data=b"v1")
    repo.add_version(bot["bot_id"], write_file(b"v2", name="v2.py"))          # head becomes v2
    result = repo.add_version(bot["bot_id"], write_file(b"v1", name="revert.py"))  # revert to v1's bytes

    versions = repo.get_versions(bot["bot_id"])
    assert len(versions) == 3
    assert result["version_number"] == 3
    # the new head's hash matches the original v1 content, not v2's
    assert versions[0]["code_hash"] == versions[-1]["code_hash"]


def test_add_version_missing_bot_raises_not_found(repo, write_file):
    with pytest.raises(NotFoundError):
        repo.add_version(99999, write_file(b"x"))


def test_add_version_missing_file_raises_validation_error(repo, make_bot, tmp_path):
    bot = make_bot()
    missing = str(tmp_path / "nope.py")
    with pytest.raises(ValidationError):
        repo.add_version(bot["bot_id"], missing)


# ---- get_versions ------------------------------------------------------

def test_get_versions_ordered_newest_first(repo, make_bot, write_file):
    # Powers the VERSION HISTORY list in the GUI's detail popup.
    bot = make_bot(data=b"v1")
    repo.add_version(bot["bot_id"], write_file(b"v2", name="v2.py"))
    repo.add_version(bot["bot_id"], write_file(b"v3", name="v3.py"))

    versions = repo.get_versions(bot["bot_id"])
    assert [v["version_number"] for v in versions] == [3, 2, 1]


def test_get_versions_returns_empty_list_for_unknown_bot_no_exception(repo):
    # Asymmetric with almost every other repository method (which raise
    # NotFoundError for an unknown bot_id) -- documented here deliberately,
    # not an oversight to "fix" without checking callers first.
    assert repo.get_versions(99999) == []


# ---- get_code integrity checking ---------------------------------------

def test_get_code_returns_bytes_and_source_filename(repo, make_bot):
    # source_filename lets the GUI's "Export Code" restore the original
    # .py/.cs extension, since LMDB keys are just hash strings.
    bot = make_bot(name="Bot", data=b"print(1)")
    version_id = repo.get_versions(bot["bot_id"])[0]["version_id"]

    data, filename = repo.get_code(version_id)
    assert data == b"print(1)"
    assert filename == "Bot.py"


def test_get_code_raises_not_found_for_unknown_version_id(repo):
    with pytest.raises(NotFoundError):
        repo.get_code(99999)


def test_get_code_raises_integrity_error_when_blob_missing(repo, lmdb_store, make_bot):
    # First of get_code's two distinct integrity-failure paths: the SQL row
    # references a code_key that no longer has a blob behind it in LMDB.
    bot = make_bot(data=b"data")
    version = repo.get_versions(bot["bot_id"])[0]
    lmdb_store.delete_code(version["code_hash"])

    with pytest.raises(IntegrityError):
        repo.get_code(version["version_id"])


def test_get_code_raises_integrity_error_on_hash_mismatch(repo, lmdb_store, make_bot):
    # Second failure path: a blob exists under the right key, but its
    # bytes no longer hash to the value stored in bot_version.code_hash --
    # this is what actually satisfies "stored bots are verified against
    # hash" (re-checked on every read, not just recorded once at upload).
    bot = make_bot(data=b"data")
    version = repo.get_versions(bot["bot_id"])[0]
    code_hash = version["code_hash"]

    # Simulate corruption: same key, different (non-matching) bytes.
    lmdb_store.delete_code(code_hash)
    lmdb_store.put_code(code_hash, b"corrupted bytes that don't hash to code_hash")

    with pytest.raises(IntegrityError):
        repo.get_code(version["version_id"])
