"""Content hashing used for integrity verification and dedup."""

import hashlib


def sha256_bytes(data: bytes) -> str:
    # hexdigest() (not digest()) because the result is stored as TEXT in
    # SQLite and used directly as the LMDB key -- both want a plain string,
    # not raw bytes.
    return hashlib.sha256(data).hexdigest()
