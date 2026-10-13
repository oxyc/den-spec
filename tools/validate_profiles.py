#!/usr/bin/env python3
"""Validate profiles-v1 vectors without third-party dependencies."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import re
import sys
import unicodedata
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


def fail(message: str) -> None:
    raise ValueError(message)


def canonical_json(value: object) -> bytes:
    """RFC 8785 bytes for the migration schemas, whose numbers are safe integers only."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def migration_digest(kind: str, value: object) -> tuple[str, str]:
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
    elif kind == "staged":
        if set(value) != {"rows"}:
            fail("invalid_migration_digest")
        normalized = rows(value["rows"], sequenced=False)
        if not normalized:
            fail("invalid_migration_digest")
    elif kind == "backup":
        if set(value) != {"id", "householdId", "attempt", "source", "wireMin", "authorization", "rows"}:
            fail("invalid_migration_digest")
        source = value["source"]
        authorization = value["authorization"]
        if (not isinstance(value["id"], str) or not 1 <= len(value["id"]) <= 256
                or not isinstance(value["attempt"], str) or not ID.fullmatch(value["attempt"])
                or not isinstance(source, dict) or set(source) != {"generation", "base", "head", "rewrite"}
                or not isinstance(source["head"], str) or not DIGEST.fullmatch(source["head"])
                or not isinstance(source["rewrite"], str) or not source["rewrite"]
                or not isinstance(authorization, dict)
                or set(authorization) != {"tokenHash", "memberHash"}
                or not isinstance(authorization["tokenHash"], str)
                or not DIGEST.fullmatch(authorization["tokenHash"])
                or (authorization["memberHash"] is not None
                    and (not isinstance(authorization["memberHash"], str)
                         or not DIGEST.fullmatch(authorization["memberHash"])) )):
            fail("invalid_migration_digest")
        base = safe_integer(source["base"])
        normalized = {
            "id": value["id"],
            "householdId": library_id(value["householdId"]),
            "attempt": value["attempt"],
            "source": {
                "generation": safe_integer(source["generation"]), "base": base,
                "head": source["head"], "rewrite": source["rewrite"],
            },
            "wireMin": safe_integer(value["wireMin"], positive=True),
            "authorization": copy.deepcopy(authorization),
            "rows": rows(value["rows"], sequenced=True, base=base),
        }
    else:
        fail("invalid_migration_digest_kind")
    canonical = canonical_json(normalized)
    return canonical.decode(), hashlib.sha256(canonical).hexdigest()


def validate_stamp(value: object) -> None:
    if not isinstance(value, list) or len(value) != 3:
        fail("invalid_stamp")
    if not all(isinstance(n, int) and 0 <= n <= 9_007_199_254_740_991 for n in value[:2]):
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
        result["profiles"][profile_id] = copy.deepcopy(case["entry"])
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


def validate_migration_fence(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"generation", "head"}
            or type(value["generation"]) is not int
            or not 0 <= value["generation"] <= 9_007_199_254_740_991
            or not isinstance(value["head"], str) or not DIGEST.fullmatch(value["head"])):
        fail("invalid_migration_fence")
    return value


def validate_candidate_key(value: object) -> str:
    if not isinstance(value, str):
        fail("invalid_library_key")
    try:
        raw = base64.b64decode(value, validate=True)
    except (TypeError, ValueError):
        fail("invalid_library_key")
    if len(raw) != 32 or base64.b64encode(raw).decode() != value:
        fail("invalid_library_key")
    return value


def library_id(value: object) -> str:
    key = base64.b64decode(validate_candidate_key(value), validate=True)
    extract = hmac.new(b"den/library/salt/v1", key, hashlib.sha256).digest()
    expanded = hmac.new(extract, b"den/library/id/v1\x01", hashlib.sha256).digest()
    return expanded[:16].hex()


def validate_storage_fence(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"generation", "base", "head", "rewrite"}
            or type(value["generation"]) is not int
            or not 0 <= value["generation"] <= 9_007_199_254_740_991
            or type(value["base"]) is not int or not 0 <= value["base"] <= 9_007_199_254_740_991
            or not isinstance(value["head"], str) or not DIGEST.fullmatch(value["head"])
            or not isinstance(value["rewrite"], str) or not value["rewrite"]
            or value["rewrite"] != value["rewrite"].strip() or len(value["rewrite"]) > 256):
        fail("invalid_storage_fence")
    return value


def validate_authority(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"generation", "base", "head", "kind"}
            or type(value["generation"]) is not int
            or not 0 <= value["generation"] <= 9_007_199_254_740_991
            or type(value["base"]) is not int or not 0 <= value["base"] <= 9_007_199_254_740_991
            or not isinstance(value["head"], str) or not DIGEST.fullmatch(value["head"])
            or value["kind"] not in ("committed", "changed-legacy", "corrupt")):
        fail("invalid_retirement_authority")
    return value


def validate_profiles_row(value: object) -> dict:
    if not isinstance(value, dict) or type(value.get("present")) is not bool:
        fail("invalid_profiles_row_evidence")
    if not value["present"]:
        if set(value) != {"present"}:
            fail("invalid_profiles_row_evidence")
        return value
    if (set(value) != {"present", "seq", "digest"}
            or type(value["seq"]) is not int or not 0 <= value["seq"] <= 9_007_199_254_740_991
            or not isinstance(value["digest"], str) or not DIGEST.fullmatch(value["digest"])):
        fail("invalid_profiles_row_evidence")
    return value


def restore_result(value: object) -> str:
    if not isinstance(value, dict):
        fail("invalid_restore_evidence")
    recovery_attempt = value.get("recoveryAttempt")
    opened = value.get("open")
    backup = value.get("backup")
    result = value.get("result")
    if not isinstance(recovery_attempt, str) or not ID.fullmatch(recovery_attempt):
        fail("invalid_restore_evidence")
    if (not isinstance(backup, dict)
            or set(backup) != {"attempt", "id", "digest", "source", "wireMin", "verified"}
            or not isinstance(backup.get("attempt"), str) or not ID.fullmatch(backup["attempt"])
            or not isinstance(backup.get("id"), str) or not backup["id"]
            or not isinstance(backup.get("digest"), str) or not DIGEST.fullmatch(backup["digest"])
            or type(backup.get("wireMin")) is not int
            or not 1 <= backup["wireMin"] <= 9_007_199_254_740_991
            or type(backup.get("verified")) is not bool):
        fail("invalid_restore_evidence")
    validate_storage_fence(backup["source"])
    if recovery_attempt == backup["attempt"]:
        fail("recovery_attempt_reused")
    if not isinstance(opened, dict) or opened.get("state") != "open":
        fail("restore_attempt_not_open")
    if opened.get("purpose") != "restore":
        fail("invalid_restore_attempt")
    if opened.get("attempt") != recovery_attempt:
        fail("restore_attempt_mismatch")
    opened_fence = validate_storage_fence(opened.get("fence"))
    if (type(opened.get("wireMin")) is not int
            or not 1 <= opened["wireMin"] <= 9_007_199_254_740_991):
        fail("invalid_restore_authority")
    if opened.get("lookupReturnsSameOpen") is not True:
        fail("restore_open_not_durable")
    fence = validate_storage_fence(value.get("fence"))
    wire_min = value.get("wireMin")
    if fence != opened_fence or wire_min != opened["wireMin"]:
        fail("restore_fence_mismatch")
    if value.get("requestMatchesBoundAttempt") is not True:
        fail("migration_attempt_mismatch")
    if (value.get("terminalLookupRetained") is not True
            or value.get("identicalRetryReturnsSameResult") is not True
            or value.get("conflictTerminalState") != "superseded"
            or value.get("unknownResolution") != ["lookup-attempt", "fresh-authenticated-read"]):
        fail("restore_terminal_not_durable")
    if value.get("sameHouseholdId") is not True or value.get("sameHouseholdToken") is not True:
        fail("restore_identity_mismatch")
    if not backup["verified"]:
        fail("backup_not_verified")
    current = validate_storage_fence(value.get("currentFence"))
    current_wire_min = value.get("currentWireMin")
    if (type(current_wire_min) is not int
            or not 1 <= current_wire_min <= 9_007_199_254_740_991):
        fail("invalid_restore_authority")
    profiles_row = validate_profiles_row(value.get("profilesRow"))
    current_profiles_row = validate_profiles_row(value.get("currentProfilesRow"))
    if fence != current or wire_min != current_wire_min or profiles_row != current_profiles_row:
        fail("migration_fence_moved")
    directory_state = value.get("directoryState")
    if directory_state == "valid":
        fail("committed_profiles_present")
    if directory_state not in ("absent", "invalid"):
        fail("invalid_directory_state")
    if value.get("plannerDecision") != "restore-backup":
        fail("recovery_not_authorized")
    if fence["generation"] == 9_007_199_254_740_991:
        fail("migration_generation_exhausted")
    if (not isinstance(result, dict)
            or set(result) != {"attempt", "state", "generation", "base", "head", "wireMin", "backup"}
            or result.get("attempt") != recovery_attempt or result.get("state") != "restored"
            or type(result.get("generation")) is not int
            or result["generation"] != fence["generation"] + 1
            or type(result.get("base")) is not int or result["base"] != backup["source"]["base"]
            or not isinstance(result.get("head"), str) or not DIGEST.fullmatch(result["head"])
            or result.get("backup") != {"id": backup["id"], "digest": backup["digest"]}):
        fail("invalid_restore_generation" if isinstance(result, dict)
             and result.get("generation") != fence["generation"] + 1
             else "invalid_restore_result")
    expected_wire_min = max(wire_min, backup["wireMin"])
    if result.get("wireMin") != expected_wire_min:
        fail("wire_minimum_downgrade")
    return "restored"


def retirement_result(value: object, directory: dict) -> str:
    if not isinstance(value, dict):
        fail("invalid_retirement_evidence")
    source = validate_storage_fence(value.get("source"))
    authority = validate_authority(value.get("authority"))
    current = validate_authority(value.get("currentAuthority"))
    household = value.get("household")
    candidate = value.get("candidate")
    if (not isinstance(household, dict) or set(household) != {"libraryId", "libraryKey"}
            or not isinstance(candidate, dict)
            or set(candidate) != {"libraryId", "libraryKey", "present", "authorized"}):
        fail("invalid_retirement_evidence")
    if (not isinstance(household["libraryId"], str) or not ID.fullmatch(household["libraryId"])
            or library_id(household["libraryKey"]) != household["libraryId"]
            or not isinstance(candidate["libraryId"], str) or not ID.fullmatch(candidate["libraryId"])
            or library_id(candidate["libraryKey"]) != candidate["libraryId"]
            or type(candidate["present"]) is not bool or type(candidate["authorized"]) is not bool):
        fail("invalid_retirement_evidence")
    if not candidate["authorized"]:
        fail("candidate_unauthorized")
    if authority != current or not value.get("profilesRowMatches", False):
        fail("authority_changed")
    if authority["kind"] == "corrupt":
        fail("corrupt_household")

    protected = value.get("protectedLibraryIds")
    if (not isinstance(protected, list) or len(protected) != len(set(protected))
            or not all(isinstance(item, str) and ID.fullmatch(item) for item in protected)):
        fail("incomplete_protection")
    household_id = household["libraryId"]
    protected_keys = {household["libraryKey"]}
    if authority["kind"] == "committed":
        if not value.get("directoryValid", False):
            fail("invalid_profiles")
        validate_directory(directory, require_migration=True)
        live_keys = {
            entry["library"]["value"]["key"]
            for entry in directory["profiles"].values()
            if not entry["deleted"]["value"]
        }
        protected_keys |= live_keys
        expected = {household_id} | {library_id(key) for key in live_keys}
    else:
        if value.get("directoryValid", False):
            fail("invalid_retirement_evidence")
        expected = {household_id}
        source_authority = {name: source[name] for name in ("generation", "base", "head")}
        current_source = {name: authority[name] for name in ("generation", "base", "head")}
        if source_authority == current_source:
            fail("same_legacy_fence")
    if set(protected) != expected:
        fail("incomplete_protection")
    if candidate["libraryId"] in expected or candidate["libraryKey"] in protected_keys:
        fail("protected_library")
    return "retired" if candidate["present"] else "absent"


def migration_next(value: dict) -> str:
    if not isinstance(value, dict):
        fail("invalid_evidence")
    state = value.get("state")
    if state not in {
        "idle", "fenced", "backed-up", "owner-copied", "ready-to-commit",
        "commit-unknown", "adopting", "cleaning-loser", "active", "restore-required",
    }:
        fail("invalid_state")
    observed = value.get("observed")
    if observed not in ("legacy", "committed", "corrupt"):
        fail("invalid_observation")
    for name in (
        "explicitIntent", "backupVerified", "ownerVerified", "winnerVerified", "candidateRetired"
    ):
        if name in value and type(value[name]) is not bool:
            fail("invalid_evidence")

    candidate = validate_candidate_key(value["candidateKey"]) if "candidateKey" in value else None
    candidate_attempt = value.get("candidateAttempt")
    if "candidateAttempt" in value and (
        candidate is None or not isinstance(candidate_attempt, str) or not ID.fullmatch(candidate_attempt)
    ):
        fail("invalid_candidate_attempt")
    retired = value.get("candidateRetired", False)
    if retired and candidate is None:
        fail("invalid_evidence")
    owner_verified = value.get("ownerVerified", False)
    candidate_head = value.get("candidateHead")
    if owner_verified:
        if candidate is None:
            fail("invalid_evidence")
        if not isinstance(candidate_head, str) or not DIGEST.fullmatch(candidate_head):
            fail("invalid_copy_head")
    elif "candidateHead" in value:
        fail("invalid_copy_head")

    has_directory = "directory" in value
    directory = value.get("directory")
    if observed == "committed":
        if not has_directory:
            fail("committed_directory_missing")
        if not isinstance(directory, dict):
            # Match den-core's stored-directory boundary rather than treating an explicit null as absence.
            fail("invalid_profiles")
        validate_directory(directory, require_migration=True)
        if candidate is not None and candidate_attempt is None:
            fail("invalid_candidate_attempt")
        live_keys = {
            entry["library"]["value"]["key"]
            for entry in directory["profiles"].values()
            if not entry["deleted"]["value"]
        }
        if candidate in live_keys:
            if retired:
                fail("committed_key_retired")
            owner = directory["owner"]
            migration = directory["migration"]
            owns_winner = (
                candidate == directory["profiles"][owner]["library"]["value"]["key"]
                and candidate_attempt == migration["attempt"]
                and owner_verified
                and candidate_head == migration["ownerHead"]
            )
            if owns_winner:
                return "activate" if value.get("winnerVerified", False) else "verify-winner"
            if state == "cleaning-loser":
                return "cleanup_targets_winner"
            fail("winner_evidence_mismatch")
        if candidate is not None and not retired:
            return "retire-losing-candidate"
        if not value.get("winnerVerified", False):
            return "verify-winner"
        return "activate"
    if has_directory:
        fail("invalid_evidence")

    if observed == "corrupt":
        return "restore-backup" if value.get("backupVerified", False) else "recovery-blocked"
    if state == "idle":
        if not value.get("explicitIntent", False):
            fail("explicit_intent_required")
        return "acquire-fence"

    fence = validate_migration_fence(value.get("fence"))
    observed_fence = validate_migration_fence(value.get("observedFence"))
    if state == "commit-unknown":
        return "read-household"
    if fence != observed_fence:
        return "retire-losing-candidate" if candidate is not None and not retired else "restart"
    if state in ("backed-up", "owner-copied", "ready-to-commit") and not value.get("backupVerified", False):
        fail("invalid_evidence")
    if state in ("owner-copied", "ready-to-commit") and candidate is None:
        fail("invalid_evidence")
    if state == "ready-to-commit" and not owner_verified:
        return "verify-owner-library"
    answer = {
        "fenced": "create-backup",
        "backed-up": "create-owner-library",
        "owner-copied": "verify-owner-library",
        "ready-to-commit": "commit-fenced-household",
    }.get(state)
    if answer is None:
        fail("invalid_state")
    return answer


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


PROFILE_PREFERENCE_KEYS = {
    "den.excludedGenreIDs",
    "den.excludedLanguages",
    "den.preferredSubtitleLanguage",
    "den.hasExplicitSubtitlePreference",
    "den.shownSubtitleLanguages",
    "den.maxSubtitlesPerLanguage",
    "den.preferredAudioLanguage",
    "den.autoSkipSegments",
    "den.autoplayTrailers",
    "den.hideAnime",
    "den.hideWatched",
    "den.minReleaseYear",
    "den.enabledRatingSources",
    "den.shownWarningCategories",
}


def partition_preferences(values: object) -> dict:
    if not isinstance(values, dict):
        raise ValueError("invalid_preferences")
    household = {}
    owner = {}
    owner_maturity = None
    for key, value in values.items():
        if key in PROFILE_PREFERENCE_KEYS:
            owner[key] = value
        elif key == "den.maturityCeiling":
            owner_maturity = value
        else:
            household[key] = value
    return {"household": household, "owner": owner, "ownerMaturity": owner_maturity}


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

    for case in vectors["migrationDigestCases"]:
        canonical, digest = migration_digest(case["kind"], case["input"])
        if canonical != case["canonical"] or digest != case["expect"]:
            fail(f"{case['name']}: migration digest mismatch")

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

    expected_settings = {
        "prefs.den.watchRegion": "household",
        "prefs.den.myServicePicks": "household",
        "prefs.den.excludedGenreIDs": "profile",
        "prefs.den.excludedLanguages": "profile",
        "prefs.den.preferredSubtitleLanguage": "profile",
        "prefs.den.hasExplicitSubtitlePreference": "profile",
        "prefs.den.shownSubtitleLanguages": "profile",
        "prefs.den.maxSubtitlesPerLanguage": "profile",
        "prefs.den.preferredAudioLanguage": "profile",
        "prefs.den.autoSkipSegments": "profile",
        "prefs.den.autoplayTrailers": "profile",
        "prefs.den.hideAnime": "profile",
        "prefs.den.hideWatched": "profile",
        "prefs.den.minReleaseYear": "profile",
        "prefs.den.enabledRatingSources": "profile",
        "prefs.den.shownWarningCategories": "profile",
    }
    actual_settings = {row["key"]: row["scope"] for row in vectors["settingsOwnership"]}
    if actual_settings != expected_settings or len(vectors["settingsOwnership"]) != 16:
        fail("settings_ownership_exact_keys")

    storage = vectors["migrationStorageContract"]
    expected_routes = {
        "openOrLookup": "POST /lib/{household}/profile-migration",
        "lookup": "GET /lib/{household}/profile-migration/{attempt}",
        "abort": "DELETE /lib/{household}/profile-migration/{attempt}",
        "backupCreate": "POST /lib/{household}/profile-migration/{attempt}/backup",
        "backupRead": "GET /lib/{household}/profile-migration/{attempt}/backup",
        "stageRows": "POST /lib/{household}/profile-migration/{attempt}/rows",
        "commit": "POST /lib/{household}/profile-migration/{attempt}/commit",
        "restore": "POST /lib/{household}/profile-migration/{attempt}/restore",
        "retireCandidate": "POST /lib/{household}/profile-migration/{attempt}/retire-candidate",
    }
    if (storage.get("publicSyncGenerationType") != "opaque-string"
            or storage.get("transactionGenerationType") != "safe-integer"
            or storage.get("sameGenerationNamespace") is not False
            or storage.get("transactionGenerationAllocation")
            != "monotone-per-household-on-visible-mutation"
            or storage.get("routes") != expected_routes):
        fail("migration_storage_surface_mismatch")
    expected_order = [
        "open-fence", "create-backup", "verify-backup", "create-owner-library", "copy-owner",
        "verify-owner", "stage-household", "commit-household", "read-winner", "retire-loser", "activate",
    ]
    if storage.get("requiredOrder") != expected_order:
        fail("migration_storage_order_mismatch")
    opened = storage["open"]
    if (not isinstance(opened.get("attempt"), str) or not ID.fullmatch(opened["attempt"])
            or opened.get("purpose") != "split" or opened.get("state") != "open"
            or opened.get("wireMin") != 4
            or library_id(opened.get("householdKey")) != opened.get("householdId")
            or any(opened.get(name) is not expected for name, expected in {
                "reopenReturnsSameFence": True,
                "survivesClientRestart": True,
                "survivesServerRestart": True,
                "idleExpiry": False,
                "terminalLookupRetained": True,
                "ordinaryWritesRemainAvailable": True,
                "concurrentAttemptsCommitByFence": True,
                "concurrentAttemptsOnSameSnapshotShareGeneration": True,
                "restoreNeverReusesGeneration": True,
            }.items())):
        fail("migration_storage_open_mismatch")
    if opened.get("terminalStates") != ["committed", "restored", "superseded", "aborted"]:
        fail("migration_storage_open_mismatch")
    fence = validate_storage_fence(opened.get("fence"))
    backup = storage["backup"]
    if (not isinstance(backup.get("id"), str) or not backup["id"]
            or not isinstance(backup.get("digest"), str) or not DIGEST.fullmatch(backup["digest"])
            or backup.get("source") != fence or backup.get("readable") is not True
            or backup.get("complete") is not True or backup.get("dryRestoreVerified") is not True
            or backup.get("destinationWritesBeforeVerification") != 0):
        fail("migration_backup_proof_mismatch")
    commit = storage["commit"]
    if (commit.get("sameHouseholdId") is not True or commit.get("sameHouseholdToken") is not True
            or commit.get("source") != fence
            or commit.get("backup") != {"id": backup["id"], "digest": backup["digest"]}
            or not isinstance(commit.get("stagedDigest"), str) or not DIGEST.fullmatch(commit["stagedDigest"])
            or commit.get("wireMin") != 5 or commit.get("atomicRowsAndMinimum") is not True
            or commit.get("terminalResultDurable") is not True):
        fail("migration_commit_proof_mismatch")
    for case in storage["restoreCases"]:
        evidence = copy.deepcopy(storage["restoreBase"])
        for mutation in case.get("mutations", []):
            mutate_pointer(evidence, mutation)
        try:
            actual = restore_result(evidence)
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if actual != case.get("expect"):
                fail(f"{case['name']}: wanted {case.get('expect')}, got {actual}")
    for case in storage["retirementCases"]:
        evidence = copy.deepcopy(storage["retirementBase"])
        for mutation in case.get("mutations", []):
            mutate_pointer(evidence, mutation)
        try:
            actual = retirement_result(evidence, base)
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if actual != case.get("expect"):
                fail(f"{case['name']}: wanted {case.get('expect')}, got {actual}")

    for case in vectors["migrationCases"]:
        assembled = copy.deepcopy(case)
        if case.get("observed") == "committed" and not case.get("omitDirectory", False):
            assembled["directory"] = copy.deepcopy(base)
            if "directoryMutation" in case:
                mutate_pointer(assembled["directory"], case["directoryMutation"])
        if case.get("observed") == "legacy" and case.get("state") != "idle":
            assembled["fence"] = copy.deepcopy(vectors["migrationFence"])
            if not case.get("omitObservedFence", False):
                assembled["observedFence"] = copy.deepcopy(vectors["migrationFence"])
            if "fenceMutation" in case:
                mutate_pointer(assembled["fence"], case["fenceMutation"])
            if "observedFenceMutation" in case:
                mutate_pointer(assembled["observedFence"], case["observedFenceMutation"])
        if case.get("omitState", False):
            assembled.pop("state", None)
        if case.get("omitObserved", False):
            assembled.pop("observed", None)
        try:
            actual = migration_next(assembled)
        except ValueError as error:
            if str(error) != case.get("error"):
                fail(f"{case['name']}: wanted {case.get('error')}, got {error}")
        else:
            if actual != case.get("expect"):
                fail(f"{case['name']}: wanted {case.get('expect')}, got {actual}")

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

    topology = vectors["wireTopology"]
    expected = {
        "rowEncoding": "library-v4",
        "householdRequestWireAfterActivation": 5,
        "profileRequestWire": 4,
        "newProfileLibraryMinimum": 4,
        "existingHouseholdMinimumChangedByContractPublication": False,
        "initialSplitOperationDefined": True,
        "activationAuthorized": False,
        "visitorSyncedLibrary": False,
        "visitorPolicy": "inherits-owner-v1",
        "kidApprovalsStoredInHousehold": True,
        "playOnceStoredInHousehold": False,
        "canaryChangesWireContract": False,
    }
    if topology != expected:
        fail("wire_topology_mismatch")
    print(
        f"profiles-v1: {len(vectors['validDirectories'])} valid, "
        f"{len(vectors['invalidDirectories'])} invalid, "
        f"{len(vectors['mergeCases'])} merge, "
        f"{len(vectors['directoryOperationCases'])} directory operations, "
        f"{len(vectors['directoryMergeCases'])} directory merge, "
        f"{len(vectors['policyCases'])} policy, "
        f"{len(vectors['policyOrderingCases'])} policy ordering, "
        f"{len(vectors['kidDiscoveryCases'])} Kid discovery, "
        f"{len(vectors['validApprovalDocuments'])} approval valid, "
        f"{len(vectors['invalidApprovalDocuments'])} approval invalid, "
        f"{len(vectors['approvalMergeCases'])} approval merge, "
        f"{len(vectors['approvalUnknownSetCases'])} approval unknown set, "
        f"{len(vectors['approvalScopeCases'])} approval scope, "
        f"{len(vectors['approvalWriterCases'])} approval writer, "
        f"{len(vectors['playOnceCases'])} play once, "
        f"{len(vectors['selectionCases'])} selection, "
        f"{len(vectors['settingsPartitionCases'])} settings partition, "
        f"{len(vectors['migrationDigestCases'])} migration digests, "
        f"{len(vectors['migrationStorageContract']['restoreCases'])} storage restore, "
        f"{len(vectors['migrationStorageContract']['retirementCases'])} storage retirement, "
        f"{len(vectors['migrationCases'])} migration, "
        f"{len(vectors['resetCases'])} reset cases passed"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, TypeError, ValueError) as error:
        print(f"profiles-v1 validation failed: {error}", file=sys.stderr)
        sys.exit(1)
