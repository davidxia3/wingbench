"""Seal the private grades while an episode runs.

The runner keeps every private grade in memory and writes the list to disk
encrypted with a key it made for the episode. The key is written only after
the episode ends. This uses only the standard library. The cipher is a keyed
BLAKE2b stream XORed with the plaintext, plus a keyed BLAKE2b tag over the
ciphertext. It is meant to stop accidental reading, not a determined attacker.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

MAGIC = b"WBSEAL1\n"
KEY_BYTES = 32
NONCE_BYTES = 16
TAG_BYTES = 32
BLOCK_BYTES = 64


def new_key() -> bytes:
    return secrets.token_bytes(KEY_BYTES)


def _stream_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for block, offset in enumerate(range(0, len(data), BLOCK_BYTES)):
        stream = hashlib.blake2b(
            nonce + block.to_bytes(8, "big"), key=key, digest_size=BLOCK_BYTES
        ).digest()
        chunk = data[offset : offset + BLOCK_BYTES]
        out[offset : offset + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, stream))
    return bytes(out)


def _tag(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return hashlib.blake2b(nonce + ciphertext, key=key, digest_size=TAG_BYTES).digest()


def seal(key: bytes, plaintext: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise ValueError("key must be 32 bytes")
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = _stream_xor(key, nonce, plaintext)
    return MAGIC + nonce + _tag(key, nonce, ciphertext) + ciphertext


def unseal(key: bytes, blob: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise ValueError("key must be 32 bytes")
    if not blob.startswith(MAGIC):
        raise ValueError("not a sealed WingBench file")
    body = blob[len(MAGIC) :]
    nonce = body[:NONCE_BYTES]
    tag = body[NONCE_BYTES : NONCE_BYTES + TAG_BYTES]
    ciphertext = body[NONCE_BYTES + TAG_BYTES :]
    if len(nonce) != NONCE_BYTES or len(tag) != TAG_BYTES:
        raise ValueError("sealed file is truncated")
    if not hmac.compare_digest(tag, _tag(key, nonce, ciphertext)):
        raise ValueError("sealed file does not match its key")
    return _stream_xor(key, nonce, ciphertext)
