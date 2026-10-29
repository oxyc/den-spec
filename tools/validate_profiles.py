#!/usr/bin/env python3
"""Validate profiles-v1 vectors without third-party dependencies."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import math
import re
import sys
import unicodedata
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "vectors" / "profiles-v1.json"
SCHEMA = ROOT / "schemas" / "profiles-v1.schema.json"
ID = re.compile(r"^[0-9a-f]{32}$")
DEVICE = re.compile(r"^(?:|[0-9a-f]{16})$")
AVATAR = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
TITLE_KEY = re.compile(r"^(?:movie|tv):([1-9][0-9]*)$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
ROW_KEY = re.compile(r"^[0-9a-f]{64}$")
SAFE_INTEGER = 9_007_199_254_740_991
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_DEPTH = 32


def fail(message: str) -> None:
    raise ValueError(message)


def canonical_json(value: object) -> bytes:
    """RFC 8785 bytes, including ECMAScript serialization for every finite JSON number."""
    output: list[str] = []

    def string(text: object) -> str:
        if not isinstance(text, str):
            fail("invalid_jcs")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            fail("invalid_jcs")
        return json.dumps(text, ensure_ascii=False, separators=(",", ":"))

    def number_text(number: object) -> str:
        if type(number) is int and abs(number) <= 1 << 53:
            return str(number)
        try:
            value = float(number)
        except (OverflowError, TypeError, ValueError):
            fail("invalid_jcs_number")
        if not math.isfinite(value):
            fail("invalid_jcs_number")
        if value == 0:
            return "0"

        # CPython's repr and ECMAScript both choose the shortest decimal that round-trips to the same IEEE-754
        # double. Reframe those digits at ECMAScript's fixed/exponent thresholds (§3.2.2.3), and never inherit
        # Python's different threshold or padded exponent spelling.
        text = repr(abs(value)).lower()
        mantissa, marker, exponent_text = text.partition("e")
        exponent = int(exponent_text) if marker else 0
        integer, point, fraction = mantissa.partition(".")
        raw_digits = integer + (fraction if point else "")
        decimal_point = len(integer)
        leading = len(raw_digits) - len(raw_digits.lstrip("0"))
        digits = raw_digits.lstrip("0").rstrip("0")
        if not digits:
            return "0"
        n = decimal_point + exponent - leading
        k = len(digits)
        if k <= n <= 21:
            body = digits + "0" * (n - k)
        elif 0 < n <= 21:
            body = digits[:n] + "." + digits[n:]
        elif -6 < n <= 0:
            body = "0." + "0" * (-n) + digits
        else:
            rest = "" if k == 1 else "." + digits[1:]
            power = n - 1
            body = f"{digits[0]}{rest}e{'+' if power >= 0 else '-'}{abs(power)}"
        return ("-" if value < 0 else "") + body

    def write(item: object) -> None:
        if item is None:
            output.append("null")
        elif item is True:
            output.append("true")
        elif item is False:
            output.append("false")
        elif type(item) in (int, float):
            output.append(number_text(item))
        elif isinstance(item, str):
            output.append(string(item))
        elif isinstance(item, list):
            output.append("[")
            for index, child in enumerate(item):
                if index:
                    output.append(",")
                write(child)
            output.append("]")
        elif isinstance(item, dict):
            members = []
            for key, child in item.items():
                if not isinstance(key, str):
                    fail("invalid_jcs")
                try:
                    order = key.encode("utf-16-be")
                except UnicodeEncodeError:
                    fail("invalid_jcs")
                members.append((order, key, child))
            members.sort(key=lambda member: member[0])
            output.append("{")
            for index, (_, key, child) in enumerate(members):
                if index:
                    output.append(",")
                output.append(string(key))
                output.append(":")
                write(child)
            output.append("}")
        else:
            fail("invalid_jcs")

    write(value)
    return "".join(output).encode()


def document_plaintext(jcs: bytes) -> bytes:
    """library-v4 document framing: 0x00 followed by one complete raw-DEFLATE stream."""
    compressor = zlib.compressobj(level=9, wbits=-15)
    plaintext = b"\x00" + compressor.compress(jcs) + compressor.flush()
    if inflate_document_plaintext(plaintext) != jcs:
        fail("invalid_document_plaintext")
    return plaintext


def json_depth_within(value: object, maximum: int) -> bool:
    """Bounded iterative check; corrupt extension trees must not exhaust the language stack."""
    pending = [(value, 0)]
    while pending:
        item, parent_depth = pending.pop()
        if isinstance(item, dict):
            depth = parent_depth + 1
            if depth > maximum:
                return False
            pending.extend((child, depth) for child in item.values())
        elif isinstance(item, list):
            depth = parent_depth + 1
            if depth > maximum:
                return False
            pending.extend((child, depth) for child in item)
    return True


def inflate_document_plaintext(plaintext: bytes) -> bytes:
    if not plaintext.startswith(b"\x00"):
        fail("invalid_document_plaintext")
    inflater = zlib.decompressobj(wbits=-15)
    try:
        jcs = inflater.decompress(plaintext[1:]) + inflater.flush()
    except zlib.error:
        fail("invalid_document_plaintext")
    if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        fail("invalid_document_plaintext")
    return jcs


def snapshot_digest(kind: str, value: object) -> tuple[str, str]:
    if not isinstance(value, dict):
        fail("invalid_migration_digest")

    def safe_integer(number: object, *, positive: bool = False) -> int:
        lower = 1 if positive else 0
        if type(number) is not int or not lower <= number <= SAFE_INTEGER:
            fail("invalid_migration_digest")
        return number

    def rows(raw: object, *, sequenced: bool, base: int | None = None) -> list[dict]:
        if not isinstance(raw, list):
            fail("invalid_migration_digest")
        keys: set[str] = set()
        sequences: set[int] = set()
        answer: list[dict] = []
        expected = {"k", "seq", "v"} if sequenced else {"k", "v"}
        for row in raw:
            if not isinstance(row, dict) or set(row) != expected:
                fail("invalid_migration_digest")
            key = row["k"]
            if (not isinstance(key, str) or not ROW_KEY.fullmatch(key) or key in keys
                    or not isinstance(row["v"], str)):
                fail("invalid_migration_digest")
            keys.add(key)
            if sequenced:
                sequence = safe_integer(row["seq"], positive=True)
                if sequence in sequences or base is None or sequence > base:
                    fail("invalid_migration_digest")
                sequences.add(sequence)
            answer.append(copy.deepcopy(row))
        return sorted(answer, key=lambda row: row["k"].encode())

    def library_id(identifier: object) -> str:
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
            fail("invalid_migration_digest")
        return identifier

    if kind in ("source", "owner"):
        expected = {"libraryId", "base", "wireMin", "rows"}
        if kind == "source":
            expected.add("generation")
        if set(value) != expected:
            fail("invalid_migration_digest")
        base = safe_integer(value["base"])
        normalized = {
            "libraryId": library_id(value["libraryId"]),
            "base": base,
            "wireMin": safe_integer(value["wireMin"], positive=True),
            "rows": rows(value["rows"], sequenced=True, base=base),
        }
        if kind == "source":
            normalized["generation"] = safe_integer(value["generation"])
    else:
        fail("invalid_migration_digest_kind")
    canonical = canonical_json(normalized)
    return canonical.decode(), hashlib.sha256(canonical).hexdigest()


def unpadded_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode_unpadded_base64url(value: object) -> bytes:
    if not isinstance(value, str) or "=" in value or not re.fullmatch(r"[0-9A-Za-z_-]*", value):
        fail("invalid_materialize_vector")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        fail("invalid_materialize_vector")


def initial_directory_constructor(value: object) -> dict:
    """Construct, stored-validate and exactly materialize the first Owner-only directory."""
    if not isinstance(value, dict) or set(value) != {"attempt", "source", "backup", "owner", "ownerHead"}:
        fail("invalid_directory_constructor")
    owner = value["owner"]
    if not isinstance(owner, dict) or set(owner) != {
            "id", "display", "libraryKey", "libraryAt", "policy", "deletedAt"}:
        fail("invalid_directory_constructor")
    owner_id = owner["id"]
    if not isinstance(owner_id, str) or not ID.fullmatch(owner_id):
        fail("invalid_profile")
    document = {
        "format": 4,
        "kind": "profiles",
        "schema": 1,
        "owner": owner_id,
        "migration": {
            "version": 1,
            "attempt": copy.deepcopy(value["attempt"]),
            "source": copy.deepcopy(value["source"]),
            "backup": copy.deepcopy(value["backup"]),
            "ownerProfile": owner_id,
            "ownerHead": copy.deepcopy(value["ownerHead"]),
        },
        "profiles": {
            owner_id: {
                "id": owner_id,
                "role": "owner",
                "display": copy.deepcopy(owner["display"]),
                "library": {
                    "value": {"keyVersion": 1, "key": copy.deepcopy(owner["libraryKey"])},
                    "at": copy.deepcopy(owner["libraryAt"]),
                },
                "policy": copy.deepcopy(owner["policy"]),
                "deleted": {"value": False, "at": copy.deepcopy(owner["deletedAt"])},
            }
        },
    }
    directory = validate_directory(document, require_migration=True)
    jcs = canonical_json(directory)
    return {"directory": directory, "jcs": unpadded_base64url(jcs),
            "plaintext": unpadded_base64url(document_plaintext(jcs))}


def profiles_materialize(document: object) -> dict:
    """Validate and materialize one stored profiles directory without caller-selected bytes."""
    if not isinstance(document, dict):
        fail("invalid_profiles_materialize")
    if "migration" not in document:
        fail("profiles_migration_required")
    try:
        directory = validate_directory(document, require_migration=True)
        if not json_depth_within(directory, MAX_DOCUMENT_DEPTH):
            fail("invalid_profiles_materialize")
        jcs = canonical_json(directory)
    except ValueError as error:
        if str(error) == "migration_missing":
            fail("profiles_migration_required")
        fail("invalid_profiles_materialize")
    if len(jcs) > MAX_DOCUMENT_BYTES:
        fail("profiles_materialize_too_large")
    plaintext = document_plaintext(jcs)
    try:
        inflated = inflate_document_plaintext(plaintext)
        decoded = json.loads(inflated)
        validate_directory(decoded, require_migration=True)
        if not json_depth_within(decoded, MAX_DOCUMENT_DEPTH):
            fail("profiles_materialize_roundtrip")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        fail("profiles_materialize_roundtrip")
    if inflated != jcs or canonical_json(decoded) != jcs or decoded != directory:
        fail("profiles_materialize_roundtrip")
    return {"directory": directory, "jcs": unpadded_base64url(jcs),
            "plaintext": unpadded_base64url(plaintext)}


def validate_stamp(value: object) -> None:
    if not isinstance(value, list) or len(value) != 3:
        fail("invalid_stamp")
    if not all(type(n) is int and 0 <= n <= 9_007_199_254_740_991 for n in value[:2]):
        fail("invalid_stamp")
    if not isinstance(value[2], str) or not DEVICE.fullmatch(value[2]):
        fail("invalid_stamp")


def stamped(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"value", "at"}:
        fail("invalid_stamped_value")
    validate_stamp(value["at"])
    return value


def validate_policy(value: object) -> None:
    if not isinstance(value, dict) or not {"maximumAge", "unknownRatings"} <= set(value):
        fail("invalid_policy")
    maximum_age = value["maximumAge"]
    if maximum_age is not None and (type(maximum_age) is not int or not 0 <= maximum_age <= 18):
        fail("invalid_policy")
    if value["unknownRatings"] not in ("allow", "block"):
        fail("invalid_policy")


def validate_migration(value: object, owner: str) -> None:
    if not isinstance(value, dict) or set(value) != {
        "version", "attempt", "source", "backup", "ownerProfile", "ownerHead"
    }:
        fail("invalid_migration")
    if value["version"] != 1 or not isinstance(value["attempt"], str) or not ID.fullmatch(value["attempt"]):
        fail("invalid_migration")
    source = value["source"]
    if not isinstance(source, dict) or set(source) != {"generation", "head"}:
        fail("invalid_migration")
    if type(source["generation"]) is not int or not 0 <= source["generation"] <= 9_007_199_254_740_991:
        fail("invalid_migration")
    if not isinstance(source["head"], str) or not DIGEST.fullmatch(source["head"]):
        fail("invalid_migration")
    backup = value["backup"]
    if not isinstance(backup, dict) or set(backup) != {"id", "digest"}:
        fail("invalid_migration")
    if not isinstance(backup["id"], str) or not 1 <= len(backup["id"]) <= 256:
        fail("invalid_migration")
    if not isinstance(backup["digest"], str) or not DIGEST.fullmatch(backup["digest"]):
        fail("invalid_migration")
    if value["ownerProfile"] != owner or not isinstance(value["ownerHead"], str) or not DIGEST.fullmatch(value["ownerHead"]):
        fail("invalid_migration")


def validate_directory(document: object, require_migration: bool = False) -> dict:
    if not isinstance(document, dict):
        fail("invalid_document")
    if document.get("format") != 4 or document.get("kind") != "profiles" or document.get("schema") != 1:
        fail("invalid_document")
    owner = document.get("owner")
    profiles = document.get("profiles")
    if not isinstance(owner, str) or not ID.fullmatch(owner) or not isinstance(profiles, dict) or not profiles:
        fail("invalid_document")
    if require_migration and "migration" not in document:
        fail("migration_missing")

    live_keys: set[bytes] = set()
    owner_roles = 0
    for map_id, entry in profiles.items():
        if not isinstance(map_id, str) or not ID.fullmatch(map_id) or not isinstance(entry, dict):
            fail("invalid_profile")
        if entry.get("id") != map_id:
            fail("profile_id_mismatch")
        role = entry.get("role")
        if role not in ("owner", "adult", "kid"):
            fail("invalid_role")
        owner_roles += role == "owner"

        display = stamped(entry.get("display"))["value"]
        if not isinstance(display, dict) or not isinstance(display.get("name"), str):
            fail("invalid_display")
        name = display["name"]
        if not name or len(name) > 64:
            fail("invalid_display")
        if unicodedata.normalize("NFC", name) != name:
            fail("display_not_nfc")
        if "avatar" in display and (not isinstance(display["avatar"], str) or not AVATAR.fullmatch(display["avatar"])):
            fail("invalid_avatar")

        library = stamped(entry.get("library"))["value"]
        if not isinstance(library, dict) or set(library) != {"keyVersion", "key"}:
            fail("invalid_library")
        if not isinstance(library["keyVersion"], int) or not 1 <= library["keyVersion"] <= 4_294_967_295:
            fail("invalid_key_version")
        try:
            raw_key = base64.b64decode(library["key"], validate=True)
        except (ValueError, TypeError):
            fail("invalid_library_key")
        if len(raw_key) != 32 or base64.b64encode(raw_key).decode() != library["key"]:
            fail("invalid_library_key")

        validate_policy(stamped(entry.get("policy"))["value"])
        deleted = stamped(entry.get("deleted"))["value"]
        if not isinstance(deleted, bool):
            fail("invalid_deleted")
        if not deleted:
            if raw_key in live_keys:
                fail("duplicate_library_key")
            live_keys.add(raw_key)

    if owner not in profiles:
        fail("owner_missing")
    if profiles[owner].get("role") != "owner":
        fail("owner_role")
    if profiles[owner]["deleted"]["value"]:
        fail("owner_deleted")
    if owner_roles != 1:
        fail("multiple_owners")
    if "migration" in document:
        validate_migration(document["migration"], owner)
    return document


def validate_selection(marker: object) -> None:
    if not isinstance(marker, dict) or marker.get("version") != 1:
        fail("invalid_selection")
    if marker.get("mode") == "persistent" and set(marker) == {"version", "mode", "profileId"}:
        if isinstance(marker["profileId"], str) and ID.fullmatch(marker["profileId"]):
            return
    if marker.get("mode") == "visitor" and set(marker) == {"version", "mode", "visitorNamespace"}:
        if isinstance(marker["visitorNamespace"], str) and ID.fullmatch(marker["visitorNamespace"]):
            return
    fail("invalid_selection")


def validate_approval(document: object, directory: dict, logical_name: str) -> dict:
    if not isinstance(document, dict):
        fail("invalid_approval_document")
    if document.get("format") != 4 or document.get("kind") != "kid-title-approvals" or document.get("schema") != 1:
        fail("invalid_approval_document")
    kid = document.get("kid")
    approvals = document.get("approvals")
    if not isinstance(kid, str) or not ID.fullmatch(kid) or not isinstance(approvals, dict):
        fail("invalid_approval_document")
    if logical_name != f"kid-title-approvals:{kid}":
        fail("approval_name_mismatch")
    entry = directory["profiles"].get(kid)
    if entry is None or entry.get("role") != "kid" or entry["deleted"]["value"]:
        fail("approval_not_kid")
    for title_key, approval in approvals.items():
        match = TITLE_KEY.fullmatch(title_key) if isinstance(title_key, str) else None
        if match is None or int(match.group(1)) > 4_294_967_295:
            fail("invalid_title_key")
        if not isinstance(approval, dict) or not {"value", "at"} <= set(approval):
            fail("invalid_approval")
        validate_stamp(approval["at"])
        if approval["value"] not in ("allow", "tombstone"):
            fail("invalid_approval")
    return document


def mutate_pointer(document: dict, mutation: dict) -> None:
    parts = mutation["path"].lstrip("/").split("/")
    target = document
    for part in parts[:-1]:
        target = target[part]
    if "rename" in mutation:
        target[mutation["rename"]] = target.pop(parts[-1])
    else:
        target[parts[-1]] = mutation["value"]


def remove_pointer(document: dict, pointer: str) -> None:
    parts = pointer.lstrip("/").split("/")
    target = document
    for part in parts[:-1]:
        target = target[part]
    del target[parts[-1]]


def merge_field(field: str, a: dict, b: dict) -> dict:
    if field == "deleted" and (a["value"] is True or b["value"] is True):
        true_values = [item for item in (a, b) if item["value"] is True]
        return max(true_values, key=lambda item: tuple(item["at"]))
    if field == "library":
        return max((a, b), key=lambda item: (item["value"]["keyVersion"], tuple(item["at"])))
    return max((a, b), key=lambda item: tuple(item["at"]))


def merge_directory(a: dict, b: dict) -> dict:
    validate_directory(a, require_migration=True)
    validate_directory(b, require_migration=True)
    if a["owner"] != b["owner"] or a["migration"] != b["migration"]:
        fail("identity_conflict")
    result = copy.deepcopy(max((a, b), key=canonical_json_bytes))
    result["owner"] = a["owner"]
    result["migration"] = copy.deepcopy(a["migration"])
    result["profiles"] = {}
    for profile_id in set(a["profiles"]) | set(b["profiles"]):
        if profile_id not in a["profiles"]:
            result["profiles"][profile_id] = copy.deepcopy(b["profiles"][profile_id])
            continue
        if profile_id not in b["profiles"]:
            result["profiles"][profile_id] = copy.deepcopy(a["profiles"][profile_id])
            continue
        left = a["profiles"][profile_id]
        right = b["profiles"][profile_id]
        if left["id"] != right["id"] or left["role"] != right["role"]:
            fail("identity_conflict")
        entry = copy.deepcopy(max((left, right), key=canonical_json_bytes))
        entry["id"] = left["id"]
        entry["role"] = left["role"]
        for field in ("display", "library", "policy", "deleted"):
            entry[field] = copy.deepcopy(merge_field(field, left[field], right[field]))
        result["profiles"][profile_id] = entry
    return validate_directory(result, require_migration=True)


def merge_approval(a: dict, b: dict) -> dict:
    def rank(item: dict) -> tuple:
        # Tombstone is the conservative winner for the otherwise impossible equal-stamp conflict.
        return (tuple(item["at"]), item["value"] == "tombstone", canonical_json_bytes(item))

    return max((a, b), key=rank)


def apply_directory_operation(document: dict, case: dict) -> dict:
    result = copy.deepcopy(document)
    profile_id = case["profile"]
    operation = case["operation"]
    if operation == "create":
        if profile_id in result["profiles"]:
            fail("profile_exists")
        entry = copy.deepcopy(case["entry"])
        if (not isinstance(entry, dict) or entry.get("id") != profile_id
                or entry.get("role") != "kid"):
            fail("invalid_create_profile")
        policy = entry.get("policy")
        policy_value = policy.get("value") if isinstance(policy, dict) else None
        maximum_age = policy_value.get("maximumAge") if isinstance(policy_value, dict) else None
        if type(maximum_age) is not int or not 0 <= maximum_age <= 18:
            fail("invalid_create_policy")
        # Kid creation is conservative by construction. A future PIN-gated policy operation may loosen this;
        # creation input cannot.
        policy_value["unknownRatings"] = "block"
        result["profiles"][profile_id] = entry
    else:
        entry = result["profiles"].get(profile_id)
        if entry is None or entry["deleted"]["value"]:
            fail("profile_not_live")
        if operation == "rename":
            entry["display"] = {"value": copy.deepcopy(case["value"]), "at": case["at"]}
        elif operation == "policy":
            entry["policy"] = {"value": copy.deepcopy(case["value"]), "at": case["at"]}
        elif operation == "delete":
            if profile_id == result["owner"]:
                fail("owner_deleted")
            entry["deleted"] = {"value": True, "at": case["at"]}
        else:
            fail("invalid_operation")
    return validate_directory(result, require_migration=True)


def create_journal_entry(value: object) -> tuple[str, str, dict]:
    if not isinstance(value, dict) or set(value) != {
            "version", "profile", "key", "display", "libraryAt", "maximumAge", "policyAt", "deletedAt"}:
        fail("invalid_create_journal")
    profile_id = value["profile"]
    key = value["key"]
    if value["version"] != 1 or not isinstance(profile_id, str) or not ID.fullmatch(profile_id):
        fail("invalid_create_journal")
    try:
        raw_key = base64.b64decode(key, validate=True)
    except (ValueError, TypeError):
        fail("invalid_create_journal")
    if len(raw_key) != 32 or base64.b64encode(raw_key).decode() != key:
        fail("invalid_create_journal")
    maximum_age = value["maximumAge"]
    if type(maximum_age) is not int or not 0 <= maximum_age <= 18:
        fail("invalid_create_journal")
    display = value["display"]
    if not isinstance(display, dict) or set(display) != {"value", "at"}:
        fail("invalid_create_journal")
    validate_stamp(display["at"])
    display_value = display["value"]
    if not isinstance(display_value, dict) or not isinstance(display_value.get("name"), str):
        fail("invalid_create_journal")
    name = display_value["name"]
    if not name or len(name) > 64 or unicodedata.normalize("NFC", name) != name:
        fail("invalid_create_journal")
    if "avatar" in display_value and (
            not isinstance(display_value["avatar"], str) or not AVATAR.fullmatch(display_value["avatar"])):
        fail("invalid_create_journal")
    for field in ("libraryAt", "policyAt", "deletedAt"):
        validate_stamp(value[field])
    entry = {
        "id": profile_id,
        "role": "kid",
        "display": copy.deepcopy(display),
        "library": {"value": {"keyVersion": 1, "key": key}, "at": copy.deepcopy(value["libraryAt"])},
        "policy": {"value": {"maximumAge": maximum_age, "unknownRatings": "block"},
                   "at": copy.deepcopy(value["policyAt"])},
        "deleted": {"value": False, "at": copy.deepcopy(value["deletedAt"])},
    }
    return profile_id, key, entry


def create_authority(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"base", "generation"}
            or type(value["base"]) is not int or not isinstance(value["generation"], str)
            or not 0 <= value["base"] <= SAFE_INTEGER
            or not value["generation"] or value["generation"] != value["generation"].strip()
            or len(value["generation"]) > 256):
        fail("invalid_create_authority")
    return value


def profiles_create_next(value: object) -> dict:
    """Plan crash-safe Kid creation from durable journal and authenticated directory observations."""
    if not isinstance(value, dict):
        fail("invalid_create_evidence")
    state = value.get("state")
    if state not in {
            "idle", "journaled", "library-created", "ready", "committed", "conflict",
            "cas-unknown", "cancelling", "active"}:
        fail("invalid_create_state")
    for field in ("explicitIntent", "householdFresh", "candidateRetired"):
        if field in value and type(value[field]) is not bool:
            fail("invalid_create_evidence")
    if state == "idle" and not value.get("explicitIntent", False):
        return {"effect": "no-op"}

    profile_id, key, entry = create_journal_entry(value.get("journal"))
    if state == "idle":
        return {"effect": "persist-journal"}
    if state == "journaled":
        return {"effect": "create-profile-library", "profile": profile_id, "key": key}
    if state == "library-created":
        return {"effect": "apply-create", "profile": profile_id, "entry": entry}
    candidate = value.get("candidate")
    try:
        validate_directory(candidate, require_migration=True)
    except ValueError:
        fail("invalid_create_candidate")
    candidate_entry = candidate["profiles"].get(profile_id)
    if (not isinstance(candidate_entry, dict)
            or canonical_json(candidate_entry) != canonical_json(entry)):
        fail("create_candidate_mismatch")

    def cas(directory: dict, authority: object) -> dict:
        observed = create_authority(authority)
        return {"effect": "cas-directory", "base": observed["base"],
                "generation": observed["generation"], "directory": directory}

    if state == "ready":
        return cas(candidate, value.get("authority"))

    if not value.get("householdFresh", False):
        return {"effect": "read-directory"}
    current = value.get("current")
    try:
        validate_directory(current, require_migration=True)
    except ValueError:
        fail("invalid_create_current")
    authority = create_authority(value.get("authority"))
    try:
        merged = merge_directory(candidate, current)
    except ValueError:
        fail("create_directory_conflict")
    candidate_won = canonical_json(merged) == canonical_json(current)
    current_keys = {
        item["library"]["value"]["key"]
        for item in current["profiles"].values()
        if item["deleted"]["value"] is False
    }
    if value.get("candidateRetired", False) and key in current_keys:
        fail("create_key_referenced")
    authoritative = current["profiles"].get(profile_id)

    def retire_displaced_candidate() -> dict | None:
        displaced = (authoritative is not None
                     and (authoritative["deleted"]["value"] is True
                          or authoritative["library"]["value"]["key"] != key))
        if (displaced and key not in current_keys
                and not value.get("candidateRetired", False)):
            return {"effect": "retire-profile-library", "profile": profile_id, "key": key}
        return None

    if state == "cancelling":
        if profile_id in current["profiles"]:
            if not candidate_won:
                fail("create_directory_conflict")
            retirement = retire_displaced_candidate()
            if retirement is not None:
                return retirement
            if authoritative["deleted"]["value"] is True:
                return {"effect": "discard-journal"}
            return {"effect": "adopt", "directory": current}
        if key in current_keys:
            fail("create_key_referenced")
        if not value.get("candidateRetired", False):
            return {"effect": "retire-profile-library", "profile": profile_id, "key": key}
        return {"effect": "discard-journal"}

    if candidate_won:
        retirement = retire_displaced_candidate()
        if retirement is not None:
            return retirement
        if authoritative is not None and authoritative["deleted"]["value"] is True:
            return {"effect": "discard-journal"}
        if state == "active":
            return {"effect": "discard-journal"}
        return {"effect": "adopt", "directory": current}
    if state in {"committed", "active"}:
        fail("create_not_committed")
    return cas(merged, authority)


def canonical_json_bytes(value: object) -> bytes:
    # Fixtures use only integers, strings, booleans and null, whose JCS form is this compact sorted JSON form.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def merge_unknown_set(a_stamp: object, a_unknown: dict, b_stamp: object, b_unknown: dict) -> dict:
    def rank(stamp: object, unknown: dict) -> tuple:
        has_stamp = stamp is not None
        return (has_stamp, tuple(stamp) if has_stamp else (), bool(unknown), canonical_json_bytes(unknown))

    return max(((a_stamp, a_unknown), (b_stamp, b_unknown)), key=lambda item: rank(*item))[1]


def policy_allows(policy: dict, minimum_age: object, approved: bool = False) -> bool:
    if minimum_age is not None and (type(minimum_age) is not int or not 0 <= minimum_age <= 18):
        fail("invalid_minimum_age")
    if approved:
        return True
    if minimum_age is None:
        return policy["unknownRatings"] == "allow"
    maximum_age = policy["maximumAge"]
    return maximum_age is None or minimum_age <= maximum_age


def kid_discovery_relevant(policy: dict, made_for_children: object, made_for_teens: object) -> bool:
    for score in (made_for_children, made_for_teens):
        if score is not None and (type(score) not in (int, float) or not 0 <= score <= 1):
            fail("invalid_audience_score")
    if made_for_children is not None and made_for_children >= 0.5:
        return True
    maximum_age = policy["maximumAge"]
    teen_band = maximum_age is None or maximum_age >= 12
    return teen_band and made_for_teens is not None and made_for_teens >= 0.5


def policy_relation(a: dict, b: dict) -> str:
    def components(policy: dict) -> tuple[int, int]:
        age = policy["maximumAge"] if policy["maximumAge"] is not None else 19
        unknown = 0 if policy["unknownRatings"] == "block" else 1
        return age, unknown

    left = components(a)
    right = components(b)
    if left == right:
        return "equal"
    if all(x <= y for x, y in zip(left, right)):
        return "stricter"
    if all(x >= y for x, y in zip(left, right)):
        return "looser"
    return "incomparable"


def selection_decision(marker: object, directory: dict) -> dict:
    restricted = any(
        not entry["deleted"]["value"]
        and (entry["policy"]["value"]["maximumAge"] is not None
             or entry["policy"]["value"]["unknownRatings"] == "block")
        for entry in directory["profiles"].values()
    )
    try:
        validate_selection(marker)
    except ValueError:
        return {"action": "picker", "ownerGate": restricted}
    if marker["mode"] == "visitor":
        return {"action": "openVisitor", "visitorNamespace": marker["visitorNamespace"]}
    profile_id = marker["profileId"]
    entry = directory["profiles"].get(profile_id)
    if entry is None or entry["deleted"]["value"] or entry["role"] not in ("owner", "kid", "adult"):
        return {"action": "picker", "ownerGate": restricted}
    return {"action": "open", "profileId": profile_id}


def library_id(value: object) -> str:
    key = base64.b64decode(validate_candidate_key(value), validate=True)
    extract = hmac.new(b"den/library/salt/v1", key, hashlib.sha256).digest()
    expanded = hmac.new(extract, b"den/library/id/v1\x01", hashlib.sha256).digest()
    return expanded[:16].hex()


def validate_reset(case: dict, directory: dict, fence: dict) -> dict:
    validate_directory(directory, require_migration=True)
    if (not isinstance(fence, dict) or set(fence) != {"generation", "head"}
            or not isinstance(fence["generation"], int) or isinstance(fence["generation"], bool)
            or not 0 <= fence["generation"] <= 9007199254740991
            or not isinstance(fence["head"], str) or not DIGEST.fullmatch(fence["head"])):
        fail("invalid_reset_fence")
    profiles = case.get("profiles")
    if not isinstance(profiles, list):
        fail("invalid_reset_profiles")
    live = {profile_id: entry for profile_id, entry in directory["profiles"].items()
            if not entry["deleted"]["value"]}
    evidence = {}
    for profile in profiles:
        if not isinstance(profile, dict) or not isinstance(profile.get("profile"), str):
            fail("invalid_reset_profiles")
        profile_id = profile["profile"]
        if profile_id in evidence:
            fail("invalid_reset_profiles")
        evidence[profile_id] = profile
    if set(evidence) != set(live):
        fail("invalid_reset_profiles")

    old_keys = {entry["library"]["value"]["key"] for entry in live.values()}
    new_keys = set()
    pending = []
    unretired = []
    for profile_id in sorted(live):
        profile = evidence[profile_id]
        current = live[profile_id]["library"]["value"]
        if profile.get("oldKey") != current["key"] or profile.get("oldVersion") != current["keyVersion"]:
            fail("profile_state_mismatch")
        if profile.get("newVersion") != current["keyVersion"] + 1:
            fail("invalid_key_version")
        key = profile.get("newKey")
        try:
            raw_key = base64.b64decode(key, validate=True)
        except (TypeError, ValueError):
            fail("invalid_library_key")
        if len(raw_key) != 32 or base64.b64encode(raw_key).decode() != key:
            fail("invalid_library_key")
        if key in old_keys or key in new_keys:
            fail("reused_key")
        new_keys.add(key)
        if not isinstance(profile.get("verified"), bool) or not isinstance(profile.get("oldRetired"), bool):
            fail("invalid_reset_profiles")
        copied_head = profile.get("copiedHead")
        if profile["verified"]:
            if not isinstance(copied_head, str) or not DIGEST.fullmatch(copied_head):
                fail("invalid_copy_head")
        elif copied_head is not None:
            fail("invalid_copy_head")
        if not profile["verified"]:
            pending.append(profile_id)
        if not profile["oldRetired"]:
            unretired.append(profile_id)

    if not isinstance(case.get("householdCommitted"), bool) or not isinstance(case.get("oldHouseholdRetired"), bool):
        fail("invalid_reset_profiles")
    if not case["householdCommitted"] and (case["oldHouseholdRetired"] or unretired != sorted(live)):
        fail("premature_retirement")
    if case["householdCommitted"] and pending:
        fail("commit_without_verification")
    if pending:
        return {"effect": "move-live-profiles", "profiles": pending}
    if not case["householdCommitted"]:
        return {"effect": "commit-successor-household"}
    if unretired or not case["oldHouseholdRetired"]:
        return {"effect": "retire-old-libraries", "profiles": unretired,
                "household": not case["oldHouseholdRetired"]}
    return {"effect": "complete"}


def reset_lock_error(status: int, error: str) -> dict:
    return {"effect": "refused", "status": status, "error": error, "sideEffects": []}


def validate_snapshot_source(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"generation", "head"}
            or type(value["generation"]) is not int
            or not 0 <= value["generation"] <= SAFE_INTEGER
            or not isinstance(value["head"], str) or not DIGEST.fullmatch(value["head"])):
        fail("invalid_migration_source")
    return value


def reset_lock_step(world: dict, request: dict) -> dict:
    """Pure model of the wire/profiles-v1.md section 9.1 lock set across several libraries."""
    libraries = world["libraries"]
    kind = request.get("kind")
    library_id_ = request.get("library")
    if library_id_ not in libraries:
        fail("unknown_library")
    library = libraries[library_id_]
    lock = library["lock"]
    presenter = request.get("attempt")
    if kind in ("retire", "abort", "commit", "lookup") and presenter is not None and not (
            isinstance(presenter, str) and ID.fullmatch(presenter)):
        fail("bad_request")

    live_phases = ("open", "committed", "retiring")

    def held(record: object) -> bool:
        """A lock in force: a household lock in a live phase, or a member lock whose household is live."""
        if record is None:
            return False
        if record["role"] == "household":
            return record["phase"] in live_phases
        house = household_lock(record)
        return (record["phase"] == "open" and house is not None
                and house["attempt"] == record["attempt"] and house["phase"] in live_phases)

    def household_lock(record: dict) -> object:
        return libraries.get(record["household"], {}).get("lock")

    def may_act(record: dict) -> bool:
        if presenter is None:
            return False
        if record["role"] == "household":
            return record["owner"] == presenter
        house = household_lock(record)
        return (house is not None and house["attempt"] == record["attempt"]
                and house["owner"] == presenter)

    conflict = reset_lock_error(409, "migration_in_progress")
    gone = reset_lock_error(410, "library_moved")

    if kind in ("acquire", "takeover"):
        if (not isinstance(presenter, str) or not ID.fullmatch(presenter)
                or request.get("purpose") != ("key-reset" if kind == "acquire" else "key-reset-recovery")
                or request.get("role") not in ("household", "profile")):
            fail("invalid_exclusive_request")
        expected = validate_snapshot_source(request.get("expectedSource"))
        if kind == "takeover" and request["role"] != "household":
            return conflict
        if kind == "acquire":
            if not isinstance(request.get("successor"), str) or not ID.fullmatch(request["successor"]):
                fail("invalid_exclusive_request")
            if request["role"] == "household":
                members = request.get("members")
                ids = [] if not isinstance(members, list) else [m.get("libraryId") for m in members]
                successors = [] if not isinstance(members, list) else [m.get("successor") for m in members]
                everything = [library_id_, *ids, request["successor"], *successors]
                if (not ids or ids != sorted(ids)
                        or not all(isinstance(v, str) and ID.fullmatch(v) for v in everything)
                        or len(set(everything)) != len(everything)):
                    fail("bad_request")
            elif not isinstance(request.get("household"), str) or not ID.fullmatch(request["household"]):
                fail("invalid_exclusive_request")
        if lock is not None:
            if kind == "acquire" and lock["attempt"] == presenter:
                if not (lock["role"] == request["role"] and lock["source"] == expected
                        and lock["purpose"] == "key-reset"
                        and lock["successor"] == request["successor"]
                        and lock.get("members") == request.get("members")
                        and lock.get("household") == request.get("household")):
                    fail("migration_attempt_mismatch")
                return {"effect": "lookup"}
            if kind == "takeover":
                if lock["attempt"] == presenter:
                    fail("migration_attempt_mismatch")
                if lock.get("owner") == presenter:
                    if lock["source"] != expected:
                        fail("migration_attempt_mismatch")
                    return {"effect": "lookup"}
        if library["retired"]:
            return gone
        if held(lock):
            if kind == "acquire" or lock["purpose"] != "key-reset" or lock["role"] != "household":
                return conflict
        elif kind == "takeover":
            return conflict
        if library["role"] != request["role"]:
            fail("role_mismatch")
        if kind == "acquire" and request["role"] == "profile":
            house = libraries.get(request["household"], {}).get("lock")
            if (house is None or house["purpose"] != "key-reset" or house["phase"] != "open"
                    or house["attempt"] != presenter or house["owner"] != presenter
                    or {"libraryId": library_id_, "successor": request["successor"]}
                    not in house["members"]):
                return conflict
        if kind == "takeover":
            if not (expected == lock["source"] == library["source"]):
                return conflict
            proof = request.get("successorCredential")
            if proof is not None:
                successor = libraries.get(lock["successor"])
                if successor is None or successor["retired"] or successor.get("credential") != proof:
                    return conflict
            if proof is None and lock["phase"] == "retiring":
                return conflict
            if proof is None and lock["phase"] == "committed":
                return {"effect": "rollback", "phase": "committed", "sideEffects": ["household-aborted"]}
            return {"effect": "takeover", "phase": lock["phase"], "sideEffects": ["owner-replaced"]}
        if library["source"] != expected:
            return reset_lock_error(409, "migration_source_changed")
        if request["role"] == "household":
            declared = [request["successor"], *[m["successor"] for m in request["members"]]]
            existing = [s for s in declared if s in libraries and libraries[s].get("exists", True)]
            return {"effect": "open", "sideEffects": ["lock"], "preexisting": existing}
        return {"effect": "open", "sideEffects": ["lock"]}

    if kind == "commit":
        if (lock is not None and lock["role"] == "household" and lock["attempt"] == presenter
                and lock["owner"] == presenter and lock["phase"] == "committed"):
            if request.get("successor") != lock["successor"]:
                fail("migration_attempt_mismatch")
            return {"effect": "lookup"}
        if (lock is None or lock["role"] != "household" or lock["phase"] != "open"
                or lock["attempt"] != presenter or lock["owner"] != presenter):
            return conflict
        if request.get("successor") != lock["successor"]:
            fail("migration_attempt_mismatch")
        for member in lock["members"]:
            member_lock = libraries[member["libraryId"]]["lock"]
            if (member_lock is None or member_lock["attempt"] != presenter
                    or member_lock["phase"] != "open"):
                return reset_lock_error(409, "locks_incomplete")
        successor = libraries.get(lock["successor"])
        if (successor is None or not successor.get("exists", True) or successor["retired"]
                or successor["wireMin"] < 5 or successor["rows"] < 1):
            return reset_lock_error(409, "successor_not_ready")
        return {"effect": "committed", "sideEffects": ["phase-committed"]}

    if kind == "abort":
        if library["retired"]:
            return gone
        if lock is None or lock["role"] != "household":
            return reset_lock_error(404, "not_found")
        if lock["phase"] == "aborted" and presenter in (lock["attempt"], lock.get("owner")):
            return {"effect": "lookup"}
        if not held(lock) or lock["owner"] != presenter or lock["phase"] != "open":
            return conflict
        return {"effect": "aborted", "sideEffects": ["household-aborted"]}

    if kind == "write":
        if library["retired"]:
            return gone
        if held(lock):
            return conflict
        return {"effect": "allowed", "sideEffects": []}

    if kind == "sweep":
        if lock is None or lock["role"] != "household" or lock["phase"] != "aborted" or lock.get("swept"):
            return {"effect": "none", "sideEffects": []}
        declared = [lock["successor"], *[m["successor"] for m in lock["members"]]]
        return {"effect": "sweep", "delete": [s for s in declared if s not in lock.get("preexisting", [])],
                "sideEffects": ["successors-deleted", "household-swept"]}

    if kind == "lookup":
        if lock is None:
            return reset_lock_error(404, "unknown_attempt")
        house = lock if lock["role"] == "household" else household_lock(lock)
        if house is None or presenter not in (lock["attempt"], lock.get("owner")):
            return reset_lock_error(404, "unknown_attempt")
        phase = lock["phase"]
        if lock["role"] == "profile" and house["phase"] == "aborted":
            phase = "aborted"
        return {"effect": "lookup", "state": "committed" if phase == "retiring" else phase}

    if kind == "retire":
        successor = request.get("successor")
        if library["retired"]:
            if lock is not None and lock["phase"] == "retired" and presenter is not None:
                owner = lock.get("owner")
                if lock["role"] == "profile":
                    house = household_lock(lock)
                    owner = house["owner"] if house is not None and house["attempt"] == lock["attempt"] else None
                if presenter in (lock["attempt"], owner) and successor == lock["successor"]:
                    return {"effect": "lookup"}
            return gone
        if lock is not None and lock.get("purpose") != "key-reset" and held(lock):
            return conflict
        if not held(lock):
            if presenter is None:
                return {"effect": "ordinary-delete", "sideEffects": ["library-retired"]}
            return reset_lock_error(409, "lock_not_held")
        if not may_act(lock):
            return conflict
        if successor != lock["successor"]:
            return reset_lock_error(409, "successor_mismatch")
        house = lock if lock["role"] == "household" else household_lock(lock)
        if not house.get("proven", True):
            return conflict
        if house["phase"] not in ("committed", "retiring"):
            return reset_lock_error(409, "successor_not_committed")
        if lock["role"] == "household":
            if house["phase"] != "retiring" or any(
                    (libraries[m["libraryId"]]["lock"] or {}).get("phase") != "retired"
                    for m in lock["members"]):
                return reset_lock_error(409, "locks_held")
            return {"effect": "retired", "sideEffects": ["library-retired", "lock-released"]}
        door = ["household-retiring"] if house["phase"] == "committed" else []
        return {"effect": "retired", "sideEffects": [*door, "library-retired", "lock-released"]}

    fail("invalid_exclusive_request")


def seed_preferences(value: object) -> dict:
    """The preferences a profile library is given from an older household's `set:prefs` (§6.2)."""
    if not isinstance(value, dict) or set(value) != {"household", "profile"}:
        raise ValueError("invalid_preferences")
    household, profile = value["household"], value["profile"]
    if not isinstance(household, dict) or not (profile is None or isinstance(profile, dict)):
        raise ValueError("invalid_preferences")
    held = profile or {}
    return {"values": {key: stamped for key, stamped in household.items()
                       if key != "den.maturityCeiling" and key not in held}}


def household_write(row: object) -> dict:
    """Whether one row may be written to a profile-aware household (profiles-v1 §8)."""
    if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"]:
        raise ValueError("invalid_household_write")
    name = row["name"]
    if name in ("set:prefs", "set:keys"):
        values = row.get("values")
        if not isinstance(values, dict):
            raise ValueError("invalid_household_write")
        if name == "set:prefs":
            return {"effect": "refuse"}
        kept = {key: value for key, value in values.items() if key != "simkl"}
        if len(kept) == len(values):
            return {"effect": "allow"}
        if not kept:
            return {"effect": "refuse"}
        return {"effect": "restrict", "values": kept}
    scoped = name.startswith(("title:", "season:", "dlv:", "set:deliver:", "set:handoff:")) or name in (
        "set:trackers", "set:assistant-applied")
    legacy = name.startswith(("rec:", "wat:", "snt:", "ep:", "set:tracker-event:"))
    return {"effect": "refuse" if scoped or legacy else "allow"}


def household_audit(rows: object) -> dict:
    """The same rule over rows already stored: which to remove, which to rewrite."""
    if not isinstance(rows, list):
        raise ValueError("invalid_household_write")
    seen = set()
    remove = []
    rewrite = []
    for row in rows:
        answer = household_write(row)
        if row["name"] in seen:
            raise ValueError("invalid_household_write")
        seen.add(row["name"])
        if answer["effect"] == "refuse":
            remove.append(row["name"])
        elif answer["effect"] == "restrict":
            rewrite.append({"name": row["name"], "values": answer["values"]})
    return {"remove": remove, "rewrite": rewrite}


def partition_preferences(values: object) -> dict:
    """Partition preferences for the supported browser-local household conversion."""
    if not isinstance(values, dict):
        raise ValueError("invalid_preferences")
    owner = {key: value for key, value in values.items() if key != "den.maturityCeiling"}
    return {"household": {}, "owner": owner, "ownerMaturity": values.get("den.maturityCeiling")}


def main() -> int:
    # Loading both files is part of validation: malformed JSON fails before any vector runs.
    schema = json.loads(SCHEMA.read_text())
    vectors = json.loads(VECTORS.read_text())
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail("unexpected_schema_dialect")
    if vectors.get("schema") != str(SCHEMA.relative_to(ROOT)):
        fail("schema_path_mismatch")
    if vectors.get("directoryName") != "profiles" or vectors.get("approvalNamePrefix") != "kid-title-approvals:":
        fail("document_name_mismatch")

    base = vectors["validDirectories"][0]["document"]
    for case in vectors["validDirectories"]:
        validate_directory(case["document"])

    for case in vectors["invalidDirectories"]:
        candidate = copy.deepcopy(base)
        if "mutate" in case:
            mutate_pointer(candidate, case["mutate"])
        if "remove" in case:
            remove_pointer(candidate, case["remove"])
        try:
            validate_directory(candidate, require_migration=case.get("stored", False))
        except ValueError as error:
            if str(error) != case["error"]:
                fail(f"{case['name']}: wanted {case['error']}, got {error}")
        else:
            fail(f"{case['name']}: invalid directory accepted")

    for case in vectors["mergeCases"]:
        for label in ("a", "b", "ok"):
            candidate = copy.deepcopy(base)
            candidate["profiles"][case["profile"]][case["field"]] = case[label]
            try:
                validate_directory(candidate)
            except ValueError as error:
                fail(f"{case['name']}: {label} is not a valid directory field: {error}")
        actual = merge_field(case["field"], case["a"], case["b"])
        if actual != case["ok"]:
            fail(f"{case['name']}: merge mismatch")
        if merge_field(case["field"], case["b"], case["a"]) != case["ok"]:
            fail(f"{case['name']}: merge is not commutative")

    for case in vectors["directoryOperationCases"]:
        result = apply_directory_operation(base, case)
        entry = result["profiles"][case["profile"]]
        expected = case["expect"]
        actual = {}
        if "role" in expected:
            actual["role"] = entry["role"]
        if "name" in expected:
            actual["name"] = entry["display"]["value"]["name"]
        if "keyVersion" in expected:
            actual["keyVersion"] = entry["library"]["value"]["keyVersion"]
        if "maximumAge" in expected:
            actual["maximumAge"] = entry["policy"]["value"]["maximumAge"]
        if "unknownRatings" in expected:
            actual["unknownRatings"] = entry["policy"]["value"]["unknownRatings"]
        if "deleted" in expected:
            actual["deleted"] = entry["deleted"]["value"]
        if "libraryKeyVersion" in expected:
            actual["libraryKeyVersion"] = entry["library"]["value"]["keyVersion"]
        if actual != expected:
            fail(f"{case['name']}: directory operation mismatch")

    for case in vectors["profilesMaterializeCases"]:
        document = copy.deepcopy(base)
        if "remove" in case:
            remove_pointer(document, case["remove"])
        if "mutation" in case:
            mutate_pointer(document, case["mutation"])
        if "oversizeExtensionBytes" in case:
            document["materializeLimitProbe"] = "x" * case["oversizeExtensionBytes"]
        if "nestedExtensionDepth" in case:
            nested: dict = {}
            document["futureNested"] = nested
            for _ in range(case["nestedExtensionDepth"]):
                child: dict = {}
                nested["next"] = child
                nested = child
        try:
            actual = profiles_materialize(document)
            if case.get("roundTripMutation") == "append-zero":
                plaintext = decode_unpadded_base64url(actual["plaintext"]) + b"\x00"
                try:
                    inflate_document_plaintext(plaintext)
                except ValueError:
                    fail("profiles_materialize_roundtrip")
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if "error" in case:
                fail(f"{case['name']}: invalid profiles materialization accepted")
            jcs = decode_unpadded_base64url(actual["jcs"])
            plaintext = decode_unpadded_base64url(actual["plaintext"])
            if (actual["directory"] != document or len(jcs) != case["expectJcsBytes"]
                    or hashlib.sha256(jcs).hexdigest() != case["expectJcsSha256"]
                    or inflate_document_plaintext(plaintext) != jcs
                    or canonical_json(json.loads(jcs)) != jcs):
                fail(f"{case['name']}: profiles materialization mismatch")

    journal_fixture = vectors["profileCreateJournal"]
    create_profile, _, create_entry = create_journal_entry(journal_fixture)
    candidate = apply_directory_operation(base, {
        "operation": "create", "profile": create_profile, "entry": create_entry,
    })
    for case in vectors["profileCreateCases"]:
        assembled = {key: copy.deepcopy(value) for key, value in case.items()
                     if key not in {"name", "expect", "error", "omitJournal", "omitCurrent",
                                    "current", "journalMutation", "authorityMutation", "candidateMutation"}}
        if not case.get("omitJournal", False):
            assembled["journal"] = copy.deepcopy(journal_fixture)
            if "journalMutation" in case:
                mutate_pointer(assembled["journal"], case["journalMutation"])
        if case["state"] in {"ready", "committed", "conflict", "cas-unknown", "cancelling", "active"}:
            assembled["candidate"] = copy.deepcopy(candidate)
            if "candidateMutation" in case:
                mutate_pointer(assembled["candidate"], case["candidateMutation"])
            assembled["authority"] = copy.deepcopy(vectors["profileCreateAuthority"])
            if "authorityMutation" in case:
                mutate_pointer(assembled["authority"], case["authorityMutation"])
        if not case.get("omitCurrent", False) and "current" in case:
            if case["current"] == "candidate":
                assembled["current"] = copy.deepcopy(candidate)
            elif case["current"] == "base":
                assembled["current"] = copy.deepcopy(base)
            elif case["current"] == "renamed-candidate":
                assembled["current"] = copy.deepcopy(candidate)
                assembled["current"]["profiles"][create_profile]["display"] = {
                    "value": {"name": "Sam Two", "avatar": "fox"},
                    "at": [1800000006000, 0, "0f1e2d3c4b5a6978"],
                }
            elif case["current"] == "rekeyed-candidate":
                assembled["current"] = copy.deepcopy(candidate)
                assembled["current"]["profiles"][create_profile]["library"] = {
                    "value": {
                        "keyVersion": 2,
                        "key": "ZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmZmY=",
                    },
                    "at": [1800000006000, 0, "0f1e2d3c4b5a6978"],
                }
            elif case["current"] == "tombstoned-candidate":
                assembled["current"] = copy.deepcopy(candidate)
                assembled["current"]["profiles"][create_profile]["deleted"] = {
                    "value": True,
                    "at": [1800000006000, 0, "0f1e2d3c4b5a6978"],
                }
            else:
                fail("invalid_create_vector")
        try:
            actual = profiles_create_next(assembled)
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if "error" in case:
                fail(f"{case['name']}: invalid create evidence accepted")
            if actual.get("effect") != case["expect"]:
                fail(f"{case['name']}: wanted {case['expect']}, got {actual}")
            if actual["effect"] == "cas-directory":
                if ({key: actual.get(key) for key in ("base", "generation")}
                        != vectors["profileCreateAuthority"]):
                    fail(f"{case['name']}: CAS authority mismatch")
                validate_directory(actual.get("directory"), require_migration=True)
            if actual["effect"] == "apply-create":
                policy = actual["entry"]["policy"]["value"]
                if policy != {"maximumAge": 8, "unknownRatings": "block"}:
                    fail(f"{case['name']}: unsafe Kid create policy")

    for case in vectors["directoryMergeCases"]:
        left = copy.deepcopy(base)
        right = copy.deepcopy(base)
        for mutation in case["aMutations"]:
            mutate_pointer(left, mutation)
        for mutation in case["bMutations"]:
            mutate_pointer(right, mutation)
        try:
            merged = merge_directory(left, right)
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if "error" in case:
                fail(f"{case['name']}: conflicting directories merged")
            profile = merged["profiles"]["11112222333344445555666677778888"]
            actual = {"name": profile["display"]["value"]["name"],
                      "maximumAge": profile["policy"]["value"]["maximumAge"]}
            if actual != case["expect"]:
                fail(f"{case['name']}: directory merge mismatch")
            if merge_directory(right, left) != merged:
                fail(f"{case['name']}: directory merge is not commutative")

    legacy = {"pg13": 13, "r": 17, None: None}
    for case in vectors["policyCases"]:
        if "legacy" in case:
            actual = {"maximumAge": legacy[case["legacy"]], "unknownRatings": "allow"}
            if actual != case["normalized"]:
                fail(f"{case['name']}: legacy mapping mismatch")
        elif "error" in case:
            try:
                policy_allows(case["policy"], case["minimumAge"], case["approved"])
            except ValueError as error:
                if str(error) != case["error"]:
                    fail(f"{case['name']}: wanted {case['error']}, got {error}")
            else:
                fail(f"{case['name']}: invalid minimum age accepted")
        elif policy_allows(case["policy"], case["minimumAge"], case["approved"]) != case["allow"]:
            fail(f"{case['name']}: policy answer mismatch")

    for case in vectors["policyOrderingCases"]:
        if policy_relation(case["a"], case["b"]) != case["relation"]:
            fail(f"{case['name']}: policy ordering mismatch")

    for case in vectors["settingsPartitionCases"]:
        try:
            actual = partition_preferences(case["values"])
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if "error" in case:
                fail(f"{case['name']}: invalid preferences accepted")
            if actual != case["expect"]:
                fail(f"{case['name']}: preference partition mismatch")

    for key, function, field in (("householdWriteCases", household_write, "row"),
                                 ("householdAuditCases", household_audit, "rows")):
        for case in vectors[key]:
            try:
                actual = function(case[field])
            except ValueError as error:
                if str(error) != case.get("error"):
                    fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
            else:
                if "error" in case:
                    fail(f"{case['name']}: invalid household write accepted")
                if actual != case["expect"]:
                    fail(f"{case['name']}: household write answer mismatch")
    for case in vectors["kidDiscoveryCases"]:
        if "error" in case:
            try:
                kid_discovery_relevant(case["policy"], case.get("madeForChildren"), case.get("madeForTeens"))
            except ValueError as error:
                if str(error) != case["error"]:
                    fail(f"{case['name']}: wanted {case['error']}, got {error}")
            else:
                fail(f"{case['name']}: invalid audience score accepted")
        elif kid_discovery_relevant(
            case["policy"], case.get("madeForChildren"), case.get("madeForTeens")
        ) != case["relevant"]:
            fail(f"{case['name']}: Kid discovery relevance mismatch")
    if vectors["unknownRatingsDefaults"] != {"owner": "allow", "kid": "block"}:
        fail("unknown_ratings_defaults_mismatch")

    approval_base = vectors["validApprovalDocuments"][0]["document"]
    approval_name = vectors["validApprovalDocuments"][0]["logicalName"]
    for case in vectors["validApprovalDocuments"]:
        validate_approval(case["document"], base, case["logicalName"])

    for case in vectors["invalidApprovalDocuments"]:
        candidate = copy.deepcopy(approval_base)
        candidate_directory = copy.deepcopy(base)
        if "mutate" in case:
            mutate_pointer(candidate, case["mutate"])
        if "directoryMutate" in case:
            mutate_pointer(candidate_directory, case["directoryMutate"])
        try:
            validate_approval(candidate, candidate_directory, case.get("logicalName", approval_name))
        except ValueError as error:
            if str(error) != case["error"]:
                fail(f"{case['name']}: wanted {case['error']}, got {error}")
        else:
            fail(f"{case['name']}: invalid approval document accepted")

    for case in vectors["approvalMergeCases"]:
        for label in ("a", "b", "ok"):
            candidate = copy.deepcopy(approval_base)
            candidate["approvals"] = {"movie:550": case[label]}
            validate_approval(candidate, base, approval_name)
        actual = merge_approval(case["a"], case["b"])
        if actual != case["ok"]:
            fail(f"{case['name']}: approval merge mismatch")
        if merge_approval(case["b"], case["a"]) != case["ok"]:
            fail(f"{case['name']}: approval merge is not commutative")
        if merge_approval(case["a"], case["a"]) != case["a"]:
            fail(f"{case['name']}: approval merge is not idempotent")

    approval_values = [case["ok"] for case in vectors["approvalMergeCases"]]
    left = merge_approval(merge_approval(approval_values[0], approval_values[1]), approval_values[2])
    right = merge_approval(approval_values[0], merge_approval(approval_values[1], approval_values[2]))
    if left != right:
        fail("approval merge is not associative")

    for case in vectors["approvalUnknownSetCases"]:
        actual = merge_unknown_set(case["aNewestStamp"], case["aUnknown"], case["bNewestStamp"], case["bUnknown"])
        if actual != case["ok"]:
            fail(f"{case['name']}: approval unknown-set merge mismatch")
        reverse = merge_unknown_set(case["bNewestStamp"], case["bUnknown"], case["aNewestStamp"], case["aUnknown"])
        if reverse != case["ok"]:
            fail(f"{case['name']}: approval unknown-set merge is not commutative")

    for case in vectors["approvalScopeCases"]:
        actual = (
            case["activeMode"] == "kid"
            and case["activeProfile"] == case["documentKid"]
            and case["value"] == "allow"
        )
        if actual != case["approved"]:
            fail(f"{case['name']}: approval scope mismatch")

    for case in vectors["approvalWriterCases"]:
        actual = case["mode"] == "owner"
        if actual != case["mayWrite"]:
            fail("approval writer mismatch")

    for case in vectors["playOnceCases"]:
        if case["allowedForPlayback"] != case["ownerPinVerified"] or case["writesApproval"]:
            fail("play_once_mismatch")

    expected_boundaries = [
        "discovery-before-rank-limit-page",
        "detail-and-deep-link",
        "ambient-trailer",
        "play-and-sources",
        "autoplay-and-binge-next",
        "download-and-offline",
        "companion-mcp-assistant",
        "remote-play",
    ]
    if vectors["enforcementBoundaries"] != expected_boundaries:
        fail("enforcement_boundaries_mismatch")

    for case in vectors["selectionCases"]:
        candidate_directory = copy.deepcopy(base)
        if case.get("allUnrestricted"):
            for entry in candidate_directory["profiles"].values():
                entry["policy"]["value"] = {"maximumAge": None, "unknownRatings": "allow"}
        if "directoryMutation" in case:
            mutate_pointer(candidate_directory, case["directoryMutation"])
        actual = selection_decision(case["marker"], candidate_directory)
        if actual != case["result"]:
            fail(f"{case['name']}: selection mismatch")

    for case in vectors["settingsSeedCases"]:
        try:
            actual = seed_preferences(case["input"])
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if "error" in case:
                fail(f"{case['name']}: invalid seed input accepted")
            if actual != case["expect"]:
                fail(f"{case['name']}: preference seed mismatch")

    for case in vectors["snapshotDigestCases"]:
        canonical, digest = snapshot_digest(case["kind"], case["input"])
        if canonical != case["canonical"] or digest != case["expect"]:
            fail(f"{case['name']}: snapshot digest mismatch")

    for case in vectors["initialDirectoryConstructorCases"]:
        constructor_input = copy.deepcopy(vectors["initialDirectoryConstructorInput"])
        for mutation in case.get("mutations", []):
            mutate_pointer(constructor_input, mutation)
        if "remove" in case:
            remove_pointer(constructor_input, case["remove"])
        try:
            actual = initial_directory_constructor(constructor_input)
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if "error" in case:
                fail(f"{case['name']}: invalid directory construction accepted")
            expected = case["expect"]
            if (not isinstance(expected, dict) or set(expected) != {"directory", "jcs", "plaintext"}
                    or actual["directory"] != expected["directory"]
                    or actual["jcs"] != expected["jcs"]):
                fail(f"{case['name']}: directory construction mismatch")
            jcs = decode_unpadded_base64url(expected["jcs"])
            if jcs != canonical_json(expected["directory"]):
                fail(f"{case['name']}: directory JCS mismatch")
            expected_plaintext = decode_unpadded_base64url(expected["plaintext"])
            actual_plaintext = decode_unpadded_base64url(actual["plaintext"])
            if (inflate_document_plaintext(expected_plaintext) != jcs
                    or inflate_document_plaintext(actual_plaintext) != jcs):
                fail(f"{case['name']}: directory plaintext mismatch")
            if canonical_json(json.loads(jcs)) != jcs:
                fail(f"{case['name']}: directory JCS does not round trip")

    for case in vectors["resetCases"]:
        directory = copy.deepcopy(base)
        if "directoryMutate" in case:
            mutate_pointer(directory, case["directoryMutate"])
        fence = copy.deepcopy(vectors["resetFence"])
        if "fenceMutate" in case:
            mutate_pointer(fence, case["fenceMutate"])
        profiles = copy.deepcopy(vectors["resetProfiles"])
        for mutation in case.get("profileMutations", []):
            mutate_pointer(profiles, mutation)
        if "removeProfile" in case:
            profiles.pop(case["removeProfile"])
        assembled = copy.deepcopy(case)
        assembled["profiles"] = list(profiles.values())
        if "duplicateProfile" in case:
            assembled["profiles"].append(copy.deepcopy(profiles[case["duplicateProfile"]]))
        try:
            actual = validate_reset(assembled, directory, fence)
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if "error" in case:
                fail(f"{case['name']}: invalid reset evidence accepted")
            if actual != case["expect"]:
                fail(f"{case['name']}: reset decision mismatch")

    lock_contract = vectors["resetLockContract"]
    if ({name: lock_contract.get(name) for name in (
            "purpose", "wire", "acquireOrder", "perLibraryLockRequired", "idleExpiry",
            "ordinaryMutationResponse", "readsContinue", "retireReleasesLockAtomically",
            "abortAllowedUntil", "householdRetiredLast", "sweepSkipsPreexistingSuccessors",
            "lookupRendersRetiringAs", "keyResetRetiredBodyNamesSuccessor", "malformedAttemptStatus",
            "retireCarriesGeneration", "recoveryAfterCommit",
            "everyAtomicStepTouchesOneLibrary", "householdRecordIsAuthority", "householdPhases",
            "memberLockVoidWhenHouseholdAborted", "retirementDoor", "successorDeletion", "plannerChange",
    )} != {
        "purpose": "key-reset",
        "wire": {"household": 5, "profile": 4},
        "acquireOrder": ["household", "profiles-ascending-library-id"],
        "perLibraryLockRequired": True,
        "idleExpiry": False,
        "ordinaryMutationResponse": {"status": 409, "error": "migration_in_progress"},
        "readsContinue": True,
        "retireReleasesLockAtomically": True,
        "abortAllowedUntil": "successor-commit",
        "householdRetiredLast": True,
        "sweepSkipsPreexistingSuccessors": True,
        "lookupRendersRetiringAs": "committed",
        "keyResetRetiredBodyNamesSuccessor": False,
        "malformedAttemptStatus": 400,
        "retireCarriesGeneration": True,
        "recoveryAfterCommit": {
            "withSuccessorProof": "finish-retirement",
            "withoutProofBeforeDoor": "rollback",
            "withoutProofAfterDoor": {"status": 409, "error": "migration_in_progress"},
            "wrongProof": {"status": 409, "error": "migration_in_progress"},
        },
        "everyAtomicStepTouchesOneLibrary": True,
        "householdRecordIsAuthority": True,
        "householdPhases": ["open", "committed", "retiring", "retired", "aborted"],
        "memberLockVoidWhenHouseholdAborted": True,
        "retirementDoor": "household-committed-to-retiring-before-first-member-retire",
        "successorDeletion": {"after": "household-decision", "driver": "den-edge",
                              "perLibraryIdempotent": True, "guarantee": "eventual"},
        "plannerChange": None,
    }):
        fail("reset_lock_contract_mismatch")
    for case in lock_contract["cases"]:
        world = copy.deepcopy(lock_contract["worldBase"])
        for library_key, override in lock_contract["scenarios"][case["scenario"]].items():
            world["libraries"][library_key].update(copy.deepcopy(override))
        for mutation in case.get("mutations", []):
            mutate_pointer(world, mutation)
        try:
            actual = reset_lock_step(world, case["request"])
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if "error" in case:
                fail(f"{case['name']}: invalid reset-lock request accepted")
            if actual != case["expect"]:
                fail(f"{case['name']}: wanted {case['expect']}, got {actual}")

    topology = vectors["wireTopology"]
    expected = {
        "rowEncoding": "library-v4",
        "householdRequestWireAfterActivation": 5,
        "profileRequestWire": 4,
        "newProfileLibraryMinimum": 4,
        "visitorSyncedLibrary": False,
        "visitorPolicy": "inherits-owner-v1",
        "kidApprovalsStoredInHousehold": True,
        "playOnceStoredInHousehold": False,
    }
    if topology != expected:
        fail("wire_topology_mismatch")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, TypeError, ValueError) as error:
        print(f"profiles-v1 validation failed: {error}", file=sys.stderr)
        sys.exit(1)
