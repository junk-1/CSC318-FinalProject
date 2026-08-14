import hashlib
import string

from backend.hashing import sha256_bytes

# NIST/FIPS 180-4 published test vectors -- known-correct, not derived from
# hashlib, so this actually catches a broken/replaced implementation.
_KNOWN_VECTORS = {
    b"": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    b"abc": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
}


def test_sha256_bytes_matches_known_vectors():
    for data, expected in _KNOWN_VECTORS.items():
        assert sha256_bytes(data) == expected


def test_sha256_bytes_matches_hashlib_directly():
    for data in (b"", b"abc", b"BotVault", bytes(range(256)) * 4):
        assert sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_sha256_bytes_deterministic():
    data = b"same content every time"
    assert sha256_bytes(data) == sha256_bytes(data)


def test_sha256_bytes_differs_for_different_input():
    assert sha256_bytes(b"foo") != sha256_bytes(b"bar")


def test_sha256_bytes_returns_lowercase_hex_string_of_length_64():
    digest = sha256_bytes(b"anything")
    assert len(digest) == 64
    assert set(digest) <= set(string.hexdigits.lower())
