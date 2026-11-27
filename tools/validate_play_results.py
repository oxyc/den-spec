#!/usr/bin/env python3
"""Validate the play-result vectors (wire/play-results-v1.md) with the standard library.

AES-GCM is not in Python's standard library, so the seal itself is checked by regenerating the file with Node
(test_play_results.py); this checks every derivation and plaintext rule the spec states.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re

from validate_wake_controller import hkdf_sha256


ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "vectors" / "play-results-v1.json"
SALT = b"den/play-result/v1"
LOWER_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
PLAINTEXT_BYTES = 128
SEALED_CHARS = 208
STATES = {"preparing", "playing", "failed"}
REASONS = {"not_allowed", "no_source", "no_trailer", "expired", "superseded", "failed"}


def address(request_id: str) -> str:
    if not LOWER_HEX_32.fullmatch(request_id):
        raise ValueError("invalid_request_id")
    return hkdf_sha256(bytes.fromhex(request_id), SALT, b"address", 16).hex()


def key(request_id: str) -> str:
    if not LOWER_HEX_32.fullmatch(request_id):
        raise ValueError("invalid_request_id")
    return hkdf_sha256(bytes.fromhex(request_id), SALT, b"key", 32).hex()


def result(plaintext: str) -> dict:
    """A plaintext as a v1 receiver writes it, or ValueError for one it never writes. (A reader is more lenient: it
    ignores unknown members and reads an unknown `reason` as `failed`.)"""
    raw = plaintext.encode("utf-8")
    if len(raw) != PLAINTEXT_BYTES:
        raise ValueError("plaintext_size")
    body = json.loads(plaintext)
    if not isinstance(body, dict) or body.get("v") != 1:
        raise ValueError("plaintext_version")
    if not isinstance(body.get("at"), int) or isinstance(body.get("at"), bool):
        raise ValueError("plaintext_at")
    if body.get("state") not in STATES:
        raise ValueError("plaintext_state")
    if ("reason" in body) != (body["state"] == "failed"):
        raise ValueError("plaintext_reason")
    if body["state"] == "failed" and body["reason"] not in REASONS:
        raise ValueError("plaintext_reason")
    return body


def validate() -> None:
    vectors = json.loads(VECTORS.read_text())
    request_id = vectors["id"]
    if vectors["address"] != address(request_id):
        raise AssertionError("address is not the id's HKDF derivation")
    if vectors["key"] != key(request_id):
        raise AssertionError("key is not the id's HKDF derivation")
    if bytes.fromhex(vectors["aad"]) != SALT + b"\x00" + vectors["address"].encode():
        raise AssertionError("aad is not the salt, 0x00 and the address")
    states = set()
    for entry in vectors["results"]:
        body = result(entry["plaintext"])
        states.add(body["state"])
        sealed = entry["sealed"]
        if len(sealed) != SEALED_CHARS or "=" in sealed:
            raise AssertionError("a sealed result is 208 unpadded base64url characters")
        raw = base64.urlsafe_b64decode(sealed + "=" * (-len(sealed) % 4))
        if raw[:12].hex() != entry["nonce"]:
            raise AssertionError("a sealed result starts with its nonce")
    if states != STATES:
        raise AssertionError("the vectors seal one result of every state")


if __name__ == "__main__":
    validate()
    print("play-results-v1 vectors: ok")
