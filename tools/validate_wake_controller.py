#!/usr/bin/env python3
"""Validate the wake-controller identifier vectors with the standard library."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "vectors" / "wake-controller-v1.json"
LOWER_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
LOWER_HEX_48 = re.compile(r"^[0-9a-f]{48}$")
LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF-SHA256."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    output = b""
    previous = b""
    counter = 1
    while len(output) < length:
        previous = hmac.new(prk, previous + info + bytes([counter]), hashlib.sha256).digest()
        output += previous
        counter += 1
    return output[:length]


def wake_inbox(credential: str) -> str:
    if not LOWER_HEX_48.fullmatch(credential):
        raise ValueError("invalid_inbox_credential")
    return hashlib.sha256(("den/wake/inbox/v1:" + credential).encode("utf-8")).hexdigest()


def validate() -> None:
    vectors = json.loads(VECTORS.read_text())
    inbox = vectors["inbox"]
    pairing = json.loads((ROOT / "vectors" / "pairing-v1.json").read_text())
    if inbox["domain"] != "den/wake/inbox/v1:":
        raise ValueError("invalid_inbox_domain")
    if inbox["credential"] != pairing["pairing"]["link"]["inbox"]:
        raise ValueError("inbox_not_pairing_vector")
    if not LOWER_HEX_64.fullmatch(inbox["wakeId"]):
        raise ValueError("invalid_wake_id")
    if wake_inbox(inbox["credential"]) != inbox["wakeId"]:
        raise ValueError("wake_id_mismatch")
    for invalid in vectors["invalidInboxCredentials"]:
        try:
            wake_inbox(invalid)
        except ValueError:
            pass
        else:
            raise ValueError("accepted_noncanonical_inbox")

    assistant = vectors["assistant"]
    key = bytes.fromhex(assistant["householdPlayKey"])
    if len(key) != 32 or assistant["salt"] != "den/link/v1" or assistant["info"] != "assistant-tv":
        raise ValueError("invalid_assistant_input")
    lane = hkdf_sha256(key, assistant["salt"].encode(), assistant["info"].encode(), 16).hex()
    if not LOWER_HEX_32.fullmatch(assistant["laneId"]) or lane != assistant["laneId"]:
        raise ValueError("assistant_lane_mismatch")


def main() -> int:
    validate()
    print("wake-controller-v1 vectors: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
