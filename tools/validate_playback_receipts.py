#!/usr/bin/env python3
"""Validate playback-receipts-v2 derivation and focused state fixtures."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "vectors" / "playback-receipts-v2.json"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ROUTE_DOMAIN = b"den/playback-receipt-route/v2\0"
PROOF_DOMAIN = b"den/playback-receipt-proof/v2\0"

OPERATION_FIELDS = {
    "register": {"v", "op", "household", "route", "binding", "generation", "activity", "kid", "public", "issuedAt", "nonce"},
    "rebind": {"v", "op", "household", "route", "binding", "generation", "previousActivity", "activity", "issuedAt", "nonce"},
    "rotate": {"v", "op", "household", "route", "binding", "generation", "kid", "public", "issuedAt", "nonce"},
    "revoke": {"v", "op", "household", "route", "binding", "generation", "issuedAt", "nonce"},
    "playback": {"v", "op", "household", "route", "binding", "generation", "request", "issuedAt", "nonce"},
    "drain": {"v", "op", "household", "route", "binding", "generation", "issuedAt", "nonce"},
    "ack": {"v", "op", "household", "route", "binding", "generation", "upTo", "issuedAt", "nonce"},
    "activate": {"v", "op", "household", "route", "binding", "generation", "issuedAt", "nonce"},
}


def b64url_decode(value: str, length: int) -> bytes:
    if not isinstance(value, str) or "=" in value:
        raise ValueError("invalid_base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as error:
        raise ValueError("invalid_base64url") from error
    if len(decoded) != length or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != value:
        raise ValueError("invalid_base64url")
    return decoded


def canonical_json(value: object) -> str:
    # Fixture operations contain only ASCII object keys/strings and safe integers, for which this is RFC 8785 JCS.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def derive_route(authority_text: str) -> str:
    authority = b64url_decode(authority_text, 32)
    return hashlib.sha256(ROUTE_DOMAIN + authority).digest()[:16].hex()


def make_proof(authority_text: str, operation: dict) -> str:
    authority = b64url_decode(authority_text, 32)
    message = PROOF_DOMAIN + canonical_json(operation).encode("utf-8")
    return base64.urlsafe_b64encode(hmac.new(authority, message, hashlib.sha256).digest()).rstrip(b"=").decode()


def validate_operation(operation: object) -> None:
    if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
        raise ValueError("invalid_operation")
    fields = OPERATION_FIELDS.get(operation["op"])
    if fields is None or set(operation) != fields:
        raise ValueError("invalid_operation_fields")
    if (operation["v"] != 2 or not isinstance(operation["generation"], int)
            or not 1 <= operation["generation"] <= 2**53 - 1):
        raise ValueError("invalid_operation")
    for field in ("household", "route", "binding", "nonce"):
        if not isinstance(operation[field], str) or not HEX32.fullmatch(operation[field]):
            raise ValueError("invalid_operation")
    if not isinstance(operation["issuedAt"], int) or not 0 <= operation["issuedAt"] <= 2**53 - 1:
        raise ValueError("invalid_operation")
    for field in ("activity", "previousActivity", "kid"):
        if field in operation and (not isinstance(operation[field], str) or not HEX32.fullmatch(operation[field])):
            raise ValueError("invalid_operation")
    if "public" in operation and (not isinstance(operation["public"], str) or not operation["public"]):
        raise ValueError("invalid_operation")
    if "request" in operation and (not isinstance(operation["request"], str) or not HEX64.fullmatch(operation["request"])):
        raise ValueError("invalid_operation")
    if operation["op"] == "ack" and (not isinstance(operation["upTo"], int)
                                      or not 1 <= operation["upTo"] <= 2**53 - 1):
        raise ValueError("invalid_operation")


def validate_wrapper(wrapper: object, member_household: str, now: int) -> None:
    if not isinstance(wrapper, dict) or set(wrapper) != {"authority", "operation", "proof"}:
        raise ValueError("invalid_wrapper")
    operation = wrapper["operation"]
    validate_operation(operation)
    if operation["household"] != member_household:
        raise ValueError("not_a_member")
    route = derive_route(wrapper["authority"])
    if route != operation["route"]:
        raise ValueError("invalid_proof")
    expected = make_proof(wrapper["authority"], operation)
    if not isinstance(wrapper["proof"], str) or not hmac.compare_digest(expected, wrapper["proof"]):
        raise ValueError("invalid_proof")
    if abs(operation["issuedAt"] - now) > 300_000:
        raise ValueError("stale_proof")


def authority_outcome(case: dict) -> str:
    if not case["member"]:
        return "not_a_member"
    if case["role"] == "visitor" or case["authority"] is None:
        return "no_receipt"
    if case["authority"] != case["requestedRoute"]:
        return "invalid_proof"
    if case["binding"] != "current":
        return "stale_binding"
    return "allow"


def lifecycle_outcome(case: dict) -> str:
    if case["event"] in ("delete", "expire"):
        return "retire_and_purge"
    if case["proof"] != "same":
        return "invalid_proof"
    if case["event"] == "rebind" and not case["newActivityMember"]:
        return "not_activity_member"
    if case["event"] == "rebind":
        return "rebind"
    if case["event"] == "rotate":
        return "advance_keep_old"
    raise ValueError("unknown_lifecycle")


def apply_outcome(case: dict) -> str:
    if not case["sameRuntime"]:
        return "retry"
    if case["result"] in {"missing_key", "write_failed"}:
        return "retry"
    return "terminal"


def ack_ceiling(start: int, results: list[str]) -> int | None:
    ceiling = None
    for offset, result in enumerate(results):
        if result != "terminal":
            break
        ceiling = start + offset
    return ceiling


def activation_outcome(case: dict) -> str:
    if case["client"] == "visitor":
        return "no_receipt"
    if case["client"] == "tizen3":
        return "handoff"
    if case["mode"] == "legacy":
        return "v1"
    if case["client"] == "v1":
        return "playback_receipts_v2_required"
    return "v2"


def validate() -> None:
    vectors = json.loads(VECTORS.read_text())
    derivation = vectors["derivation"]
    if derivation["routeDomain"] + "\0" != ROUTE_DOMAIN.decode():
        raise ValueError("route_domain_mismatch")
    if derivation["proofDomain"] + "\0" != PROOF_DOMAIN.decode():
        raise ValueError("proof_domain_mismatch")
    if derive_route(derivation["authority"]) != derivation["route"]:
        raise ValueError("route_mismatch")
    for fixture in vectors["proofs"]:
        validate_operation(fixture["operation"])
        if fixture["operation"]["route"] != derivation["route"]:
            raise ValueError(f"{fixture['name']}: route mismatch")
        if make_proof(derivation["authority"], fixture["operation"]) != fixture["proof"]:
            raise ValueError(f"{fixture['name']}: proof mismatch")
    envelope = vectors["envelope"]
    if envelope["info"] != "den/playback-receipt/v2":
        raise ValueError("envelope_info_mismatch")
    if canonical_json(envelope["aad"]) != envelope["aadJcs"]:
        raise ValueError("aad_jcs_mismatch")
    if canonical_json(envelope["receipt"]) != envelope["receiptJcs"]:
        raise ValueError("receipt_jcs_mismatch")
    if set(envelope["aad"]) != {"v", "household", "route", "binding", "generation", "kid", "id"}:
        raise ValueError("invalid_aad_fields")
    receipt_fields = {"v", "household", "route", "binding", "generation", "kid", "id", "title", "season",
                      "episode", "positionSeconds", "durationSeconds", "completed", "endedAt", "expiresAt"}
    if set(envelope["receipt"]) != receipt_fields:
        raise ValueError("invalid_receipt_fields")
    bound = ("v", "household", "route", "binding", "generation", "kid", "id")
    if any(envelope["aad"][field] != envelope["receipt"][field] for field in bound):
        raise ValueError("envelope_route_binding_mismatch")
    decisions = (
        ("authorityCases", authority_outcome),
        ("lifecycleCases", lifecycle_outcome),
        ("applyCases", apply_outcome),
        ("activationCases", activation_outcome),
    )
    for group, decide in decisions:
        for case in vectors[group]:
            if decide(case) != case["expected"]:
                raise ValueError(f"{case['name']}: decision mismatch")
    for case in vectors["ackCases"]:
        if ack_ceiling(case["start"], case["results"]) != case["expected"]:
            raise ValueError(f"{case['name']}: ack mismatch")


def main() -> int:
    validate()
    print("playback-receipts-v2 vectors: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
