#!/usr/bin/env python3
"""Generate the store-v1 test vectors: a tiny store, and the values a correct reader gets out of it.

  python3 tools/store-fixture.py --build-store ../den-dataset/scripts/v2/build_store.py \
      --out-dir vectors

Writes `vectors/store-v1.store` (a few hundred KB) and `vectors/store-v1.json`.

## Why this exists

`wire/store-v1.md` is implemented twice: a Python writer in den-dataset and a Rust reader in den-core, in
two repos on different release cadences. Prose cannot keep them in step — the first version of the spec
said the content hash was xxHash64 while the writer used blake2b-64, which would have made every store we
shipped unreadable by a reader that believed the document. Nothing caught it, because the fixture the spec
promised did not exist.

Both repos load these two files in their own tests. A writer that changes the layout, or a reader that
misreads it, fails its own build instead of shipping a store that reads as plausible nonsense.

The store here is built by **the real writer**, not by a reimplementation: a fixture produced by a second
copy of the layout logic would only prove that copy agrees with itself. The inputs are synthetic and tiny,
chosen to exercise the shapes that are easy to get wrong rather than to look like real data.
"""
import argparse
import gzip
import json
import os
import struct
import subprocess
import sys
import tempfile

DIMS = 1024

# Three titles, chosen for shape rather than realism:
#   movie:1  everything present, two of every list, a full facet row
#   movie:2  a FACTS-ONLY row — no labels, no vectors. These exist (89 of them in the real corpus) and
#            are exactly what a reader is most likely to mishandle.
#   tv:10    the other media type, so the packed key's media bit is exercised, plus a premise vector and
#            an empty list where movie:1 has entries.
TITLES = [
    {
        "key": "movie:1", "mediaType": "movie", "tmdbId": 1,
        "facts": {
            "titles": {"en": "Alpha", "orig": "Alfa", "aliases": ["Alpha One"]},
            "imdbId": "tt0000001",
            "released": {"date": "1999-12-31", "precision": "day"},
            "runtimeMinutes": 101,
            "genres": ["Q1"], "countries": ["US", "GB"], "languages": ["EN"],
            "directors": ["Q100"], "screenwriters": ["Q101"], "cast": ["Q102", "Q103"],
            "composers": ["Q104"], "franchise": ["Q105"],
            "basedOn": ["Q106"], "basedOnKind": ["book"],
            # Every remaining entity list, so the fixture exercises all of them: a section that is
            # legitimately empty in the real corpus is a bug, and the writer now refuses one.
            "cinematographers": ["Q108"], "distributors": ["Q109"],
            "productionCompanies": ["Q110"], "narrativeLocations": ["Q111"],
            "mainSubjects": ["Q112"], "instanceOf": ["Q113"],
            "hasVector": True,
        },
        "labels": {"primaryGenre": "Drama", "animated": False,
                   "subgenres": [{"label": "Prison", "confidence": 0.7}],
                   "moods": [{"label": "Bleak", "confidence": 0.55}]},
        "applicability": {"validity": {"choice": "correct-screen-work", "confidence": 0.99}},
        "facets": {"era": {"choice": "contemporary", "confidence": 0.96},
                   "tone": {"choice": "bleak", "confidence": 0.37},
                   # `does-not-apply` must be stored as ABSENT, never as a value
                   "pacing": {"choice": "does-not-apply", "confidence": 0.5}},
        "scores": {"intensity": {"score": 3.21}, "humour": {"score": 0.33},
                   "emotional_weight": {"score": 2.81}, "complexity": {"score": 2.29}},
        "nouls": {"theme__epic": {"noul": 0.9}, "theme__vampire": {"noul": 0.25}},
        "critique": {"institution": {"noul": 0.8}, "class": {"noul": 0.1}},
        "technique": {"live_action": {"noul": 0.62}},
        "depicts": {"violence": {"noul": 0.4}},
        "audience": {"made_for_children": {"noul": 0.0}},
    },
    {
        "key": "movie:2", "mediaType": "movie", "tmdbId": 2,
        "facts": {"titles": {"en": "Beta"}, "countries": ["FR"], "hasVector": False},
        "labels": None, "premiseLabels": None,
        "applicability": {}, "facets": {}, "scores": {}, "nouls": {},
        "critique": {}, "technique": {}, "depicts": {}, "audience": {},
    },
    {
        "key": "tv:10", "mediaType": "tv", "tmdbId": 10,
        "facts": {
            "titles": {"en": "Gamma"}, "imdbId": "tt0000010",
            "started": {"date": "2010-01-01", "precision": "day"},
            "ended": {"date": "2014", "precision": "year"},
            "episodes": 62, "seasons": 5,
            "genres": ["Q2"], "languages": ["SV"],
            "creators": ["Q100"], "cast": ["Q103"], "broadcaster": ["Q107"],
            "hasVector": True,
        },
        "labels": {"primaryGenre": "Crime", "animated": False, "subgenres": [], "moods": []},
        "applicability": {"narrative_applicability": {"choice": "open-or-multi-arc-narrative",
                                                      "confidence": 0.88}},
        "facets": {"ensemble": {"choice": "ensemble-led", "confidence": 0.93}},
        "scores": {"intensity": {"score": 3.0}, "humour": {"score": 1.09},
                   "emotional_weight": {"score": 3.0}, "complexity": {"score": 3.13}},
        "nouls": {"theme__political": {"noul": 0.75}},
        "critique": {"institution": {"noul": 0.95}, "class": {"noul": 0.6}},
        "technique": {"live_action": {"noul": 0.99}},
        "depicts": {"violence": {"noul": 0.7}},
        "audience": {"made_for_children": {"noul": 0.0}},
    },
]

ENTITIES = {
    "Q100": {"en": "Ada Director", "tmdbPersonId": "900"},
    "Q101": {"en": "Bo Writer"},
    # Aliases: people search indexes these as well as `en`, so the fixture has to carry one.
    "Q102": {"en": "Cy Actor", "tmdbPersonId": "902", "aliases": ["Cyrus Actor", "C. Actor"]},
    "Q103": {"en": "Di Actor"},
    "Q104": {"en": "Ed Composer"},
    "Q105": {"en": "The Alpha Saga"},
    "Q106": {"en": "Alpha, the novel"},
    "Q107": {"en": "A Broadcaster"},
    "Q108": {"en": "Fi Cinematographer"},
    "Q109": {"en": "A Distributor"},
    "Q110": {"en": "A Production Company"},
    "Q111": {"en": "A Narrative Place"},
    "Q112": {"en": "A Main Subject"},
    "Q113": {"en": "film"},
}
GENRE_MAP = {"Q1": {"movie": 18, "tv": 18}, "Q2": {"movie": [80, 18], "tv": [80, 18]}}
# Which titles carry a vector, in the order their labels file lists them — the store re-orders these into
# sorted-key order, and getting that wrong is invisible in normal use.
PLOT_ROWS = ["tv:10", "movie:1"]
PREMISE_ROWS = ["tv:10"]


def vector_blob(keys, fill):
    body = bytearray(struct.pack("<II", len(keys), DIMS))
    for i, _ in enumerate(keys):
        body.extend(bytes((fill + i + j) % 256 for j in range(DIMS)))
    return bytes(body)


def labels_file(keys):
    return {"count": len(keys),
            "records": [{"mediaType": k.split(":")[0], "tmdbId": int(k.split(":")[1])} for k in keys]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-store", required=True, help="path to den-dataset's build_store.py")
    ap.add_argument("--out-dir", default="vectors")
    # Anything after `--` goes to the writer untouched. The writer has options that do not change the
    # bytes — stamping a manifest, for one — and den-dataset's own tests exercise them through this
    # generator, because this is where the tiny valid inputs live. Without a passthrough those tests
    # have to skip, which is the false pass this whole fixture exists to prevent.
    ap.add_argument("writer_args", nargs="*", help="extra arguments for build_store.py (after --)")
    args = ap.parse_args()

    work = tempfile.mkdtemp(prefix="store-fixture-")
    corpus = os.path.join(work, "corpus.jsonl.gz")
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(corpus, "wb"), mtime=0) as fh:
        for row in TITLES:
            fh.write((json.dumps(row, sort_keys=True) + "\n").encode())

    def dump(name, value):
        path = os.path.join(work, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(value, fh, sort_keys=True)
        return path

    entities = dump("entities.json", ENTITIES)
    facts = dump("facts.json", {"datasetVersion": "fixture", "genreMap": GENRE_MAP,
                                "records": [{"mediaType": t["mediaType"], "tmdbId": t["tmdbId"]}
                                            for t in TITLES]})
    metadata = dump("metadata.json", {"records": [
        {"mediaType": "movie", "tmdbId": 1, "title": "Alpha", "posterPath": "/alpha.jpg", "year": 1999},
        {"mediaType": "movie", "tmdbId": 2, "title": "Beta", "year": 2001},
        {"mediaType": "tv", "tmdbId": 10, "title": "Gamma", "posterPath": "/gamma.jpg", "year": 2010},
    ]})
    # An enriched batch dir, for vote counts. movie:2 is deliberately absent — a title with no vote count
    # must read 0 rather than inherit a neighbour's, which is what a row ordered by votes depends on.
    enriched = os.path.join(work, "enriched")
    os.makedirs(enriched, exist_ok=True)
    with open(os.path.join(enriched, "batch-1.json"), "w", encoding="utf-8") as fh:
        json.dump([{"mediaType": "movie", "tmdbId": 1, "voteCount": 1234}], fh)
    # A LATER batch revising movie:1 upward, so the fixture pins batch-number order with last-write-wins:
    # `sorted()` on the names would put batch-10 before batch-2 and take 1234.
    with open(os.path.join(enriched, "batch-10.json"), "w", encoding="utf-8") as fh:
        json.dump([{"mediaType": "movie", "tmdbId": 1, "voteCount": 4321},
                   {"mediaType": "tv", "tmdbId": 10, "voteCount": 77}], fh)

    plot_labels = dump("plot-labels.json", labels_file(PLOT_ROWS))
    premise_labels = dump("premise-labels.json", labels_file(PREMISE_ROWS))
    plot_bin = os.path.join(work, "plot.bin")
    open(plot_bin, "wb").write(vector_blob(PLOT_ROWS, 7))
    premise_bin = os.path.join(work, "premise.bin")
    open(premise_bin, "wb").write(vector_blob(PREMISE_ROWS, 200))

    os.makedirs(args.out_dir, exist_ok=True)
    store = os.path.join(args.out_dir, "store-v1.store")
    result = subprocess.run(
        [sys.executable, args.build_store, "--corpus", corpus, "--entities", entities, "--facts", facts,
         "--metadata", metadata, "--vectors", plot_bin, "--vector-labels", plot_labels,
         "--premise-vectors", premise_bin, "--premise-labels", premise_labels,
         "--enriched", enriched,
         "--dataset-version", "fixture", "--out", store, *args.writer_args],
        capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"build_store failed:\n{result.stderr}")

    blob = open(store, "rb").read()
    version, endian = struct.unpack("<II", blob[8:16])
    digest, sections, rows = struct.unpack("<QII", blob[16:32])

    # What a correct reader gets out of it. Written from the INPUTS above, so a reader that agrees with
    # this agrees with the data rather than with whatever the writer happened to do.
    expected = {
        "_": "Test vectors for den-spec wire/store-v1.md. Generated by tools/store-fixture.py.",
        "store": "store-v1.store",
        "header": {
            "magic": "DENSTOR1", "formatVersion": version, "endianness": endian,
            "contentHash": digest, "sectionCount": sections, "rowCount": rows,
            "datasetVersion": "fixture", "bytes": len(blob),
        },
        "rows": [
            {"key": "movie:1", "row": 0, "media": 0, "tmdbId": 1,
             "cardTitle": "Alpha", "cardPoster": "/alpha.jpg", "cardYear": 1999, "votes": 4321,
             "primaryGenre": "Drama", "animated": False,
             "subgenres": [["Prison", 70]], "moods": [["Bleak", 55]],
             "facets": {"era": ["contemporary", 96], "tone": ["bleak", 37]},
             "facetsAbsent": ["pacing", "setting", "scope", "ending", "chronology", "continuity",
                              "conflict", "ensemble", "timespan", "archetype"],
             "scores": {"intensity": 321, "humour": 33, "emotional_weight": 281, "complexity": 229},
             "world": 25, "nouls": {"theme__epic": 90, "theme__vampire": 25},
             "critique": {"institution": 80, "class": 10},
             "imdb": "tt0000001", "runtime": 101, "released": 10956, "releasedPrecision": 0,
             "genres": [18], "countries": ["US", "GB"], "languages": ["EN"],
             "makers": ["Ada Director", "Bo Writer"], "cast": ["Cy Actor", "Di Actor"],
             "composers": ["Ed Composer"], "franchise": "The Alpha Saga",
             "cinematographers": ["Fi Cinematographer"], "distributors": ["A Distributor"],
             "productionCompanies": ["A Production Company"],
             "narrativeLocations": ["A Narrative Place"], "mainSubjects": ["A Main Subject"],
             "instanceOf": ["film"], "basedOn": ["Alpha, the novel"], "basedOnKind": ["book"],
             "aliasTitles": ["Alpha One"], "hasVector": True,
             "hasPlotVector": True, "hasPremiseVector": False},
            {"key": "movie:2", "row": 1, "media": 0, "tmdbId": 2,
             "cardTitle": "Beta", "cardPoster": None, "cardYear": 2001, "votes": 0,
             "primaryGenre": None, "subgenres": [], "moods": [],
             "countries": ["FR"], "makers": [], "cast": [],
             "released": None, "hasVector": False,
             "hasPlotVector": False, "hasPremiseVector": False,
             "_note": "A facts-only row: present in facts, absent from every label and vector file."},
            {"key": "tv:10", "row": 2, "media": 1, "tmdbId": 10,
             "cardTitle": "Gamma", "cardPoster": "/gamma.jpg", "cardYear": 2010, "votes": 77,
             "primaryGenre": "Crime", "subgenres": [], "moods": [],
             "facets": {"ensemble": ["ensemble-led", 93]},
             "scores": {"intensity": 300, "humour": 109, "emotional_weight": 300, "complexity": 313},
             "genres": [80, 18], "episodes": 62, "seasons": 5,
             "makers": ["Ada Director"], "cast": ["Di Actor"],
             "hasPlotVector": True, "hasPremiseVector": True,
             "_note": "genres: one Q-id mapping to TWO TMDB ids, in the genreMap's order — NOT sorted. "
                      "/recommend treats the first as the most significant, so sorting renames titles."},
        ],
        # The entity table, keyed by Q-id. People search reads the aliases as well as the name, so a
        # reader that indexes only `ent_name` answers "Cyrus Actor" with nothing.
        "entities": [
            {"qid": 102, "name": "Cy Actor", "tmdbPersonId": 902,
             "aliases": ["Cyrus Actor", "C. Actor"]},
            {"qid": 100, "name": "Ada Director", "tmdbPersonId": 900, "aliases": []},
        ],
        "notes": [
            "Scores are u16 HUNDREDTHS of a 0..4 axis: 321 is 3.21. They are not twentieths.",
            "Probabilities and confidences are u8 hundredths.",
            "A facet answered `does-not-apply` is stored as u32::MAX, never as a value.",
            "`released` is days since 1970-01-01; 10956 is 1999-12-31.",
            "The content hash is BLAKE2b with digest_size=8, read little-endian, over every byte from 64.",
            "Rows are sorted by the packed key (media << 32 | tmdbId), so movie rows precede tv rows.",
            "Vectors are re-ordered from their own labels-file order into the store's sorted-key order.",
            "ent_alias_* holds STRINGS, not hashes: the reader folds them with its own name_key.",
        ],
    }
    with open(os.path.join(args.out_dir, "store-v1.json"), "w", encoding="utf-8") as fh:
        json.dump(expected, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps({"store": store, "bytes": len(blob), "sections": sections, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
