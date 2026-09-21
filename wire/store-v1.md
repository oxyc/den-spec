# Store v1

The single artifact den-atlas loads: every per-title signal the serving path needs, plus both vector
matrices, in one mmap'd file. Written by den-dataset (`scripts/v2/build_store.py`) from
`corpus-<version>.jsonl.gz`; read by den-atlas (`src/store.rs`). Nothing else reads it, and nothing writes
it by hand.

A Python writer and a Rust reader, in two repos on different release cadences, is exactly the drift this
repo exists to prevent. [`vectors/store-v1.json`](../vectors/store-v1.json) and the fixture beside it are
loaded by both repos' tests, so a writer or a reader that drifts fails its own build rather than shipping a
store that reads as nonsense. Both assert the `format_version` in §2.

As with every format here, a change is a **new version file** — `store-v2.md` — never an edit to this one.

## Why a binary store at all

atlas parsed ~210 MB of JSON into owned Rust structures at load: 800 ms, and a 552 MB peak of which ~290 MB
was a `serde_json` arena that never returned to the OS. Measured alternatives: SQLite lost More Like This by
100× (15.4 ms vs 0.15 ms — a 400-candidate pool is 400 statement steps), rkyv cost 2× the disk and 2× the
resident footprint. A columnar blob mmaps in 8.6 ms with no parse and no heap copy.

## Principles

**Everything is an array indexed by row.** A title's row number is its position in `keys`; every per-title
section is addressed by that same number. There are no per-title structs and no pointers — a "record" is a
row number read out of a dozen parallel arrays.

**Strings appear once, in ONE dictionary.** Every string — a facet value, a genre label, a country, a
title, an alias, an entity name — lives in `strings` and is referenced by a `u32` id. The corpus holds
~1.1M owned `String`s representing 281,090 distinct values, of which the controlled vocabulary (facet
values, label names, countries, languages) is only ~479.

Splitting that vocabulary into its own `u16` table saves 1.84 MB of 124.8 MB — and buys two bare integer
id spaces with nothing in the format distinguishing them, so a reader that resolves a vocabulary id against
the free-text table gets a wrong but perfectly valid string, silently. One id space costs 1.5% and makes
that unrepresentable. **There is exactly one `string(id)` on each side of this contract.**

**Probabilities are `u8` hundredths; the 0..4 score axes are `u16` hundredths.** Verified over the corpus:
1,976,530 float literals, **zero** with more than two decimals, and `57u8 as f64 / 100.0` is bit-identical
to parsing `"0.57"`. The writer asserts this rather than assuming it — a future pass emitting three
decimals must fail loudly, not round silently.

The scores were `u8` twentieths in the first draft, on the reasoning that hundredths overflow a `u8` at
2.56. True, and the wrong conclusion: the corpus scores are hundredths, so twentieths rounded **94,498 of
190,116 values** without a word. The answer to "does not fit" is a wider integer, not a coarser unit.

**Variable-length lists are values + offsets.** A list section is a values array and an `offsets` array of
`len(keys) + 1` entries; row *i* owns `values[offsets[i]..offsets[i+1]]`. An empty list is a zero-width
span, not a sentinel.

**Sections are 8-byte aligned.** An mmap base is page-aligned, so 8-byte section offsets make every
`u16`/`u32`/`u64`/`i8` slice cast legal. Alignment is not a detail: a content hash catches corruption but
**not** misalignment, and unaligned loads happen to work on both our architectures, so the UB would pass
every test.

## Layout

```
┌──────────────────────────────────────────────────────────────┐
│ header            64 bytes, fixed                            │
├──────────────────────────────────────────────────────────────┤
│ section table     n × 32 bytes                               │
├──────────────────────────────────────────────────────────────┤
│ sections          8-byte aligned, in table order             │
└──────────────────────────────────────────────────────────────┘
```

### Header (64 bytes, little-endian)

| offset | type | field |
|---|---|---|
| 0 | `[u8; 8]` | magic `DENSTOR1` |
| 8 | `u32` | `format_version` — a reader that does not know it MUST refuse |
| 12 | `u32` | `endianness` = `0x01020304`; any other value means a foreign writer, refuse |
| 16 | `u64` | `content_hash` — **BLAKE2b with `digest_size=8`**, read little-endian, over every byte from 64 to EOF |
| 24 | `u32` | `section_count` |
| 28 | `u32` | `row_count` — titles |
| 32 | `[u8; 16]` | `dataset_version`, ASCII, NUL-padded |
| 48 | `[u8; 16]` | reserved, zero |

`content_hash` is verified **before any cast**. It is the only defence that actually works: measured
against 400 single-bit flips, a structural validator rejected 39 and let 77 return wrong answers; a
64-bit content hash caught 400 of 400. Structure-checking is not integrity-checking.

The writer computes it as `hashlib.blake2b(payload, digest_size=8)`; the reader must agree byte for byte.
This document said **xxHash64** until the fixture below existed to contradict it — a reader that believed
the spec would have refused every store we ship, at byte 16, without reading a single section. That is
what an unenforced contract is worth.

### Section table entry (32 bytes)

| offset | type | field |
|---|---|---|
| 0 | `[u8; 16]` | name, ASCII, NUL-padded |
| 16 | `u64` | offset from file start |
| 24 | `u32` | byte length |
| 28 | `u32` | element width, for the alignment assert |

## Sections

`R` = `row_count`. `E` = entity count.

### Identity

| name | type | length | meaning |
|---|---|---|---|
| `keys` | `u64` | R | `(media << 32) \| tmdb_id`, **sorted ascending**. `media`: 0 = movie, 1 = tv. Binary search; this is the primary key |
| `strings` | `u8` | — | every string, concatenated, UTF-8, no separators |
| `str_off` | `u32` | n+1 | string *i* is `strings[str_off[i]..str_off[i+1]]` |

### Cards

| name | type | length |
|---|---|---|
| `card_title` | `u32` | R — string id |
| `card_poster` | `u32` | R — string id, `u32::MAX` = none |
| `card_year` | `i16` | R — `i16::MIN` = none |

### Labels

| name | type | length |
|---|---|---|
| `primary_genre` | `u32` | R — string id, `u32::MAX` = none |
| `animated` | `u8` | R — 1 if the title is animated |
| `subgenre_v` / `subgenre_c` / `subgenre_o` | `u32` / `u8` / `u32` | list |
| `mood_v` / `mood_c` / `mood_o` | `u32` / `u8` / `u32` | list |

Subgenres and moods are **scored**: each entry is a label and the model's confidence, sharing one offsets
array so row *i* owns both spans. Storing only the label would discard the one thing separating a 0.7
assignment from a 0.3 one.

### Facets

Two dense row-major arrays, `R × 12`, in this fixed axis order: `era`, `setting`, `scope`, `ending`,
`pacing`, `chronology`, `continuity`, `conflict`, `ensemble`, `tone`, `timespan`, `archetype`. Row *i*
axis *a* is at `i * 12 + a`.

| name | type | length |
|---|---|---|
| `facet_v` | `u32` | R × 12 — string id, `u32::MAX` = the model declined |
| `facet_c` | `u8` | R × 12 — confidence, hundredths |

Dense rather than 24 per-axis sections: a section name is capped at 16 bytes and `facet_chronology_v` is
18, but the better reason is that one indexing rule beats twelve names.

`does-not-apply` is stored as absent, never as a value: it is the model declining, and a scorer counting it
as agreement would pair every declining title with every other.

### Scores, world, nouls, critique

| name | type | length | meaning |
|---|---|---|---|
| `score_intensity` · `score_humour` · `score_weight` · `score_complexity` | `u16` | R | **hundredths** of a 0..4 axis, so 321 is 3.21. `u16::MAX` = absent, which a genuine 0.00 is not |
| `world` | `u8` | R | hundredths, distance from a realist world |
| `noul_k_v` / `noul_k_o` | `u8` / `u32` | list | noul id, index into `noul_names` |
| `noul_v_v` / `noul_v_o` | `u8` / `u32` | list | its value, hundredths. Same spans as `noul_k` |
| `noul_names` | `u32` | N | string ids, the taxonomy noul vocabulary |
| `critique` | `u8` | R × C | dense, row-major, hundredths. Every title carries every axis |
| `critique_names` | `u32` | C | string ids |
| `technique` | `u8` | R × T | dense, hundredths |
| `technique_names` | `u32` | T | string ids |

Critique is dense because it is centred per-axis at load: an unanswered axis must centre to `-mean`, not
be skipped by a cosine's name intersection.

### Facts

Each is a `u32` values array of interned ids plus a `u32` offsets array:

`makers` (directors ∪ creators ∪ **screenwriters**, deduplicated, entity ids), `cast` (entity ids),
`broadcasters` (entity ids), `genres` (**TMDB genre ids, not entity ids and not interned**), `countries`
(string ids), `languages` (string ids), `alias_titles` (string ids).

| name | type | length | meaning |
|---|---|---|---|
| `imdb` | `u32` | R | string id, `u32::MAX` = none |
| `released` | `i32` | R | the corpus `released`/`started` value verbatim, `i32::MIN` = none |
| `runtime` | `u16` | R | minutes, 0 = none |
| `franchise` | `u32` | R | entity id, `u32::MAX` = none |
| `orig_lang` | `u32` | R | string id of the first language, `u32::MAX` = none |

### Entities

| name | type | length |
|---|---|---|
| `ent_qid` | `u32` | E — **sorted**, binary search |
| `ent_name` | `u32` | E — string id |
| `ent_tmdb` | `u32` | E — TMDB person id, `u32::MAX` = none |
| `ent_credits` | `u32` | E — titles crediting this entity, for rarity weighting |

### The inverted maker index

| name | type | length |
|---|---|---|
| `maker_ent` | `u32` | m — **sorted** entity ids |
| `maker_rows_v` / `maker_rows_o` | `u32` / `u32` | list — rows crediting that entity |

`titles_sharing_makers` was a linear scan of every record, per request: 83.7 µs, and 158.7 µs at 2× corpus.
Indexed it is **0.4 µs and flat**. 355 KB.

### Vectors

| name | type | length |
|---|---|---|
| `vec_plot` | `i8` | R × 1024 |
| `vec_premise` | `i8` | R × 1024 — all-zero for a row with no premise vector |
| `vec_premise_has` | `u8` | R — 1 where the row has one |

`i8` is align-1, so these cost no padding. They stay a contiguous matrix because the ANN scan walks them
linearly; row *i* is `vec_plot[i * 1024 ..][.. 1024]`.

## What the writer asserts

A silent miss is the failure this whole format exists to stop. The writer refuses to emit a store when:

- a section's row count != `row_count`, or a list's offsets != `row_count + 1`
- store rows != the facts record count — **89 facts-only titles were dropped once** by iterating the pass
  instead of joining by key, and they are exactly the records nothing else covers
- labels present != `labelsRecords`, premise != `premiseLabelsRecords` — a join that matches nothing is a
  bug in the join, never a property of the data
- any probability has more than two decimals, is `NaN`, or is out of range
- any section offset is not 8-byte aligned
- a string id is out of range, or an entity id is not in `ent_qid`

## Determinism

Byte-identical across runs and machines: dictionaries are sorted before ids are assigned, rows are sorted
by key, and no `dict`/`set` iteration order reaches the output. The publish step re-uploads only what
changed, so non-determinism would mean churn on every publish.
