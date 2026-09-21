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
        "applicability": {"validity": {"choice": "correct-screen-work", "confidence": 0.99,
                                       "probabilities": {"correct-screen-work": 0.99,
                                                         "source-work": 0.01}},
                          "narrative_applicability": {
                              "choice": "bounded-fictional-narrative", "confidence": 0.97,
                              "probabilities": {"bounded-fictional-narrative": 0.97,
                                                "documentary-or-factual": 0.03}}},
        "facets": {"era": {"choice": "contemporary", "confidence": 0.96,
                           "probabilities": {"contemporary": 0.96, "medieval": 0.04}},
                   # Below the publication gate's 0.70: collected, never published
                   "tone": {"choice": "bleak", "confidence": 0.37,
                            "probabilities": {"bleak": 0.37, "clinical": 0.33, "pulpy": 0.30}},
                   # `does-not-apply` must be stored as ABSENT, never as a value
                   "pacing": {"choice": "does-not-apply", "confidence": 0.5,
                              "probabilities": {"does-not-apply": 0.5, "brisk": 0.5}}},
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
            # A Wikidata label carrying a Wikipedia disambiguator. The card must draw "Gamma"; the label
            # itself stays in the dictionary, because search still has to match what people type.
            "titles": {"en": "Gamma (TV series)"}, "imdbId": "tt0000010",
            "started": {"date": "2010-01-01", "precision": "day"},
            "ended": {"date": "2014", "precision": "year"},
            "episodes": 62, "seasons": 5,
            "genres": ["Q2", "Q3"], "languages": ["SV"],
            "creators": ["Q100"], "cast": ["Q103", "Q999"], "broadcaster": ["Q107"],
            "hasVector": True,
        },
        "labels": {"primaryGenre": "Crime", "animated": False, "subgenres": [], "moods": []},
        # An open-ended series: its archetype is suppressed by content type, its other axes are not.
        "applicability": {"validity": {"choice": "correct-screen-work", "confidence": 0.95,
                                       "probabilities": {"correct-screen-work": 0.95,
                                                         "season-or-episode": 0.05}},
                          "narrative_applicability": {
                              "choice": "open-or-multi-arc-narrative", "confidence": 0.88,
                              "probabilities": {"open-or-multi-arc-narrative": 0.88,
                                                "bounded-fictional-narrative": 0.12}}},
        "facets": {"ensemble": {"choice": "ensemble-led", "confidence": 0.93,
                                "probabilities": {"ensemble-led": 0.93, "single-lead": 0.07}},
                   "archetype": {"choice": "downfall", "confidence": 0.91,
                                 "probabilities": {"downfall": 0.91, "rise": 0.09}}},
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
GENRE_MAP = {
    "Q1": {"movie": 18, "tv": 18},
    "Q2": {"movie": [80, 18], "tv": [80, 18]},
    # TV-ONLY, and a COMPOSITE: 10765 is "Sci-Fi & Fantasy". The writer has no film mapping to prefer,
    # so it stores 10765 and the reader must fold it into 878 and 14. Keeping the composite is what
    # dropped Horror from Chilling Adventures of Sabrina on the real corpus.
    "Q3": {"tv": 10765},
}
# Which titles carry a vector, in the order their labels file lists them — the store re-orders these into
# sorted-key order, and getting that wrong is invisible in normal use.
PLOT_ROWS = ["tv:10", "movie:1"]
PREMISE_ROWS = ["tv:10"]


def vector_blob(keys, fill):
    """A `DENVEC02` int8 blob: magic, count, dims, a u64 key per row, then the rows.

    This is an INPUT to the fixture, not part of store-v1 — the store copies rows out of it and writes
    its own `keys` section, so the key column here changes nothing about the bytes this tool commits.
    It is here because `build_store.py` now joins the blob to the corpus BY KEY. The blob used to be
    the matrix alone, and which title row n belonged to was whatever order `labels-*.json` happened to
    list its records in — an assumption nothing could check. The key is the same one the store uses:
    `(media << 32) | tmdbId`, media 0 = movie, 1 = tv.
    """
    packed = [((0 if k.split(":")[0] == "movie" else 1) << 32) | int(k.split(":")[1]) for k in keys]
    body = bytearray(b"DENVEC02" + struct.pack("<II", len(keys), DIMS))
    body.extend(struct.pack(f"<{len(packed)}Q", *packed))
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
    # No metadata sidecar: the writer no longer takes one. The title and the year come from each corpus
    # row's own `facts` — `titles.en` and `released` — and the poster path has no replacement at all.
    # The name a card draws is therefore the STRIPPED form of the Wikidata label, which is why tv:10's
    # label below carries a disambiguator the store must not keep.
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
         "--vectors", plot_bin, "--vector-labels", plot_labels,
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
             "cardTitle": "Alpha", "cardYear": 1999, "votes": 4321,
             "primaryGenre": "Drama", "animated": False,
             "subgenres": [["Prison", 70]], "moods": [["Bleak", 55]],
             # `tone` is collected at 0.37 and WITHHELD by the publication gate (probability < 0.70),
             # so it is absent here exactly as `pacing`'s `does-not-apply` is: one sentinel, one
             # meaning — this store makes no claim on that axis.
             "facets": {"era": ["contemporary", 96]},
             "facetsAbsent": ["pacing", "tone", "setting", "scope", "ending", "chronology",
                              "continuity", "conflict", "ensemble", "timespan", "archetype"],
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
             "aliasTitles": ["Alpha", "Alfa", "Alpha One"], "hasVector": True,
             "hasPlotVector": True, "hasPremiseVector": False},
            {"key": "movie:2", "row": 1, "media": 0, "tmdbId": 2,
             # No `released` in its facts, so no year: the card's year is the Wikidata release date now,
             # and a row without one carries the sentinel rather than a year from somewhere else.
             "cardTitle": "Beta", "cardYear": None, "votes": 0,
             # Its own name, not a neighbour's: the alias row is per title, and the writer once read a
             # stale binding here and gave every row the LAST row's names.
             "aliasTitles": ["Beta"],
             "primaryGenre": None, "subgenres": [], "moods": [],
             "countries": ["FR"], "makers": [], "cast": [],
             "released": None, "hasVector": False,
             "hasPlotVector": False, "hasPremiseVector": False,
             "_note": "A facts-only row: present in facts, absent from every label and vector file."},
            {"key": "tv:10", "row": 2, "media": 1, "tmdbId": 10,
             # The label is "Gamma (TV series)"; the card draws it stripped, and the FULL label stays in
             # the alias row, because search still has to match what people type.
             "cardTitle": "Gamma", "cardYear": 2010, "votes": 77,
             "aliasTitles": ["Gamma (TV series)"],
             "primaryGenre": "Crime", "subgenres": [], "moods": [],
             # `archetype` is answered at 0.91 and withheld: an open-or-multi-arc narrative may not
             # carry one, and no confidence threshold would have caught it.
             "facets": {"ensemble": ["ensemble-led", 93]},
             "facetsAbsent": ["archetype"],
             "scores": {"intensity": 300, "humour": 109, "emotional_weight": 300, "complexity": 313},
             "genres": [80, 18, 10765], "episodes": 62, "seasons": 5,
             "makers": ["Ada Director"], "cast": ["Di Actor", "Q999"],
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
            "card_poster is NOT a section: the writer is handed posterPath for two of these three "
            "titles and drops both, paths included, so a reader must treat the section's absence as "
            "'no posters' rather than as an error.",
            "Q3 is tv-only and a COMPOSITE (10765): stored as-is, the reader folds it to 878 and 14.",
            "Q999 is credited but has no entity entry: it is still in the table, named by its Q-id.",
        ],
    }
    with open(os.path.join(args.out_dir, "store-v1.json"), "w", encoding="utf-8") as fh:
        json.dump(expected, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps({"store": store, "bytes": len(blob), "sections": sections, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
