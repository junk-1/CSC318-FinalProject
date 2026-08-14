"""Tests for BotRepository.create_bot, search_bots, and the head-version-
scoped mutators (set_status, set_notes, rename_bot, set_strategy).

search_bots is the bulk of this file: it's a single SQL query joining
bot + strategy_type + a window-function-computed "head version" per bot
(see _HEAD_JOIN_SQL in repository.py), so most of what can go wrong here
is SQL-shaped -- wrong filter logic, unescaped wildcards, an unsafe sort
column, unstable ordering -- rather than plain Python logic bugs.
"""

import pytest

from backend import config
from backend.exceptions import NotFoundError, ValidationError
from backend.hashing import sha256_bytes


# ---- create_bot -----------------------------------------------------

def test_create_bot_creates_version_1_head(make_bot):
    # A brand-new bot is always its own version 1, in the app's default
    # status, with no notes yet.
    bot = make_bot(name="MyBot")
    assert bot["name"] == "MyBot"
    assert bot["version"] == 1
    assert bot["status"] == config.DEFAULT_STATUS_TAG
    assert bot["notes"] == ""


def test_create_bot_strips_whitespace_from_name(repo, strategy_id, write_file):
    # " Spacey " typed into the Add Bot dialog shouldn't become a bot
    # literally named " Spacey " with stray whitespace.
    bot = repo.create_bot(write_file(b"code"), "  Spacey  ", strategy_id)
    assert bot["name"] == "Spacey"


def test_create_bot_blank_name_raises_validation_error(repo, strategy_id, write_file):
    # Whitespace-only counts as blank once stripped, not just an empty string.
    path = write_file(b"code")
    with pytest.raises(ValidationError):
        repo.create_bot(path, "   ", strategy_id)


def test_create_bot_missing_file_raises_validation_error(repo, strategy_id, tmp_path):
    # Picking a file that doesn't exist on disk must surface as a domain
    # ValidationError, not an unhandled OSError from Path.read_bytes().
    missing = str(tmp_path / "nope.py")
    with pytest.raises(ValidationError):
        repo.create_bot(missing, "Bot", strategy_id)


def test_create_bot_invalid_strategy_id_raises_validation_error_and_leaves_orphaned_but_harmless_blob(
    repo, lmdb_store, write_file
):
    data = b"orphan content"
    path = write_file(data)
    file_hash = sha256_bytes(data)

    with pytest.raises(ValidationError):
        repo.create_bot(path, "Bot", 99999)

    # LMDB write happens before the SQL transaction (module docstring
    # invariant) -- the blob is orphaned but present, no bot row exists.
    assert lmdb_store.has_code(file_hash) is True
    assert repo.search_bots() == []


# ---- search_bots -----------------------------------------------------

def test_search_bots_returns_all_by_default(repo, strategy_id, write_file):
    # No query/status filter supplied -- every bot should come back.
    for name in ("Alpha", "Beta"):
        repo.create_bot(write_file(name.encode(), name=f"{name}.py"), name, strategy_id)
    results = repo.search_bots()
    assert {r["name"] for r in results} == {"Alpha", "Beta"}


def test_search_bots_filters_by_status(repo, make_bot):
    # Only bots whose HEAD version currently carries the requested
    # status_tag should be returned.
    make_bot(name="Dev")
    done = make_bot(name="Done")
    repo.set_status(done["bot_id"], "finished")

    results = repo.search_bots(status="finished")
    assert [r["name"] for r in results] == ["Done"]


def test_search_bots_status_all_returns_everything(repo, make_bot):
    # "all" is the sentinel value that disables the status filter entirely.
    make_bot(name="Dev")
    done = make_bot(name="Done")
    repo.set_status(done["bot_id"], "finished")

    results = repo.search_bots(status="all")
    assert {r["name"] for r in results} == {"Dev", "Done"}


def test_search_bots_query_matches_bot_name(repo, make_bot):
    make_bot(name="AlphaBot")
    make_bot(name="BetaBot")
    results = repo.search_bots(query="Alpha")
    assert [r["name"] for r in results] == ["AlphaBot"]


def test_search_bots_query_matches_strategy_name(repo, sqlite_conn, write_file):
    # Free-text search matches on the bot's OWN name and also on its
    # strategy's name -- not just bot_name.
    # `repo` and `sqlite_conn` share the same connection (repo depends on
    # sqlite_conn), so a strategy inserted directly here is immediately
    # visible to repo.create_bot() below.
    sid = sqlite_conn.execute(
        "INSERT INTO strategy_type (strategy_name, market_type, strategy_description) "
        "VALUES ('forexy', '', '')"
    ).lastrowid
    repo.create_bot(write_file(b"x"), "Bot1", sid)

    results = repo.search_bots(query="forexy")
    assert [r["name"] for r in results] == ["Bot1"]


def test_search_bots_query_escapes_percent_wildcard(repo, strategy_id, write_file):
    # Without escaping, a literal "%" typed by the user would act as a SQL
    # LIKE wildcard and over-match (e.g. also matching "50xoff"). search_bots
    # must escape it so searching "50%off" only matches that exact substring.
    repo.create_bot(write_file(b"a", name="a.py"), "50%off", strategy_id)
    repo.create_bot(write_file(b"b", name="b.py"), "50xoff", strategy_id)

    results = repo.search_bots(query="50%off")
    assert [r["name"] for r in results] == ["50%off"]


def test_search_bots_query_escapes_underscore_wildcard(repo, strategy_id, write_file):
    # Same idea as the "%" test above, but for LIKE's single-character
    # wildcard "_" -- "bot_1" shouldn't also match "botX1".
    repo.create_bot(write_file(b"a", name="a.py"), "bot_1", strategy_id)
    repo.create_bot(write_file(b"b", name="b.py"), "botX1", strategy_id)

    results = repo.search_bots(query="bot_1")
    assert [r["name"] for r in results] == ["bot_1"]


def test_search_bots_query_escapes_backslash(repo, strategy_id, write_file):
    # Backslash is the ESCAPE character search_bots uses for the two cases
    # above, so a literal "\" in the query must itself be escaped or it
    # would corrupt the escaping of whatever character follows it.
    repo.create_bot(write_file(b"a", name="a.py"), "path\\to\\bot", strategy_id)

    results = repo.search_bots(query="path\\to\\bot")
    assert [r["name"] for r in results] == ["path\\to\\bot"]


def test_search_bots_unknown_sort_key_falls_back_to_name(repo, strategy_id, write_file):
    # sort_key is looked up against a column whitelist rather than
    # interpolated directly into SQL (no ORDER BY injection risk); an
    # unrecognized key should degrade to the default sort, not raise.
    repo.create_bot(write_file(b"a", name="a.py"), "Zeta", strategy_id)
    repo.create_bot(write_file(b"b", name="b.py"), "Alpha", strategy_id)

    results = repo.search_bots(sort_key="does-not-exist")
    assert [r["name"] for r in results] == ["Alpha", "Zeta"]


@pytest.mark.parametrize("sort_key", ["name", "strategy", "status", "version", "notes"])
def test_search_bots_sort_by_each_whitelisted_column(repo, sqlite_conn, write_file, sort_key):
    # Exercises every sortable column in both directions. Bot A is given
    # the lowest value on every axis (name, strategy, status, version,
    # notes) and Bot Z the highest, so regardless of which column is
    # sorted, ascending must read [A, Z] and descending [Z, A].
    sid_a = sqlite_conn.execute(
        "INSERT INTO strategy_type (strategy_name, market_type, strategy_description) "
        "VALUES ('aaa-strategy', '', '')"
    ).lastrowid
    sid_z = sqlite_conn.execute(
        "INSERT INTO strategy_type (strategy_name, market_type, strategy_description) "
        "VALUES ('zzz-strategy', '', '')"
    ).lastrowid

    bot_a = repo.create_bot(write_file(b"a", name="a.py"), "AlphaBot", sid_a)
    bot_z = repo.create_bot(write_file(b"z", name="z.py"), "ZetaBot", sid_z)
    repo.add_version(bot_z["bot_id"], write_file(b"z2", name="z2.py"))  # bumps version to 2

    repo.set_status(bot_a["bot_id"], "finished")     # alphabetically lowest tag
    repo.set_status(bot_z["bot_id"], "shelved")       # alphabetically highest tag
    repo.set_notes(bot_a["bot_id"], "aaa note")
    repo.set_notes(bot_z["bot_id"], "zzz note")

    asc = repo.search_bots(sort_key=sort_key, sort_dir="asc")
    desc = repo.search_bots(sort_key=sort_key, sort_dir="desc")

    assert [r["name"] for r in asc] == ["AlphaBot", "ZetaBot"]
    assert [r["name"] for r in desc] == ["ZetaBot", "AlphaBot"]


def test_search_bots_ties_break_by_bot_name(repo, strategy_id, write_file):
    # When the sort column's value is equal across bots (both default
    # status here), bot_name is always the secondary sort key -- results
    # shouldn't reorder unpredictably between reloads.
    repo.create_bot(write_file(b"a", name="a.py"), "Zulu", strategy_id)
    repo.create_bot(write_file(b"b", name="b.py"), "Alpha", strategy_id)

    results = repo.search_bots(sort_key="status", sort_dir="asc")
    assert [r["name"] for r in results] == ["Alpha", "Zulu"]


def test_search_bots_reflects_head_version_status_and_notes_not_stale_version(
    repo, strategy_id, write_file
):
    # search_bots must reflect the CURRENT head version's status/notes
    # after a new version is uploaded -- not whatever version was head
    # when status/notes were originally set.
    bot = repo.create_bot(write_file(b"v1", name="v1.py"), "Bot", strategy_id)
    repo.set_notes(bot["bot_id"], "v1 notes")
    repo.set_status(bot["bot_id"], "finished")
    repo.add_version(bot["bot_id"], write_file(b"v2", name="v2.py"))

    row = repo.search_bots()[0]
    assert row["version"] == 2
    assert row["notes"] == ""          # notes reset on new head version
    assert row["status"] == "finished"  # status carried forward


# ---- set_status / set_notes / rename_bot / set_strategy -----------------
#
# status_tag and version_note are stored per-VERSION (on the head row of
# bot_version), not per-bot -- these four methods all follow the same
# shape: look up the current head, mutate it, raise NotFoundError if the
# bot doesn't exist at all.

def test_set_status_updates_head_version(repo, make_bot):
    bot = make_bot()
    repo.set_status(bot["bot_id"], "finished")
    assert repo.search_bots()[0]["status"] == "finished"


def test_set_status_invalid_status_raises_validation_error(repo, make_bot):
    # Must be one of config.STATUS_TAGS -- also CHECK-constrained in schema.sql.
    bot = make_bot()
    with pytest.raises(ValidationError):
        repo.set_status(bot["bot_id"], "not-a-real-status")


def test_set_status_missing_bot_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.set_status(99999, "finished")


def test_set_notes_updates_head_version(repo, make_bot):
    bot = make_bot()
    repo.set_notes(bot["bot_id"], "some notes")
    assert repo.search_bots()[0]["notes"] == "some notes"


def test_set_notes_missing_bot_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.set_notes(99999, "notes")


def test_rename_bot_updates_name(repo, make_bot):
    # bot_name lives on the `bot` row itself (not per-version), so renaming
    # is a plain single-column update, unlike status/notes above.
    bot = make_bot(name="Old")
    repo.rename_bot(bot["bot_id"], "New")
    assert repo.search_bots()[0]["name"] == "New"


def test_rename_bot_blank_name_raises_validation_error(repo, make_bot):
    bot = make_bot()
    with pytest.raises(ValidationError):
        repo.rename_bot(bot["bot_id"], "   ")


def test_rename_bot_missing_bot_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.rename_bot(99999, "New")


def test_set_strategy_updates_bot(repo, sqlite_conn, make_bot):
    bot = make_bot()
    sid2 = sqlite_conn.execute(
        "INSERT INTO strategy_type (strategy_name, market_type, strategy_description) "
        "VALUES ('other', '', '')"
    ).lastrowid
    repo.set_strategy(bot["bot_id"], sid2)
    assert repo.search_bots()[0]["strategy"] == "other"


def test_set_strategy_invalid_id_raises_validation_error(repo, make_bot):
    # bot.strategy_id has a FK to strategy_type -- an unknown id must
    # surface as ValidationError, not a raw sqlite3.IntegrityError.
    bot = make_bot()
    with pytest.raises(ValidationError):
        repo.set_strategy(bot["bot_id"], 99999)


def test_set_strategy_missing_bot_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.set_strategy(99999, 1)
