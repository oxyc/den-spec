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
SCHEMA = ROOT / "schemas" / "playback-receipts-v2.schema.json"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ROUTE_DOMAIN = b"den/playback-receipt-route/v2\0"
PROOF_DOMAIN = b"den/playback-receipt-proof/v2\0"
RETIRED_DOMAIN = b"den/playback-receipt-retired/v2\0"
SAFE_INTEGER = 2**53 - 1
XWING_PUBLIC_KEY_BYTES = 1216
EXACT_UINT_TOKEN = re.compile(r"^(?:0|[1-9][0-9]*)$")

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


def strict_json_loads(text: str) -> object:
    def parse_int(token: str) -> int:
        if not EXACT_UINT_TOKEN.fullmatch(token):
            raise ValueError("invalid_integer_token")
        value = int(token)
        if value > SAFE_INTEGER:
            raise ValueError("unsafe_integer")
        return value

    def reject_number(token: str) -> float:
        raise ValueError(f"invalid_number_token:{token}")

    return json.loads(text, parse_int=parse_int, parse_float=reject_number, parse_constant=reject_number)


def derive_route(authority_text: str) -> str:
    authority = b64url_decode(authority_text, 32)
    return hashlib.sha256(ROUTE_DOMAIN + authority).digest()[:16].hex()


def make_proof(authority_text: str, operation: dict) -> str:
    authority = b64url_decode(authority_text, 32)
    message = PROOF_DOMAIN + canonical_json(operation).encode("utf-8")
    return base64.urlsafe_b64encode(hmac.new(authority, message, hashlib.sha256).digest()).rstrip(b"=").decode()


def exact_uint(value: object, minimum: int = 0) -> bool:
    """JSON booleans are not integers and decoders must not coerce fractional numbers."""
    return type(value) is int and minimum <= value <= SAFE_INTEGER


def validate_public_key(public_text: object, kid: object) -> None:
    public = b64url_decode(public_text, XWING_PUBLIC_KEY_BYTES)
    expected_kid = hashlib.sha256(public).digest()[:16].hex()
    if not isinstance(kid, str) or not HEX32.fullmatch(kid) or not hmac.compare_digest(expected_kid, kid):
        raise ValueError("invalid_public_key_id")


def derive_retired_tag(key_text: str, household: str, route: str) -> str:
    key = b64url_decode(key_text, 32)
    if not HEX32.fullmatch(household) or not HEX32.fullmatch(route):
        raise ValueError("invalid_retired_route")
    message = RETIRED_DOMAIN + bytes.fromhex(household) + bytes.fromhex(route)
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def retirement_state_path(case: dict) -> list[dict]:
    """Run the focused reserved-slot state path used by the retirement fixture."""
    capacity = case["capacity"]
    live_routes = case["initialLiveRoutes"]
    active_tombstones: set[str] = set()
    retired_tags: dict[int, set[str]] = {}
    retained_keys = {entry["version"]: entry["key"] for entry in case["retainedKeys"]}
    target_route_live = True
    snapshots = []

    if not exact_uint(capacity, 1) or not exact_uint(live_routes) or live_routes > capacity:
        raise ValueError("invalid_retirement_state")
    if len(retained_keys) != len(case["retainedKeys"]) or not 1 <= len(retained_keys) <= 2:
        raise ValueError("invalid_retirement_keys")

    def reserved() -> int:
        return live_routes + len(active_tombstones) + sum(len(tags) for tags in retired_tags.values())

    for step in case["steps"]:
        event = step["event"]
        route = step["route"]
        if event == "retire":
            if not target_route_live or live_routes == 0:
                raise ValueError("invalid_retirement_transition")
            live_routes -= 1
            target_route_live = False
            active_tombstones.add(route)
            outcome = "retired"
        elif event == "compact":
            version = step["keyVersion"]
            if route not in active_tombstones or version not in retained_keys:
                raise ValueError("invalid_retirement_transition")
            active_tombstones.remove(route)
            retired_tags.setdefault(version, set()).add(
                derive_retired_tag(retained_keys[version], case["household"], route)
            )
            outcome = "compacted"
        elif event == "register":
            # A generation-1 replay is checked under every retained Kretire version
            # before ordinary slot admission, so rotation cannot resurrect a route.
            stale = any(
                derive_retired_tag(key, case["household"], route) in retired_tags.get(version, set())
                for version, key in retained_keys.items()
            )
            if route in active_tombstones or stale:
                outcome = "receipt_route_retired"
            elif reserved() >= capacity:
                outcome = "receipt_route_history_full"
            else:
                live_routes += 1
                outcome = "registered"
        else:
            raise ValueError("unknown_retirement_event")

        if reserved() > capacity:
            raise ValueError("retirement_capacity_exceeded")
        snapshots.append({
            "event": event,
            "outcome": outcome,
            "liveRoutes": live_routes,
            "activeTombstones": len(active_tombstones),
            "retiredTags": sum(len(tags) for tags in retired_tags.values()),
            "reserved": reserved(),
        })
    return snapshots


def validate_operation(operation: object) -> None:
    if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
        raise ValueError("invalid_operation")
    fields = OPERATION_FIELDS.get(operation["op"])
    if fields is None or set(operation) != fields:
        raise ValueError("invalid_operation_fields")
    if type(operation["v"]) is not int or operation["v"] != 2 or not exact_uint(operation["generation"], 1):
        raise ValueError("invalid_operation")
    for field in ("household", "route", "binding", "nonce"):
        if not isinstance(operation[field], str) or not HEX32.fullmatch(operation[field]):
            raise ValueError("invalid_operation")
    if not exact_uint(operation["issuedAt"]):
        raise ValueError("invalid_operation")
    for field in ("activity", "previousActivity", "kid"):
        if field in operation and (not isinstance(operation[field], str) or not HEX32.fullmatch(operation[field])):
            raise ValueError("invalid_operation")
    if "public" in operation:
        validate_public_key(operation["public"], operation["kid"])
    if "request" in operation and (not isinstance(operation["request"], str) or not HEX64.fullmatch(operation["request"])):
        raise ValueError("invalid_operation")
    if operation["op"] == "ack" and not exact_uint(operation["upTo"], 1):
        raise ValueError("invalid_operation")


def validate_drain_response(response: object) -> None:
    if not isinstance(response, dict) or set(response) != {"v", "route", "binding", "messages"}:
        raise ValueError("invalid_drain_response")
    if type(response["v"]) is not int or response["v"] != 2:
        raise ValueError("invalid_drain_response")
    for field in ("route", "binding"):
        if not isinstance(response[field], str) or not HEX32.fullmatch(response[field]):
            raise ValueError("invalid_drain_response")
    if not isinstance(response["messages"], list) or len(response["messages"]) > 25:
        raise ValueError("invalid_drain_response")
    previous_seq = 0
    for entry in response["messages"]:
        if not isinstance(entry, dict) or set(entry) != {"generation", "id", "kid", "sealed", "seq"}:
            raise ValueError("invalid_drain_entry")
        if not exact_uint(entry["generation"], 1) or not exact_uint(entry["seq"], 1):
            raise ValueError("invalid_drain_entry")
        if entry["seq"] <= previous_seq:
            raise ValueError("invalid_drain_order")
        previous_seq = entry["seq"]
        for field in ("id", "kid"):
            if not isinstance(entry[field], str) or not HEX32.fullmatch(entry[field]):
                raise ValueError("invalid_drain_entry")
        if not isinstance(entry["sealed"], str) or not entry["sealed"]:
            raise ValueError("invalid_drain_entry")


def validate_envelope(envelope: object) -> None:
    if not isinstance(envelope, dict) or set(envelope) != {"info", "aad", "aadJcs", "receipt", "receiptJcs"}:
        raise ValueError("invalid_envelope")
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
    for value in (envelope["aad"], envelope["receipt"]):
        if type(value["v"]) is not int or value["v"] != 2 or not exact_uint(value["generation"], 1):
            raise ValueError("invalid_envelope_integer")
    receipt = envelope["receipt"]
    if (not exact_uint(receipt["season"]) or receipt["season"] > 9999
            or not exact_uint(receipt["episode"], 1) or receipt["episode"] > 99999
            or not exact_uint(receipt["positionSeconds"])
            or not exact_uint(receipt["durationSeconds"], 1)
            or receipt["positionSeconds"] > receipt["durationSeconds"]
            or not exact_uint(receipt["endedAt"])
            or not exact_uint(receipt["expiresAt"])
            or receipt["expiresAt"] != receipt["endedAt"] + 604_800_000):
        raise ValueError("invalid_receipt_integer")
    if type(receipt["completed"]) is not bool:
        raise ValueError("invalid_completed")
    if not isinstance(receipt["title"], dict) or set(receipt["title"]) != {"id", "type"}:
        raise ValueError("invalid_title")
    if not exact_uint(receipt["title"]["id"], 1) or receipt["title"]["type"] not in {"movie", "tv"}:
        raise ValueError("invalid_title")


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
        return "retire_purge_and_compact_tag"
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
    vectors = strict_json_loads(VECTORS.read_text())
    schema = strict_json_loads(SCHEMA.read_text())
    defs = schema["$defs"]
    if set(defs["drainEntry"]["required"]) != {"generation", "id", "kid", "sealed", "seq"}:
        raise ValueError("schema_drain_entry_mismatch")
    if (defs["xwingPublic"]["minLength"] != 1622 or defs["xwingPublic"]["maxLength"] != 1622
            or defs["xwingPublic"]["x-den-decodedLength"] != XWING_PUBLIC_KEY_BYTES):
        raise ValueError("schema_public_key_mismatch")
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
    validate_envelope(vectors["envelope"])
    validate_drain_response(vectors["drainResponse"])
    public_key = vectors["publicKey"]
    validate_public_key(public_key["public"], public_key["kid"])
    for case in vectors["integerTokenCases"]:
        try:
            parsed = strict_json_loads('{"value":' + case["token"] + "}")
            accepted = exact_uint(parsed["value"])
        except (ValueError, json.JSONDecodeError):
            accepted = False
        if accepted != case["valid"]:
            raise ValueError(f"{case['name']}: integer token decision mismatch")
    for case in vectors["publicKeyCases"]:
        public = public_key["public"][:-2] if case["mutation"] == "truncate" else public_key["public"]
        kid = "0" * 32 if case["mutation"] == "wrongKid" else public_key["kid"]
        try:
            validate_public_key(public, kid)
            accepted = True
        except ValueError:
            accepted = False
        if accepted != case["valid"]:
            raise ValueError(f"{case['name']}: public key decision mismatch")
    retirement = vectors["retirement"]
    if retirement["tagDomain"] + "\0" != RETIRED_DOMAIN.decode():
        raise ValueError("retired_domain_mismatch")
    if derive_retired_tag(retirement["key"], retirement["household"], retirement["route"]) != retirement["tag"]:
        raise ValueError("retired_tag_mismatch")
    capacity_path = vectors["retirementCapacityPath"]
    if retirement_state_path(capacity_path) != capacity_path["expected"]:
        raise ValueError("retirement_capacity_path_mismatch")
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
