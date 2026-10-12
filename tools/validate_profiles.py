#!/usr/bin/env python3
"""Validate profiles-v1 vectors without third-party dependencies."""

from __future__ import annotations

import base64
import copy
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


def fail(message: str) -> None:
    raise ValueError(message)


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


def validate_directory(document: object) -> dict:
    if not isinstance(document, dict):
        fail("invalid_document")
    if document.get("format") != 4 or document.get("kind") != "profiles" or document.get("schema") != 1:
        fail("invalid_document")
    owner = document.get("owner")
    profiles = document.get("profiles")
    if not isinstance(owner, str) or not ID.fullmatch(owner) or not isinstance(profiles, dict) or not profiles:
        fail("invalid_document")

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


def merge_field(field: str, a: dict, b: dict) -> dict:
    if field == "deleted" and (a["value"] is True or b["value"] is True):
        true_values = [item for item in (a, b) if item["value"] is True]
        return max(true_values, key=lambda item: tuple(item["at"]))
    if field == "library":
        return max((a, b), key=lambda item: (item["value"]["keyVersion"], tuple(item["at"])))
    return max((a, b), key=lambda item: tuple(item["at"]))


def merge_approval(a: dict, b: dict) -> dict:
    def rank(item: dict) -> tuple:
        # Tombstone is the conservative winner for the otherwise impossible equal-stamp conflict.
        return (tuple(item["at"]), item["value"] == "tombstone", canonical_json_bytes(item))

    return max((a, b), key=rank)


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
        mutate_pointer(candidate, case["mutate"])
        try:
            validate_directory(candidate)
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
        if case["marker"] is not None:
            validate_selection(case["marker"])

    topology = vectors["wireTopology"]
    expected = {
        "rowEncoding": "library-v4",
        "householdRequestWireAfterActivation": 5,
        "profileRequestWire": 4,
        "newProfileLibraryMinimum": 4,
        "existingHouseholdMinimumChangedByThisRevision": False,
        "initialSplitOperationDefined": False,
        "visitorSyncedLibrary": False,
        "visitorPolicy": "inherits-owner-v1",
        "kidApprovalsStoredInHousehold": True,
        "playOnceStoredInHousehold": False,
        "canaryChangesWireContract": False,
    }
    if topology != expected:
        fail("wire_topology_mismatch")
    if len(vectors["settingsOwnership"]) != 29:
        fail("settings_ownership_row_count")
    if len({row["key"] for row in vectors["settingsOwnership"]}) != 29:
        fail("duplicate_settings_ownership_key")

    print(
        f"profiles-v1: {len(vectors['validDirectories'])} valid, "
        f"{len(vectors['invalidDirectories'])} invalid, "
        f"{len(vectors['mergeCases'])} merge, "
        f"{len(vectors['policyCases'])} policy, "
        f"{len(vectors['policyOrderingCases'])} policy ordering, "
        f"{len(vectors['validApprovalDocuments'])} approval valid, "
        f"{len(vectors['invalidApprovalDocuments'])} approval invalid, "
        f"{len(vectors['approvalMergeCases'])} approval merge, "
        f"{len(vectors['approvalUnknownSetCases'])} approval unknown set, "
        f"{len(vectors['approvalScopeCases'])} approval scope, "
        f"{len(vectors['approvalWriterCases'])} approval writer, "
        f"{len(vectors['playOnceCases'])} play once, "
        f"{len(vectors['selectionCases'])} selection cases passed"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, TypeError, ValueError) as error:
        print(f"profiles-v1 validation failed: {error}", file=sys.stderr)
        sys.exit(1)
