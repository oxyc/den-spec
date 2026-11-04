#!/usr/bin/env python3
"""Discover every spec-owned Python contract validator and run its tests."""

import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parent


def test_cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_cases(test)
        else:
            yield test


def main():
    expected = {path.stem for path in TOOLS.glob("test_*.py")}
    suite = unittest.defaultTestLoader.discover(
        str(TOOLS), pattern="test_*.py", top_level_dir=str(TOOLS)
    )
    selected = {
        test.__class__.__module__.rpartition(".")[2]
        for test in test_cases(suite)
    }
    missing = sorted(expected - selected)
    if missing:
        raise AssertionError(
            "contract validators discovered by filename but contributing no tests: "
            + ", ".join(missing)
        )
    unexpected = sorted(selected - expected)
    if unexpected:
        raise AssertionError(
            "contract tests loaded outside test_*.py validators: "
            + ", ".join(unexpected)
        )

    print(f"contract validators: {', '.join(sorted(selected))}")
    result = unittest.TextTestRunner().run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
