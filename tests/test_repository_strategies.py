"""Tests for BotRepository.list_strategies and create_strategy.

These back the strategy dropdowns in AddBotDialog / BotDetailDialog, so
the main things worth pinning down are: the list is always usably sorted,
and name collisions are handled predictably.
"""

import pytest

from backend.exceptions import ValidationError


def test_list_strategies_empty_when_none_seeded(repo):
    # The `repo` fixture deliberately does NOT seed default strategies
    # (that's `seeded_repo`), so a fresh repository starts with none.
    assert repo.list_strategies() == []


def test_list_strategies_sorted_case_insensitive(repo):
    # Inserted out of alphabetical AND mixed-case order -- the query must
    # sort case-insensitively (COLLATE NOCASE) for the dropdown to make
    # sense to a user.
    repo.create_strategy("banana")
    repo.create_strategy("Apple")
    repo.create_strategy("cherry")

    names = [s["strategy_name"] for s in repo.list_strategies()]
    assert names == ["Apple", "banana", "cherry"]


def test_create_strategy_success(repo):
    # Confirms the returned dict shape includes every field the caller
    # supplied, not just strategy_name.
    s = repo.create_strategy("Trend Following", market_type="stocks", strategy_description="desc")
    assert s["strategy_name"] == "Trend Following"
    assert s["market_type"] == "stocks"
    assert s["strategy_description"] == "desc"


def test_create_strategy_blank_name_raises_validation_error(repo):
    # Whitespace-only counts as blank once stripped.
    with pytest.raises(ValidationError):
        repo.create_strategy("   ")


def test_create_strategy_duplicate_name_raises_validation_error(repo):
    # strategy_type.strategy_name is UNIQUE -- the raw sqlite3.IntegrityError
    # from that constraint must be translated into the domain ValidationError.
    repo.create_strategy("Momentum")
    with pytest.raises(ValidationError):
        repo.create_strategy("Momentum")


def test_create_strategy_different_case_name_is_allowed(repo):
    # strategy_type.strategy_name's UNIQUE constraint has no COLLATE NOCASE,
    # so this is intentionally allowed, not a bug to "fix" later.
    repo.create_strategy("Forex")
    second = repo.create_strategy("forex")

    assert second["strategy_name"] == "forex"
    names = {s["strategy_name"] for s in repo.list_strategies()}
    assert {"Forex", "forex"} <= names


def test_create_strategy_defaults_market_type_and_description_to_empty_string(repo):
    s = repo.create_strategy("Bare")
    assert s["market_type"] == ""
    assert s["strategy_description"] == ""
