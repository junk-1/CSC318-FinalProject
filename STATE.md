# BotVault — Project State

Last updated: 2026-08-02

This file documents what has been implemented, the design decisions behind
it, what was deliberately left out, and what's still open. It's a handoff
document, not user-facing help — see the in-app status bar / README-style
comments in the code for that.

## What BotVault is

A fully local (no network, no server) Windows desktop app that catalogs
trading bots: metadata + strategy classification in SQLite, actual code and
backtest documents (content) in LMDB. Built on the original GUI-only
prototype in `App.py`, whose backend touchpoints were stubbed with `# JUN:`
comments and an in-memory `_SAMPLE` list.

## File inventory

```
frontend/
  App.py                      GUI (CustomTkinter). Owns the main window,
                              table, toolbar, statusbar, hotkeys, row
                              selection. Talks only to
                              backend.repository.BotRepository. Inserts the
                              project root onto sys.path before importing
                              backend, since frontend/ is a sibling of
                              backend/, not a parent.
  theme.py                     COL / COLUMNS / STATUS_COLOR / TAG_OPTIONS.
                              No CTkFont objects (can't exist before a
                              CTk() root).
  dialogs.py                    AddBotDialog — modal collecting bot name +
                              strategy (with inline "create new strategy")
                              after the file picker, before create_bot() is
                              called.
  detail_dialog.py               BotDetailDialog — double-click popup:
                              version history, "Upload New Version",
                              "Export Code", backtest list, add/delete
                              backtest.
schema.sql                  SQLite DDL: strategy_type, bot, bot_version,
                        bot_backtest + indexes.
requirements.txt              customtkinter, lmdb
run_botvault.bat                Launcher (see below).
backend/
  config.py                    Paths (%LOCALAPPDATA%\BotVault), STATUS_TAGS,
                              LMDB sizing, seed strategy list.
  exceptions.py                 BotVaultError and subclasses (Validation,
                              NotFound, Integrity, Export, StorageFull).
  hashing.py                     sha256_bytes().
  sqlite_db.py                    connect() / init_schema() / seed_strategies().
  lmdb_store.py                     CodeStore — one LMDB env, two named
                              sub-dbs ("code", "backtest_docs").
  repository.py                     BotRepository — the ONLY module App.py
                              imports from. All business logic lives here.
```

`final project (1).docx` is the original requirements/ERD doc this was
built from. `desktop.ini` is a Windows folder-view artifact, unrelated.

## Key design decisions (deviations from the literal ERD, all confirmed
with the project owner)

1. **`bot_version.status_tag`**: the ERD had this as a BOOLEAN ("is this
   version running"). It's repurposed as a TEXT enum
   (`in development` / `finished` / `shelved`), CHECK-constrained, matching
   the GUI's STATUS dropdown that already existed. `bot_version.version_note`
   is exactly what the GUI's NOTES box edits. Both are scoped to a bot's
   **current head version** (`MAX(version_number)` per `bot_id`) — there is
   no separate bot-level status/notes column.
   - **Accepted tradeoff**: uploading a new version resets `version_note` to
     blank, since notes are version-scoped, not bot-scoped. `status_tag` is
     carried forward from the previous head instead of resetting.
2. **`bot_performance` table removed entirely** — performance data now just
   lives inside backtest documents in LMDB (raw content), not structured
   SQL columns. `bot_backtest` (doc_key, start/end period, note) is kept.
3. **Dedup rule** for re-uploading code (`add_version`): if the new file's
   sha256 hash matches the bot's *current head* version, it's a no-op (no
   new version row, no LMDB write). If it matches an older, non-head
   version (e.g. an intentional revert), a new head version is still
   created.
4. **`bot_version.source_filename`** added — not in the original ERD, but
   required to restore the correct `.py`/`.cs` extension when code is
   exported back out via the detail popup's "Export Code".
5. **Content-addressable code storage**: LMDB key = sha256 hex digest of
   the file bytes. This gives free, correct dedup — identical code re-used
   across *different* bots is only stored once. `bot_backtest.doc_key` is
   deliberately **not** content-addressed (fresh UUID4 per row) since
   backtest documents are unlikely to be byte-identical across bots.
6. **Data location**: `%LOCALAPPDATA%\BotVault\` (`botvault.sqlite3` +
   `botvault_lmdb\`), not inside the project/source folder — standard
   Windows app-data separation, keeps source and user data independent.
7. **First-run strategy seeding**: `strategy_type` is seeded (idempotent,
   `INSERT OR IGNORE`) with the doc's example list — discretionary,
   non-discretionary, forex, futures, stocks — so the very first "Add bot"
   flow doesn't force the user to invent a strategy type immediately.

## What's implemented and working

- Full CRUD wiring: `load_bots` → `search_bots` (window-function query for
  each bot's head version, live search + status filter), `add_bot` →
  `create_bot` (with the new AddBotDialog for name/strategy), `delete_bot`,
  `set_status`, `set_notes` — all backed by SQLite + LMDB, replacing the old
  in-memory `_SAMPLE` list.
- **Integrity check on read**: `get_code()` recomputes the sha256 of the
  retrieved blob and compares to the stored hash, raising `IntegrityError`
  on mismatch — this is what actually satisfies the "stored bots are
  verified against hash" NFR, not just storing the hash passively.
- **Delete with LMDB garbage collection**: deleting a bot removes its SQL
  rows (cascade via `ON DELETE CASCADE` + `PRAGMA foreign_keys=ON`) and only
  GCs its code blob from LMDB if no *other* bot_version anywhere still
  references that same hash.
- **Hotkeys**: `+` (add), `/` (focus search), `Delete` (remove selected
  row) — all guarded so they don't fire while a text entry has focus.
- **Row selection** (single click, green border) and **detail popup**
  (double click) — version history, upload-new-version, export-code,
  backtest add/list/delete. This was the "final milestone," built last per
  your instruction, using repository methods (`add_version`, `get_code`,
  `get_versions`, `create_backtest`, `list_backtests`, `delete_backtest`)
  that had already been written earlier specifically to support it.
- **Export Vault** toolbar button — snapshots SQLite (via the Online Backup
  API, safe under WAL) + LMDB (via `env.copy(compact=True)`, LMDB's hot
  backup call) into one portable `.zip`, written atomically (`.part` file +
  `os.replace`).
- Clean shutdown (`WM_DELETE_WINDOW` closes the SQLite connection + LMDB
  env) — previously the app had no shutdown hook at all.
- `run_botvault.bat` — launches the app with the correct interpreter
  (`C:\Users\Pivital\miniconda3\python.exe`, the one with `customtkinter`
  and `lmdb` installed) regardless of the current working directory;
  pauses only if the app exits with an error, so a crash is visible instead
  of a flashing window.

## Verification performed this session

- Syntax-checked every new/edited file.
- `import App` (module-level only, no GUI launch) — clean, no errors.
- An ad hoc backend smoke-test script (not committed to the repo — it lived
  in a scratch temp directory) exercised, against a temp DB: strategy
  seeding, bot creation, search/filter, status+notes edits and validation,
  version dedup on identical content, version bump + status carry-forward
  on different content, integrity-checked code retrieval, strategy
  duplicate rejection, backtest create/list/get/delete, vault export (zip
  contents verified), bot deletion, and post-delete LMDB GC — all passed.
- Launched the real GUI headlessly for ~4s to confirm it starts without a
  traceback and correctly creates `%LOCALAPPDATA%\BotVault\botvault.sqlite3`
  + `botvault_lmdb\` on first run.
- Launched `run_botvault.bat` and confirmed it correctly starts
  `python.exe` with `App.py`.
- **Not verified interactively**: I don't have hands-on GUI control in this
  environment, so the actual click-through of `AddBotDialog` (including the
  inline "+ New Strategy..." sub-form) and `BotDetailDialog` (upload new
  version, export code, add/delete backtest) has only been exercised via
  the repository layer directly, not by clicking the real widgets. Worth a
  manual pass before you rely on it.

## Deliberately out of scope / not done

- **No auto-export / scheduled backup** — user declined this explicitly;
  `Export Vault` is a manual, on-demand action only.
- **No automated test suite committed to the repo** — the smoke test that
  validated the backend was a throwaway script in a temp scratch directory,
  not saved into the project. If you want repeatable regression tests,
  that's a follow-up (e.g. a `tests/` folder with `pytest`, reusing the
  same monkeypatch-`backend.config`-to-a-temp-dir approach).
- **No packaging/installer** (PyInstaller, MSIX, etc.) — currently runs
  from source via the `.bat` launcher or `python frontend\App.py` directly.
- **Not a git repo** — no version control on this project yet. Recommended
  before further changes; not done because it's a durable repo-level
  action that needs explicit confirmation.
- **`bot_performance`** table is gone per your decision — if you later want
  structured performance metrics (sharpe, win rate, drawdown, etc.) queried
  or sorted in SQL rather than living inside opaque backtest documents,
  that table would need to be reintroduced.

## How to run it

```
C:\Users\Pivital\miniconda3\python.exe -m pip install -r requirements.txt   # once
run_botvault.bat                                                             # every time
```

Data lives at `%LOCALAPPDATA%\BotVault\`. Deleting a bot's *original source
file* after adding it to BotVault is safe — the code is copied into
`botvault_lmdb\` at upload time and never read from the original path
again. Losing the `%LOCALAPPDATA%\BotVault\` folder itself (or this
machine) loses everything unless you've used `Export Vault` to back it up
elsewhere first — there is no cloud/server component in this app.

## 2026-08-04 session

- Moved the GUI layer (`App.py`, `theme.py`, `dialogs.py`,
  `detail_dialog.py`) into a `frontend/` folder, mirroring `backend/`.
  `frontend/App.py` now inserts the project root onto `sys.path` before
  importing `backend`, since the two packages are siblings rather than
  parent/child. `run_botvault.bat` updated to launch `frontend\App.py`.
- Added/filled in comments across all 10 `.py` files (function/class-level
  and inline block-level) for readability.
- Appended an "Implementation Notes — Deviations from Original ERD"
  section to `BotVault SRS.docx`, documenting the design decisions above
  (status_tag as enum, dropped `bot_performance`, dedup rule,
  `source_filename`, content-addressable storage, data location, strategy
  seeding) without altering the original assignment text.
- The GUI click-through and git-init follow-ups from the previous session
  are still open (see above) — not addressed this session.
