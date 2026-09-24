# Store v2

The single artifact den-atlas loads: every per-title signal the serving path needs, plus both vector
matrices, in one mmap'd file. Written by den-dataset (`scripts/v2/build_store.py`) from
`corpus-<version>.jsonl.gz`; read by den-atlas (`src/store.rs`). Nothing else reads it, and nothing writes
it by hand.

A Python writer and a Rust reader, in two repos on different release cadences, is exactly the drift this
repo exists to prevent. [`vectors/store-v2.json`](../vectors/store-v2.json) and the fixture beside it are
loaded by both repos' tests, so a writer or a reader that drifts fails its own build rather than shipping a
store that reads as nonsense. Both assert the `format_version` in §2.

As with every format here, a change is a **new version file** — `store-v3.md` — never an edit to this one.

## What changed from store-v1

[store-v1](store-v1.md) held one series per title. store-v2 holds all of them:

- `franchise` (`u32`, R) is **gone**. In its place is the list `franchise_v` / `franchise_o`: every
  Wikidata series (P179) the title is part of, **most specific first**, as raw Q-id numbers. A title is
  in more than one real series 219 times in the corpus, and some titles meet their siblings only through
  the second — The Batman, Spider-Man: Brand New Day and The Hobbit (oxyc/den-atlas#43).
- `format_version` is **2**.

store-v1 also called the franchise value an "entity id". It never was: the writer stored the raw Q-id
number, because the entity table holds almost no franchise entities. store-v2 says what the bytes are.

**Reading a store-v1 file.** Every other section is byte-for-byte the same shape, so a reader MAY accept
`format_version` 1 as well as 2, and when it does it MUST read v1's `franchise` column as a list of zero
(`u32::MAX`) or one Q-id. A v1 reader refuses a v2 store by its version, as §2 requires, rather than finding
`franchise` missing. [`vectors/store-v1.store`](../vectors/store-v1.store) is kept, frozen, as the last store
a v1 writer produced, so a reader can test that it still reads one.

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
| 8 | `u32` | `format_version` = 2 — a reader that does not know it MUST refuse |
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
| `card_title` | `u32` | R — string id. Wikidata's English label, disambiguator stripped |
| `card_poster` | `u32` | R — string id, `u32::MAX` = none. **OPTIONAL — no longer written** |
| `card_year` | `i16` | R — `i16::MIN` = none. The year of Wikidata's release date |
| ~~`votes`~~ | | **REMOVED — see below** |

**`card_poster` is optional**, as are the role lists, the source authors, `ent_imdb`, the person traits,
the birthplaces, the tentative facets, and the studio and award sections below; nothing else is. A reader must treat its
absence as "no poster paths", never as an error: the producer stopped writing it under oxyc/den#118,
because a poster path is licensed vendor content and the store is a public artifact. A reader wanting
artwork has two other sources — a batch metadata route, and the metahub URL keyed by IMDb id — and so
loses art rather than the card. A reader that takes the whole card map down when this section is missing
is the bug that removal was gated on, and den-atlas had exactly that shape.

Absence, not a version bump: the section directory is looked up by name, so a store without an optional
section is still store-v2 and every existing reader of the other sections stays valid.

**`votes` is gone, and a reader needs its own source of one.** It was what a browse row is ORDERED by, so
a title without one sorts by tmdbId and lands *La Job* (tv:5) next to *Game of Thrones* — and that is the
failure mode to design against, because it is silent. den-atlas joins IMDb's public
`title.ratings.tsv.gz` on the `imdb` column at load and refreshes it daily: 99.9% of the corpus, fresher
than a number frozen at build time, and it carries an average rating the store never had. A reader with no
such source must say so where an operator can see it rather than serve rows in id order.

It was removed for the same reason as the poster path — a vote count is a vendor's content and this file is
public — and the count it came from was read out of a TMDB batch tree, so dropping it dropped the last TMDB
artifact the writer touched.

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

`facet_v` holds only what the writer PUBLISHES: an answer that passed den-dataset's publication gates
(`store/facets.py`, FACETS-V2 § "Publication gates"). Everything else is absent there, whatever the reason.

#### Tentative facets — OPTIONAL

The same dense `R × 12` shape and axis order as `facet_v`.

| name | type | length |
|---|---|---|
| `facet_tv` | `u32` | R × 12 — string id, `u32::MAX` = no tentative value |
| `facet_tp` | `u8` | R × 12 — the value's **probability**, hundredths; 0 where `facet_tv` is absent |

An answer the gates refused only for being **uncertain** (probability under 0.70, or too thin a margin
over the runner-up), when it is still the model's best guess. The writer decides which: den-dataset's
`store/facets.py` names the floor and any axis or value left out, and the manifest's `facetGates` records
them with the counts. The published tier grades 9 to 10 correct in 10; the tentative tier about 8 in 10
(oxyc/den-atlas#35). A reader that shows one must list it after the published values and say it is
tentative.

- A cell is tentative only where `facet_v` is absent. The two tiers never hold a value for the same cell,
  so a reader may read them as one answer per cell: published, else tentative, else none.
- `facet_tp` is a probability from the answer's distribution. `facet_c` is the model's self-reported
  confidence, a different number. Do not compare one with the other.
- A refusal that is not uncertainty gives no tentative value: the wrong work, a non-narrative programme,
  `does-not-apply`, `ending=unknown`, an archetype outside a bounded narrative.
- A value may appear in `facet_tv` and nowhere in `facet_v`. It is in the one dictionary like any other.

The two are written together. **Absent means no tentative values, never an error**: a store written
before them is read exactly as before. One without the other, or either not sized `R × 12`, is malformed.

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

**`released` is not the corpus value.** An earlier draft of this document said it was stored "verbatim",
which is how the column shipped as 47,618 sentinels: the corpus holds `{"date": "2007-01-20",
"precision": "day"}` and a writer reading it as an integer finds none. It is resolved to days since the
epoch here, and its precision kept beside it — **more than half of dated rows are year-precision**, so a
reader that ignores `released_prec` dates 24,034 titles to 1 January.

Each is a `u32` values array of interned ids plus a `u32` offsets array:

`makers` (directors ∪ creators ∪ **screenwriters**, deduplicated, entity ids), `cast` (entity ids),
`broadcasters` (entity ids), `genres` (**TMDB genre ids, not entity ids and not interned**), `countries`
(string ids), `languages` (string ids), `alias_titles` (string ids — a title's OWN names first, `titles.en`
then `titles.orig`, THEN `titles.aliases`, deduplicated; `aliases` is a disjoint set that a title's own names
are vetted against, so writing it alone silently drops the original-language title of most of the corpus),
`franchise` (**raw Q-id numbers, not entity ids** — `Q42` is 42 — every series the title is part
of, most specific first as the facts order them, deduplicated, NOT sorted; a title in no series owns an
empty span).

The other Wikidata entity lists are the same shape, entity ids in the facts' order: `composers`, `dops`
(cinematographers), `distributors`, `companies` (production companies, P272), `locations` (narrative
locations), `subjects` (main subjects), `instance_of` and `based_on`. `based_kind` holds string ids.

**A franchise's name is its entry in the entity table**, looked up by the Q-id number in `ent_qid`, when it
has one. The facts stage names every series a title is in, so a current store names nearly all of them
(702 of 713 series on the published facts; the rest have no English label). A store written before that
names few, and a reader must treat a franchise with no entry as nameless, not as an error.

| name | type | length | meaning |
|---|---|---|---|
| `imdb` | `u32` | R | string id, `u32::MAX` = none |
| `released` | `i32` | R | **days since 1970-01-01**, `i32::MIN` = none. The corpus value is `{date, precision}`; this is that date resolved |
| `released_prec` | `u8` | R | 0 day · 1 month · 2 year · 3 decade · 4 century · `0xFF` none |
| `ended` / `ended_prec` | `i32` / `u8` | R | the same, for a series' last air date |
| `runtime` | `u16` | R | minutes, 0 = none |
| `orig_lang` | `u32` | R | string id of the first language, `u32::MAX` = none |

#### Roles — OPTIONAL

`directors` (P57), `creators` (P170) and `writers` (screenwriters, P58): the three credits `makers` is the
union of, kept apart, so a reader can tell a director from a writer. Same shape as `makers`: entity ids, in
the facts' order, deduplicated. On the published facts: directors 88.3% of titles, writers 67.2%, creators
6.8% (a series credit). Absent means a store written before them, and reads as three empty lists per row.

#### Source authors — OPTIONAL

| name | type | length | meaning |
|---|---|---|---|
| `src_authors_v` / `_o` | `u32` / `u32` | list per row | the authors (P50) of the works the title is based on (P144), entity ids |

Who wrote what the title is adapted from (oxyc/den-dataset#114): "films adapted from Stephen King" is a
question about the source work's author, which `based_on` cannot answer. One hop only: the author of each
`based_on` work, as Wikidata states it at best rank, deduplicated across the works and in Q-id order. A
source Wikidata names no author for (a character, a franchise, a folk tale, a film being remade) adds none,
and a screenwriter is not a source author. The authors are always in the entity table, named like any
entity, and asked for none of the person traits: they are not credits.

Absent means a store written before them, and reads as an empty list per row. Measured on the corpus's
47,539 titles against QLever's Wikidata dump: 9,503 (20.0%) are based on something, and 6,860 of those
(72.2%) on a work with an author — 3,893 distinct authors, Stephen King the most adapted at 86 titles.

### Entities

| name | type | length |
|---|---|---|
| `ent_qid` | `u32` | E — **sorted**, binary search |
| `ent_name` | `u32` | E — string id |
| `ent_tmdb` | `u32` | E — TMDB person id, `u32::MAX` = none |
| `ent_credits` | `u32` | E — titles crediting this entity, for rarity weighting |
| `ent_alias_v` / `_o` | `u32` | list per entity — the other names they go by, as string ids |
| `ent_imdb` | `u32` | E — string id of the IMDb person id (`nm…`, Wikidata P345), `u32::MAX` = none. **OPTIONAL** |

`ent_alias_*` holds STRINGS, not precomputed hashes. People search keys on a folded, normalised form of a
name, and a writer producing those hashes would be a second copy of that algorithm — the exact drift this
format exists to prevent. The reader folds them itself and drops the strings once its index is built.
64,075 of 162,812 entities have aliases, 118,958 in all; without them a search for "Michael James Vogel"
finds nothing while "Mike Vogel" works.

`ent_imdb` is a **join key, never an identity**: it lets a reader join IMDb's own principals at run time —
who is top-billed, say — the way the `imdb` column joins its ratings. The entity's public id stays its
Wikidata Q-id. Only person ids are written; a company's or a character's IMDb id is not. It comes from
Wikidata (a CC0 fact), never from IMDb's dumps, which may not be redistributed. Absent means no IMDb ids,
not an error; present, it has one entry per entity. On the published facts: 131,826 of 162,812 entities,
93.5% of credited people, and the people behind 98.4% of cast credits.

#### Person traits — OPTIONAL

| name | type | length | meaning |
|---|---|---|---|
| `ent_gender_v` / `_o` | `u32` / `u32` | list per entity | sex or gender (P21): entity ids of the items Wikidata names |
| `ent_citizen_v` / `_o` | `u32` / `u32` | list per entity | countries of citizenship (P27), entity ids |
| `ent_occupation_v` / `_o` | `u32` / `u32` | list per entity | occupations (P106), entity ids |
| `ent_born` | `i32` | E | date of birth (P569), days since 1970-01-01, `i32::MIN` = none |
| `ent_born_prec` | `u8` | E | 0 day · 1 month · 2 year · 3 decade · 4 century · `0xFF` none |
| `ent_died` / `ent_died_prec` | `i32` / `u8` | E | the same, for the date of death (P570) |

What Wikidata states about a person (oxyc/den#136), and nothing else: no gender is inferred from a name or
a photo, no nationality from a birthplace. Each is read at best rank, as `wdt:` reads it. A gender, a
country or an occupation is the item Wikidata names, stored as an entity id like any credit, so a reader
shows its `ent_name` — whatever values the data holds, which are not only male and female. The items a
trait names are always in the entity table; one Wikidata gives no English label is named by its Q-id, as
any unnamed entity is. Lists are in Q-id order.

A date is encoded as `released` is — days since the epoch and a precision code — with two differences a
reader must allow for. It reaches before the common era (a screenwriter credit can reach Sophocles), in the
proleptic Gregorian calendar, where year 0 is 1 BCE; and it may be known only to the decade or century. The
day is Wikidata's value cut to its precision: a year-precision date is 1 January of that year, and a reader
must not show it as that day. **Below a year, read only what the precision asserts.** A decade is the
year's decade. A century is ⌈year / 100⌉ and nothing finer: Wikidata writes "20th century" as any year
from 1901 to 2000, and on the corpus's people as 1953, 2000, 1901 and 1950 — 3,236 births, which a reader
bucketing by decade would scatter into the 1950s and the 2000s. Where Wikidata gives several best-rank
dates, the stored one is the earliest that no other refines (a bare 1946 beside 1946-06-14 is the day).

The ten sections are written together. **Absent means no traits, never an error**; some of them without
the others is malformed, as is any not sized to the entity table. Only credited people — cast, directors,
creators, writers, composers and cinematographers — are asked, so every other entity has none.

Measured over the 140,521 people the current facts credit: gender 98.7%, born 84.5%, died 25.0%,
citizenship 85.2%, occupation 98.8%; the people behind cast credits, weighted by credits, 99.9% / 96.0% /
29.4% / 96.1% / 99.7%. Wikidata holds 23 distinct gender values here (male 94,531, female 43,761,
non-binary 143, trans woman 142, …), and 71 people carry more than one.

#### Birthplaces — OPTIONAL

| name | type | length | meaning |
|---|---|---|---|
| `ent_bplace_v` / `_o` | `u32` / `u32` | list per entity | place of birth (P19), entity ids |
| `ent_bcountry_v` / `_o` | `u32` / `u32` | list per entity | the country (P17) of each of those places, entity ids |
| `ent_iso` | `u32` | E | string id of a country's ISO 3166-1 alpha-2 code (P297), `u32::MAX` = none |

Where a person was born (oxyc/den-dataset#114), so a reader can answer "born in Stockholm" and "born in
Sweden". The place is the item Wikidata names — a city, a village, a hospital, now and then a country — and
the country is Wikidata's own P17 on that place, not a guess from the person's citizenship; the two are
different questions and both are stored. Both are read at best rank, deduplicated, in Q-id order. A person
can have several (1,195 of the corpus's people have more than one place, 837 more than one country), and a
place Wikidata puts in no country gives none. The places and countries are always in the entity table,
named by their Q-id when they have no English label.

`ent_iso` is the code a reader resolves `SE` through. It is written for the countries a person's
citizenship or birthplace names, where Wikidata gives one at best rank, and is `u32::MAX` for every other
entity. A code may belong to more than one entity, and a reader matching a code matches all of them. Some
countries have none: historical states (the Soviet Union, the Kingdom of England), and the Netherlands
(Q55), whose `NL` is deprecated on Wikidata in favour of the Kingdom of the Netherlands (Q29999) — 1,167 of
the corpus's people are born in a place whose country is Q55. A reader must accept the entity's Q-id as
well as its code.

The five sections are written together. **Absent means no birthplaces, never an error**; some of them
without the others is malformed, as is any not sized to the entity table. Only credited people are asked,
as for the traits.

Measured over the 140,462 people the corpus's 47,539 titles credit, against QLever's Wikidata dump: a
birthplace for 77.0%, a birth country for 76.9%, a country with an ISO code for 75.8%; weighted by credits,
91.5% of credits name someone with a birthplace. 20,000 distinct places, in 338 countries, 136 of them with
no code.

### The inverted maker index

| name | type | length |
|---|---|---|
| `maker_ent` | `u32` | m — **sorted** entity ids |
| `maker_rows_v` / `maker_rows_o` | `u32` / `u32` | list — rows crediting that entity |

`titles_sharing_makers` was a linear scan of every record, per request: 83.7 µs, and 158.7 µs at 2× corpus.
Indexed it is **0.4 µs and flat**. 355 KB.

### Iconic studios — OPTIONAL

`S` = studio count.

| name | type | length | meaning |
|---|---|---|---|
| `studio_qid` | `u32` | S | the studio's own Wikidata item, raw Q-id number, **sorted** — the id a studio page is addressed by |
| `studio_name` | `u32` | S | string id: what a viewer calls it, which is not always Wikidata's label (Golden Harvest) |
| `studio_ent_v` / `_o` | `u32` / `u32` | list per studio | entity ids of every credited item that is this studio |

The production companies a viewer browses by: a house style (Ghibli, Aardman, Hammer) or a curation (A24,
Searchlight). A title's studios are its `companies` entries found in `studio_ent_v`. They are a hand-kept
list in den-dataset, `data/iconic-studios.json` (oxyc/den#132), because no measure over plots or credits
finds the curatorial labels; the writer ships the ones the corpus credits.

One studio is often several Wikidata items — a TV or animation arm, a renamed item, a duplicate — so a
studio lists all of them, and a reader counts a title crediting Toho Animation as Toho's. An item belongs to
at most one studio. The studio's own item need not be credited itself: a corpus crediting only HBO Films
still has HBO.

**Absent means no iconic studios, never an error**, as with `card_poster`: a reader built before these
sections existed ignores them, and a reader built after must still open a store-v2 written without them. That
is why they are an addition to store-v2 and not a store-v3. The sections may also be present and empty.

### Awards — OPTIONAL

`C` = ceremony count.

| name | type | length | meaning |
|---|---|---|---|
| `ceremony_qid` | `u32` | C | the ceremony's Wikidata item, raw Q-id number, **sorted** |
| `ceremony_name` | `u32` | C | string id: its English label, or `Q<n>` when it has none |
| `award_v` / `award_w` / `award_o` | `u32` / `u8` / `u32` | list | per title: ceremony (an index into `ceremony_qid`), and 1 if it won at least one award there, 0 if it was only nominated. `award_v` and `award_w` share `award_o` |

A title's awards are the ceremonies it was recognised at, one entry per ceremony, in ceremony-table
order: won beats nominated, so a title that won Best Picture and was nominated for Best Director carries
the Academy Awards once, as won. The categories themselves are not stored.

They come from Wikidata alone: P166 (award received) and P1411 (nominated for) on the title, and each award
item filed under its ceremony — a "group of awards" (Q107655869) the item is part of (P361) or an instance of
(P31), else the item itself when it is one, else the body that confers it (P1027), which is how a festival
prize names its festival. An award none of those find is not stored.

Measured on the published facts (47,618 titles): 8,200 titles (17.2%) carry P166 or P1411; 2,337 distinct
award items, of which 1,944 file under a ceremony, covering 91.6% of title–award pairs; 7,447 titles (15.6%)
end up with at least one of 362 ceremonies, 4,834 of them with a win. The largest unfiled item is
"International Submission to the Academy Awards" (1,262 titles), which is not an award and is right to be
left out. One body can still appear twice — the National Board of Review as its organisation (through
P1027) and as its "Awards" group — because Wikidata files some of its prizes one way and some the other.

**Absent means no awards, never an error**, as with the studio sections.

### Vectors

| name | type | length |
|---|---|---|
| `vec_plot` | `i8` | R × 1024 |
| `vec_premise` | `i8` | R × 1024 — all-zero for a row with no premise vector |
| `vec_plot_has` | `u8` | R — 1 where the row has a plot vector |
| `vec_premise_has` | `u8` | R — 1 where the row has a premise vector |

**`vec_plot_has` is the question you want; `facts_has_vec` is not it.** The latter is the facts field of
that name — a scrape-time claim that a vector was expected — and on the real corpus the two disagree on
**9,013 rows**: 9,010 carry a vector while `facts_has_vec` reads 0. It was originally called `has_vector`,
two entries from `vec_premise_has`, which is exactly the confusion the name invited.

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
