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
BANDS = ("littleKids", "family", "teen", "mature")


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
    if not isinstance(value, dict) or set(value) != {"maxBand", "unknownRatings"}:
        fail("invalid_policy")
    if value["maxBand"] is not None and value["maxBand"] not in BANDS:
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


def set_pointer(document: dict, pointer: str, value: object) -> None:
    parts = pointer.lstrip("/").split("/")
    target = document
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


def merge_field(field: str, a: dict, b: dict) -> dict:
    if field == "deleted" and (a["value"] is True or b["value"] is True):
        true_values = [item for item in (a, b) if item["value"] is True]
        return max(true_values, key=lambda item: tuple(item["at"]))
    if field == "library":
        return max((a, b), key=lambda item: (item["value"]["keyVersion"], tuple(item["at"])))
    return max((a, b), key=lambda item: tuple(item["at"]))


def policy_allows(policy: dict, title_band: object) -> bool:
    if title_band is None:
        return policy["unknownRatings"] == "allow"
    ceiling = policy["maxBand"]
    return ceiling is None or BANDS.index(title_band) <= BANDS.index(ceiling)


def main() -> int:
    # Loading both files is part of validation: malformed JSON fails before any vector runs.
    schema = json.loads(SCHEMA.read_text())
    vectors = json.loads(VECTORS.read_text())
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail("unexpected_schema_dialect")
    if vectors.get("schema") != str(SCHEMA.relative_to(ROOT)):
        fail("schema_path_mismatch")

    base = vectors["validDirectories"][0]["document"]
    for case in vectors["validDirectories"]:
        validate_directory(case["document"])

    for case in vectors["invalidDirectories"]:
        candidate = copy.deepcopy(base)
        set_pointer(candidate, case["mutate"]["path"], case["mutate"]["value"])
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

    legacy = {"pg13": "teen", "r": "mature", None: None}
    for case in vectors["policyCases"]:
        if "legacy" in case:
            actual = {"maxBand": legacy[case["legacy"]], "unknownRatings": "allow"}
            if actual != case["normalized"]:
                fail(f"{case['name']}: legacy mapping mismatch")
        elif policy_allows(case["policy"], case["titleBand"]) != case["allow"]:
            fail(f"{case['name']}: policy answer mismatch")

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
    }
    if topology != expected:
        fail("wire_topology_mismatch")
    if len(vectors["settingsOwnership"]) != 28:
        fail("settings_ownership_row_count")
    if len({row["key"] for row in vectors["settingsOwnership"]}) != 28:
        fail("duplicate_settings_ownership_key")

    print(
        f"profiles-v1: {len(vectors['validDirectories'])} valid, "
        f"{len(vectors['invalidDirectories'])} invalid, "
        f"{len(vectors['mergeCases'])} merge, "
        f"{len(vectors['policyCases'])} policy, "
        f"{len(vectors['selectionCases'])} selection cases passed"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, TypeError, ValueError) as error:
        print(f"profiles-v1 validation failed: {error}", file=sys.stderr)
        sys.exit(1)
