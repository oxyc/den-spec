#!/usr/bin/env python3
"""Install a structurally valid, semantically wrong library-v4 outcome for CI."""

import json
import sys
from pathlib import Path

import validate_library_vectors


CASE = "a watchlist removal of a film never watched leaves it in no list, not deleted"


def install_wrong_outcome(path):
    vectors = json.loads(path.read_text())
    matches = [case for case in vectors["cases"] if case.get("name") == CASE]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {CASE!r} case")
    status = matches[0]["ok"]["documents"][0]["status"]
    if status.get("value") != "none":
        raise ValueError("watchlist-removal proof no longer starts from status none")
    status["value"] = "watched"

    # This is the gap the reverse contract closes: framing still accepts the
    # vector, while den-core's authoritative semantic replay must reject it.
    validate_library_vectors.validate(vectors)
    path.write_text(json.dumps(vectors, indent=2, ensure_ascii=False) + "\n")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <library-v4.json>")
    install_wrong_outcome(Path(sys.argv[1]))
    print(f"installed structurally valid wrong outcome: {CASE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
