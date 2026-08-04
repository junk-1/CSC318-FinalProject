"""Paths and constants shared across the backend package.

Runtime data (the SQLite file and the LMDB environment) lives under
%LOCALAPPDATA%\\BotVault, not inside the project/source folder -- standard
Windows app-data separation, so the source tree and the user's actual bot
library stay independent (e.g. deleting/moving the project folder never
touches the data).
"""

import os
from pathlib import Path

# %LOCALAPPDATA% is always set on Windows; the home-dir fallback only
# matters if this ever runs somewhere that env var is missing.
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BotVault"
SQLITE_PATH = DATA_DIR / "botvault.sqlite3"
LMDB_DIR = DATA_DIR / "botvault_lmdb"

# schema.sql lives at the project root (one level up from backend/), not
# inside the package, so it's easy to find/edit alongside the top-level
# project files (frontend/, requirements.txt, etc.).
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

# Must stay in lockstep with theme.STATUS_COLOR / theme.TAG_OPTIONS and the
# bot_version.status_tag CHECK constraint in schema.sql.
STATUS_TAGS = ("in development", "finished", "shelved")
DEFAULT_STATUS_TAG = "in development"

# LMDB reserves virtual address space up front on open, not actual disk, so
# it's cheap to size generously -- 1 GiB comfortably covers a source-code
# catalog without needing to grow (map_size can't grow without reopening).
LMDB_MAP_SIZE = 1 * 1024 * 1024 * 1024
LMDB_MAX_DBS = 2
CODE_SUBDB = b"code"
BACKTEST_SUBDB = b"backtest_docs"

# Seeded into strategy_type on first run (idempotent), from the project
# doc's example list, purely to keep the first "Add bot" flow to a couple of
# clicks instead of forcing the user to invent a strategy type immediately.
SEED_STRATEGIES = [
    {"strategy_name": "discretionary", "market_type": "", "strategy_description": ""},
    {"strategy_name": "non-discretionary", "market_type": "", "strategy_description": ""},
    {"strategy_name": "forex", "market_type": "forex", "strategy_description": ""},
    {"strategy_name": "futures", "market_type": "futures", "strategy_description": ""},
    {"strategy_name": "stocks", "market_type": "stocks", "strategy_description": ""},
]
