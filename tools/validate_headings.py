#!/usr/bin/env python3
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "vectors/headings-v1.json"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def scalar_count(value):
    # Python strings are Unicode scalar sequences on supported builds; reject lone surrogates explicitly.
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("invalid_heading")
    return len(value)


def bounded_text(value, maximum, allow_empty=False):
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and scalar_count(value) <= maximum
    )


def valid_identifier(value, maximum):
    return bounded_text(value, maximum) and IDENTIFIER.fullmatch(value) is not None


def normalize(value):
    if isinstance(value, str):
        if not bounded_text(value, 1024):
            raise ValueError("invalid_heading")
        return {"id": "den.legacy", "args": {}, "fallback": value}
    if not isinstance(value, dict) or set(value) != {"id", "args", "fallback"}:
        raise ValueError("invalid_heading")
    if not valid_identifier(value["id"], 128) or not bounded_text(value["fallback"], 1024):
        raise ValueError("invalid_heading")
    args = value["args"]
    if not isinstance(args, dict) or len(args) > 16:
        raise ValueError("invalid_heading")
    for name, argument in args.items():
        if not valid_identifier(name, 64):
            raise ValueError("invalid_heading")
        if isinstance(argument, bool):
            raise ValueError("invalid_heading")
        if isinstance(argument, int):
            if abs(argument) > MAX_SAFE_INTEGER:
                raise ValueError("invalid_heading")
        elif not bounded_text(argument, 512, allow_empty=True):
            raise ValueError("invalid_heading")
    return value


def main():
    vectors = json.loads(VECTORS.read_text())
    assert vectors["version"] == 1
    for case in vectors["validCases"]:
        assert normalize(case["value"]) == case["normalized"], case["name"]
    for case in vectors["invalidCases"]:
        try:
            normalize(case["value"])
        except (TypeError, ValueError):
            continue
        raise AssertionError(case["name"])
    print(f"headings-v1: {len(vectors['validCases'])} valid, {len(vectors['invalidCases'])} invalid cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
