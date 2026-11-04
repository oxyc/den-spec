#!/usr/bin/env python3
"""Validate den-core-generated library-v4 vector framing with the standard library."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "vectors/library-v4.json"


def fail(reason):
    raise ValueError(f"invalid_library_vectors: {reason}")


def nonempty_text(value):
    return isinstance(value, str) and bool(value)


def validate_stamp(value):
    return (
        isinstance(value, list)
        and len(value) == 3
        and type(value[0]) is int
        and type(value[1]) is int
        and isinstance(value[2], str)
    )


def validate(vectors):
    if not isinstance(vectors, dict) or set(vectors) != {
        "cases", "merge", "notes", "spec", "version"
    }:
        fail("top-level shape")
    if vectors["version"] != 1 or vectors["spec"] != "wire/library-v4.md":
        fail("provenance")
    if not (
        isinstance(vectors["notes"], list)
        and vectors["notes"]
        and all(nonempty_text(note) for note in vectors["notes"])
    ):
        fail("notes")

    cases = vectors["cases"]
    if not isinstance(cases, list) or not cases:
        fail("cases")
    names = set()
    watchlist_removals = 0
    for case in cases:
        if not isinstance(case, dict):
            fail("case shape")
        answers = set(case) & {"ok", "ok_subset", "error"}
        if len(answers) != 1 or set(case) != {
            "name", "section", "request", *answers
        }:
            fail("case outcome")
        name = case["name"]
        if not nonempty_text(name) or name in names:
            fail("case name")
        names.add(name)
        if not nonempty_text(case["section"]):
            fail(f"{name}: section")
        if "error" in case and not nonempty_text(case["error"]):
            fail(f"{name}: error")
        if "ok_subset" in case and not isinstance(case["ok_subset"], dict):
            fail(f"{name}: ok_subset")

        request = case["request"]
        if not isinstance(request, dict) or not nonempty_text(request.get("op")):
            fail(f"{name}: request")
        if request["op"] == "apply_write":
            write = request.get("write")
            if not isinstance(write, dict) or not nonempty_text(write.get("kind")):
                fail(f"{name}: write")
            if write["kind"] == "watchlist_remove":
                if set(write) != {"kind", "at"} or not validate_stamp(write["at"]):
                    fail(f"{name}: watchlist_remove")
                watchlist_removals += 1
    if not watchlist_removals:
        fail("watchlist_remove coverage")

    merges = vectors["merge"]
    if not isinstance(merges, list) or not merges:
        fail("merge cases")
    merge_names = set()
    for merge in merges:
        if not isinstance(merge, dict) or set(merge) != {
            "name", "a", "b", "c", "merged"
        }:
            fail("merge shape")
        name = merge["name"]
        if not nonempty_text(name) or name in merge_names:
            fail("merge name")
        merge_names.add(name)

    return len(cases), watchlist_removals, len(merges)


def main():
    counts = validate(json.loads(VECTORS.read_text()))
    print(
        f"library-v4: {counts[0]} cases "
        f"({counts[1]} watchlist removals), {counts[2]} merges"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
