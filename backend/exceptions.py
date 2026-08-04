"""Domain-level exceptions raised by the backend package.

App.py (and dialogs) catch these specifically to show a friendly messagebox
instead of an unhandled traceback -- never catch bare Exception at the call
site, catch these.
"""


class BotVaultError(Exception):
    """Base class for all domain errors raised by the backend package."""


class ValidationError(BotVaultError):
    """Caller-supplied input is invalid (blank name, bad file, etc.)."""


class NotFoundError(BotVaultError):
    """A referenced bot/version/strategy/backtest id does not exist."""


class IntegrityError(BotVaultError):
    """A stored blob's recomputed hash does not match its recorded hash."""


class ExportError(BotVaultError):
    """The vault export routine could not complete."""


class StorageFullError(BotVaultError):
    """The LMDB environment's map_size has been exhausted."""
