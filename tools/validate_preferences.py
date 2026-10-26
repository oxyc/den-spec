#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "vectors/preferences-v1.json"
LANGUAGE = __import__("re").compile(r"^[a-z]{2}$")
PREFERRED = "den.preferredSubtitleLanguage"
EXPLICIT = "den.hasExplicitSubtitlePreference"


def read(values):
    if not isinstance(values, dict) or len(values) > 4096:
        raise ValueError("invalid_preferences")
    has_language = PREFERRED in values
    has_explicit = EXPLICIT in values
    language = values.get(PREFERRED)
    explicit = values.get(EXPLICIT)
    if language is not None:
        if not isinstance(language, dict) or set(language) != {"string"} or not LANGUAGE.fullmatch(language["string"]):
            raise ValueError("invalid_subtitle_preference")
        if has_explicit and explicit != {"bool": True}:
            raise ValueError("invalid_subtitle_preference")
        return {"mode": "language", "language": language["string"]}
    if has_explicit:
        if explicit is None:
            return {"mode": "automatic"}
        if explicit == {"bool": True}:
            return {"mode": "off"}
        raise ValueError("invalid_subtitle_preference")
    return {"mode": "off"} if has_language else {"mode": "automatic"}


def write(choice):
    if not isinstance(choice, dict) or set(choice) not in ({"mode"}, {"mode", "language"}):
        raise ValueError("invalid_subtitle_choice")
    mode = choice.get("mode")
    if mode == "automatic" and set(choice) == {"mode"}:
        language, explicit = None, None
    elif mode == "off" and set(choice) == {"mode"}:
        language, explicit = None, {"bool": True}
    elif mode == "language" and set(choice) == {"mode", "language"} and isinstance(choice["language"], str) and LANGUAGE.fullmatch(choice["language"]):
        language, explicit = {"string": choice["language"]}, {"bool": True}
    else:
        raise ValueError("invalid_subtitle_choice")
    return {PREFERRED: language, EXPLICIT: explicit}


def main():
    vectors = json.loads(VECTORS.read_text())
    assert vectors["version"] == 1
    for case in vectors["readCases"]:
        assert read(case["values"]) == case["choice"], case["name"]
    for case in vectors["writeCases"]:
        assert write(case["choice"]) == case["changes"], case["name"]
    for case in vectors["invalidCases"]:
        try:
            read(case["values"]) if case["operation"] == "read" else write(case["choice"])
        except ValueError:
            continue
        raise AssertionError(case["name"])
    print(f"preferences-v1: {len(vectors['readCases'])} read, {len(vectors['writeCases'])} write, {len(vectors['invalidCases'])} invalid cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
