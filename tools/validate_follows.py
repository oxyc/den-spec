#!/usr/bin/env python3
"""Reference implementation of follows-v1 (rows, event identity, derivation), checked against its vectors."""
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "vectors/follows-v1.json"
MAX_SAFE_INTEGER = 9_007_199_254_740_991
FOLLOW_KEY = re.compile(r"^(tv|movie|person):([1-9][0-9]{0,15})$")
DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
DAY_MS = 86_400_000
EPISODE_DAYS = 7
RETURNS_DAYS = 90
RELEASE_DAYS = 30
RETENTION_MS = 180 * DAY_MS
UPDATES_LIMIT = 2_000
STATES = {"read", "dismissed"}


def safe_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SAFE_INTEGER


def follows(values):
    """§1: the active follows, key → since."""
    active = {}
    for key, setting in values.items():
        match = FOLLOW_KEY.fullmatch(key)
        if not match or int(match[2]) > MAX_SAFE_INTEGER or not isinstance(setting, dict):
            continue
        value = setting.get("value")
        if isinstance(value, dict) and set(value) == {"int"} and safe_int(value["int"]):
            active[key] = value["int"]
    return active


def stamp(setting):
    at = setting.get("at") if isinstance(setting, dict) else None
    if isinstance(at, list) and len(at) == 3 and safe_int(at[0]) and safe_int(at[1]) and isinstance(at[2], str):
        return tuple(at)
    return None


def marks(values, now):
    """§1: event id → read/dismissed, ignoring settings past retention."""
    found = {}
    for event, setting in values.items():
        at = stamp(setting)
        if at is None or at[0] < now - RETENTION_MS:
            continue
        value = setting.get("value")
        if isinstance(value, dict) and set(value) == {"string"} and value["string"] in STATES:
            found[event] = value["string"]
    return found


def pruned(values, now):
    """§1: what a writer keeps of the row's settings before adding its own."""
    kept = {event: setting for event, setting in values.items() if (at := stamp(setting)) and at[0] >= now - RETENTION_MS}
    newest = sorted(kept, key=lambda event: stamp(kept[event]), reverse=True)[:UPDATES_LIMIT]
    return {event: kept[event] for event in sorted(newest)}


def calendar_day(value):
    if not isinstance(value, str) or not DAY.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def baseline_day(since):
    return datetime.fromtimestamp(since / 1000, tz=timezone.utc).date()


def episodes(airing):
    listed = [airing.get("previous"), airing.get("next"), *(airing.get("upcoming") or [])]
    return [one for one in listed if isinstance(one, dict)]


def derive(today_text, follow_values, facts, mark_values=None, now=0):
    today = calendar_day(today_text)
    active = follows(follow_values)
    found = {}

    def add(event_id, kind, title, day, source, observed, via, **coords):
        assert len(event_id) <= 96 and event_id.isascii(), event_id
        held = found.get(event_id)
        if held is None or via < held["_first"]:
            fields = {
                "id": event_id,
                "type": kind,
                "title": title,
                **coords,
                "date": day.isoformat(),
                "precision": "day",
                "source": source,
                "observedAt": observed,
                "via": held["via"] if held else [],
                "_first": via,
            }
            found[event_id] = fields
        if via not in found[event_id]["via"]:
            found[event_id]["via"].append(via)

    for key, since in sorted(active.items()):
        kind, raw = key.split(":")
        ident = int(raw)
        base = baseline_day(since)
        if kind == "tv":
            airing = facts.get("airing", {}).get(raw)
            if not isinstance(airing, dict):
                continue
            source = "tvmaze" if airing.get("source") == "tvmaze" else "tmdb"
            observed = airing.get("observedAt", 0)
            newest = None
            for one in episodes(airing):
                day = calendar_day(one.get("date"))
                season, episode = one.get("season"), one.get("episode")
                if day is None or not safe_int(season) or not safe_int(episode) or season <= 0:
                    continue
                if 0 <= (today - day).days <= EPISODE_DAYS and (newest is None or (season, episode) > newest[:2]):
                    newest = (season, episode, day)
            if newest and newest[2] > base:
                season, episode, day = newest
                add(f"episode:tv:{ident}:{season}:{episode}", "episode", {"type": "tv", "id": ident}, day, source,
                    observed, key, season=season, episode=episode)
            premiere = airing.get("seasonPremiere")
            if isinstance(premiere, dict) and safe_int(premiere.get("season")) and premiere["season"] >= 1:
                day = calendar_day(premiere.get("date"))
                if day and 1 <= (day - today).days <= RETURNS_DAYS and day > base:
                    season = premiere["season"]
                    add(f"returns:tv:{ident}:{season}", "returns", {"type": "tv", "id": ident}, day, source,
                        observed, key, season=season)
        elif kind == "movie":
            film = facts.get("films", {}).get(raw)
            day = calendar_day(film.get("date")) if isinstance(film, dict) else None
            if day and 0 <= (today - day).days <= RELEASE_DAYS and day > base:
                add(f"release:movie:{ident}", "release", {"type": "movie", "id": ident}, day, "tmdb",
                    film.get("observedAt", 0), key)
        else:
            credits = facts.get("credits", {}).get(raw)
            if not isinstance(credits, dict):
                continue
            for title in credits.get("titles", []):
                kind_of, title_id = title.get("type"), title.get("id")
                if kind_of not in ("movie", "tv") or not safe_int(title_id) or title_id < 1:
                    continue
                if kind_of == "tv" and f"tv:{title_id}" in active:
                    continue
                day = calendar_day(title.get("date"))
                if day and 0 <= (today - day).days <= RELEASE_DAYS and day > base:
                    add(f"release:{kind_of}:{title_id}", "release", {"type": kind_of, "id": title_id}, day, "tmdb",
                        credits.get("observedAt", 0), key)

    state = marks(mark_values or {}, now)
    listed = []
    for event in found.values():
        event.pop("_first")
        event["via"].sort()
        if state.get(event["id"]) == "dismissed":
            continue
        event["read"] = state.get(event["id"]) == "read"
        listed.append(event)
    listed.sort(key=lambda event: event["id"].encode())
    listed.sort(key=lambda event: event["date"], reverse=True)
    return {"events": listed, "unread": sum(not event["read"] for event in listed)}


def main():
    vectors = json.loads(VECTORS.read_text())
    assert vectors["version"] == 1
    for case in vectors["followCases"]:
        assert follows(case["values"]) == case["active"], case["name"]
    for case in vectors["updateCases"]:
        assert marks(case["values"], case["now"]) == case["marks"], case["name"]
        assert pruned(case["values"], case["now"]) == case["kept"], case["name"]
    for case in vectors["deriveCases"]:
        got = derive(case["today"], case["follows"], case["facts"], case.get("marks"), case.get("now", 0))
        assert got == case["expected"], f"{case['name']}: {json.dumps(got)}"
    print(
        f"follows-v1: {len(vectors['followCases'])} follow, {len(vectors['updateCases'])} update, "
        f"{len(vectors['deriveCases'])} derive cases"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
