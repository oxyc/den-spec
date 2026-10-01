# Library wire format, v4 — proposal (revision 4)

**Status: draft for audit.** Nothing here is implemented.

v4 stores a library as **documents**, one per title and one per season, with delivery receipts in documents of
the same shape per tracker account. Keys, sealing, settings rows and the log protocol are [library v2](library-v2.md);
stamps, merges, derived state, writes and tracker delivery are [library v3](library-v3.md) §3–§7 restated for
documents. A v4 client follows every rule there unless a section below replaces it. Vectors:
`../vectors/library-v4.json` (§15 lists what they MUST pin). Every rule is implemented once, in den-core, as ops both
clients call (§14); a client never re-implements a merge, write, decision or encoding.

Words: *MUST* is a rule a client breaks at the cost of other clients; *should* is advice.

## What changes from v3

| | v3 | v4 |
|---|---|---|
| A film | a `rec` row and a `wat:movie:<id>:0:0` row | one **title document** (§3) |
| A series | a `rec` row and one `wat` row per 32 episodes | one title document and one **season document** per season (§3) |
| How state is split | by block, `floor(episode / 32)` | by coordinate (title, season), never by size; no spill, shard or split |
| Plaintext | UTF-8 JSON | `0x00` + raw DEFLATE of RFC 8785 JCS, with a per-document device table (§4) |
| Receipts | `snt` rows per watch row, and 4,096 hashed title shards | one **delivery document** per account and title, and per account and season (§9) |
| Rules on den-edge seqs | `seededThrough`, the `seedBound` and `connectedFrom` windows, handoff `head` | none: a seq is only a compare-and-set base, and the lease row's is observed for liveness (§9) |
| Per-change history | none (v3 folds `ep` rows and v1 events) | none; v4 writes neither and folds any it finds (§8, §11) |
| den-edge value cap | 32 KiB | 256 KiB for a library at minimum 4 (§13) |
| Switch | from v2: drain, handoffs, facade, waiting commands | from v3: one fenced rewrite by any ready device (§10) |

Everything else — keys, names hashed with `macKey`, AES-GCM sealing, settings rows, credentials in `set:trackers`,
`set:deliver` (less three settings v4 ignores), the lease, the generation and wire-minimum headers — is unchanged.

## 1. How a library moves to v4

There is **no mixed period**: no library is written in both formats at once.

1. **Ready builds.** v4-capable builds read and write **v3 only** on a v3 library, deliver under v3 §6, send
   `x-den-wire: 4` on every request (§11), and report `format` 4 for their device (§10).
2. **The switch.** Once every device is ready and the delivery gate holds (§10), any ready device rewrites the library
   into its v4 form and raises its wire minimum to 4 in one fenced, atomic step.
3. **v4.** From then on every client writes v4 only. den-edge refuses any request without `x-den-wire ≥ 4`, so a
   v3 build can neither write nor delete; once updated, it writes its kept work back in v4 form (§11).

## 2. Guarantees

v3 §2's guarantees hold, restated where documents change them:

1. **Convergence.** Clients that have read the same document versions derive the same state, in any arrival order
   (clock caveat as v3 §2.1).
2. **No lost update.** Changes to different fields, episodes or seasons never overwrite each other; changes to the
   same one resolve by its merge rule (§6). Two writers of one document meet at its compare-and-set and merge.
3. **Monotonic reads.** Content never moves between rows: a coordinate lives in exactly one document name for the
   life of the library. A reader that has read the log to any point holds, for every document, a version that is a
   merge-ancestor of the current one, so a partial read only ever holds older state, never less state.
4. **No lost state at the switch.** The v4 form derives, for every coordinate and title, the state v3 derives from
   the log at the rewrite's `base`, and replaces it atomically while no other write can land (§10).
5. **Imports never override a person** (v3 §2.4).
6. **Bounded growth.** Rows grow with titles and seasons — per account, one delivery document per title and per
   season with a receipt — never with changes. A document's size grows only with its season's episodes and is
   capped (§4).
7. **Idempotence.** Replaying any write, any number of times, in any order, changes nothing further.
8. **Nothing delivered twice, nothing dropped.** One device at a time delivers for an account; a settled change is
   not sent again, across the switch and restores included; an unsettled or held one stays pending (known limits:
   §9, §11).

## 3. Documents

A document is a JSON object whose **identity fields** name it. Every document carries `"v": 4`, its `kind`, and its
identity:

| Kind | Name | Identity fields | Holds |
|---|---|---|---|
| `title` | `title:<type>:<id>` | `title` | a film: every v2 `rec` field and its watch register; a series: every v2 `rec` field |
| `season` | `season:tv:<id>:<season>` | `title`, `season` | a series season: its `seasonReset` and every episode register |
| `delivery` | `dlv:<provider>:<account>:<type>:<id>` | `provider`, `account`, `title` | an account's receipts for a title's own targets (§9) |
| `delivery` | `dlv:<provider>:<account>:tv:<id>:<season>` | `provider`, `account`, `title`, `season` | an account's receipts for one season's episodes (§9) |

- `title` is `{"type": "movie" | "tv", "id": <TMDB id>}` as v2 §3; `<type>` and `<id>` (canonical decimal) spell it
  in the name. `season` is a non-negative integer, spelled in canonical decimal. Season 0 (specials) is a season like
  any other. `provider` and `account` are v3 §6's provider name and account id.
- **Names** are hashed into the row key as v2 §2: `k` = hex HMAC-SHA256(`macKey`, name). A reader that opens a row
  rebuilds the name from the document's identity fields and drops the row if its HMAC is not `k` (§4 *Rejection*).
- **The split is fixed by coordinate.** A film is always one document; a series always one title document plus one
  season document per season it has state in. No rule moves content between documents, and no rule depends on a
  document's size other than the caps of §4.
- A document is created by its first write (`base` 0). Rows are never removed (v2 §3).
- **Settings rows** (`set:<name>`) are v2's and v3's, unchanged and uncompressed (§4).
- **Rows of other kinds** — a kind this version does not know — are kept with `k` and `v` unchanged.
- **v2 and v3 rows** (`rec`, `wat`, `snt`, `ep`, `set:tracker-event:*`) never appear in a v4 library except as
  strays: §11 says what a reader does with one.

### Title document — `title:<type>:<id>`

Its known fields are v2 §3's `rec` fields, with v2's meanings and value sets: `status`, `resume`, `reaction`,
`deleted`, `dismissed`, `episodesReset`, `addedAt`, `watchedAt`. A **film** document also has `watch`: the film's
watch register, `{"plays", "cleared"}` with v3 §3's meanings (v3's `"0"` register of `wat:movie:<id>:0:0`). A
`watch` on a `tv` document, and `episodesReset` on a `movie` document, are kept and read by no rule.

An **absent** field keeps v3's meaning: an absent stamped field counts as `[0, 0, ""]` (import-owned, v3 §7), which
is not the same as a present field with a stamped default. A writer never adds a field it did not write.

A film (stored form, before compression; §4 explains `devices` and the integer device positions):

```json
{
  "v": 4, "kind": "title",
  "title": {"type": "movie", "id": 550},
  "devices": ["0f1e2d3c4b5a6978", "a1b2c3d4e5f60718"],
  "status": {"value": "watched", "at": [1789000000000, 0, 1]},
  "resume": {"value": 1, "at": [1789000000000, 0, 1], "viewing": 1, "seconds": 8340},
  "reaction": {"value": "love", "at": [1789000100000, 0, 0]},
  "deleted": {"value": false, "at": [1788000000000, 0, 1]},
  "dismissed": {"value": false, "at": [1788000000000, 0, 1]},
  "addedAt": 1788000000000,
  "watchedAt": 1760000000000,
  "watch": {
    "plays": {"-9005499254740992": 1700000000000, "0": 1760000000000, "1": 1789000000000},
    "cleared": null
  }
}
```

A series:

```json
{
  "v": 4, "kind": "title",
  "title": {"type": "tv", "id": 1399},
  "devices": ["a1b2c3d4e5f60718"],
  "status": {"value": "inProgress", "at": [1789000000000, 0, 0]},
  "reaction": {"value": "like", "at": [1789000200000, 0, 0]},
  "deleted": {"value": false, "at": [1788000000000, 0, 0]},
  "dismissed": {"value": false, "at": [1788000000000, 0, 0]},
  "episodesReset": [1788500000000, 3, 0],
  "addedAt": 1788000000000,
  "watchedAt": null
}
```

### Season document — `season:tv:<id>:<season>`

```json
{
  "v": 4, "kind": "season",
  "title": {"type": "tv", "id": 1399}, "season": 1,
  "devices": ["0f1e2d3c4b5a6978", "a1b2c3d4e5f60718"],
  "seasonReset": null,
  "episodes": {
    "1": {
      "progress": {"value": 1, "at": [1788900000000, 0, 0], "viewing": 0, "seconds": 3120},
      "imported": false,
      "plays": {"0": 1788900000000},
      "cleared": null
    },
    "2": {
      "progress": {"value": 1, "at": [1789000000000, 0, 1], "viewing": 1, "seconds": 3000},
      "imported": false,
      "plays": {"-9005499254740992": 1700000000000, "0": 1760000000000, "1": 1789000000000},
      "cleared": null
    }
  }
}
```

- `seasonReset`: a stamp or null (v3 §3), one per season.
- `episodes`: key → **register** with v3 §3's fields and rules: `progress`, `imported`, `plays` (with the 8-kept
  selection), `cleared`. A **valid key** is the canonical decimal form of an integer `0 ≤ e ≤ 99999` (v3's range,
  without the block condition). A reader derives nothing from any other key and keeps it (§6 *Unknowns*).
- Numbers and stamp bounds are v3 §3's, unchanged.

## 4. Encoding

### Plaintext

A row's sealed plaintext (v2 §2) is one of:

- **Compressed:** the byte `0x00`, then raw DEFLATE (RFC 1951) of the UTF-8 JCS (RFC 8785) of the document's
  **stored form**. Every document is written this way.
- **JSON:** UTF-8 JSON starting with `{` (`0x7B`). Settings rows are written this way, and v2/v3 rows and rows of
  unknown kinds are kept this way.

A reader accepts both for every row and tells them apart by the first byte. Settings stay uncompressed because they
hold credentials: compressing secret bytes beside content another party can choose is what makes compression leak.
Documents hold no secrets.

### Logical and stored form

- The **logical form** of a document is the JSON object with every device id written as a string and no `devices`
  member. Every rule — merge, ties, equality, no-op detection, derived state, delivery — reads the logical form, and
  equality is equality of its JCS bytes.
- The **stored form** adds `devices`: the distinct device ids found in the document's **indexed positions** (below),
  `""` and `"local"` included when present, sorted by their UTF-8 bytes, without duplicates. In the stored form a
  writer writes every indexed device id as its integer index into `devices`. A reader accepts, in an indexed
  position, either a string or an index; an index outside `devices` rejects the document.
- **Indexed positions** in `v: 4`, a closed list:
  - title documents: the third element of `status.at`, `resume.at`, `reaction.at`, `deleted.at`, `dismissed.at`,
    `episodesReset`, and of the stamp in `watch.cleared`;
  - season documents: the third element of `seasonReset`, and of each valid-key register's `progress.at` and
    `cleared` stamp;
  - delivery documents (§9): in each entry of a known shape, the third element of its value stamp and of its settle
    order.

  Nothing else is ever indexed: not unknown fields, not entries under invalid keys, not entries of unknown shape. A
  later revision that adds a stamp under `v: 4` MUST write its device id inline as a string, so an older writer that
  rebuilds `devices` never re-points it. Unknown fields are kept byte for byte in the logical form (§6).
- Encoding is a function of the logical form: `encode(L)` = `0x00 ‖ DEFLATE(JCS(stored(L)))`. Two writers holding
  the same logical form store the same JCS.

### Compression

den-core compresses and decompresses with **one pinned DEFLATE implementation and level**; no client uses a
platform compressor. Compressed bytes may differ between den-core versions, and **no rule depends on them**: every
comparison is on the logical form, as above. (Sealed bytes differ on every write anyway: v2's nonce is random.)

### Bounds and rejection

A reader **rejects** a row whose plaintext:

- inflates past **8 MiB**, or nests JSON deeper than **32** levels;
- is not valid UTF-8 JSON, or holds a string JCS cannot serialize (a lone surrogate);
- is a document with no integer `v`, a known field of the wrong shape, an index outside `devices`, or a `devices`
  that is not sorted and duplicate-free;
- names a different row: the name rebuilt from its identity fields does not HMAC to its `k` (v2 §2).

A rejected row is ignored for state and **never overwritten**: a client holds every write it would make to that
document until the row reads valid, and shows that the title cannot be read. While any row of the log is rejected,
**no device delivers for any account**: a rejected row may be a delivery document, and a target read without its
receipt could be sent. The offering clients show why.

A document whose `v` is greater than 4 is read for the fields this spec defines, if it decodes by §4's rules (v2 §6:
a client keeps reading a newer row), and is **never written**: writes to it are held until the build updates, and
every target it holds or names is held (not decided).

### Size

den-edge's value cap for a v4 library is **256 KiB** of `v`, the base64url text of `nonce ‖ ciphertext ‖ tag`
(§13). That is 196,580 bytes of compressed plaintext. **A writer never sends a document whose sealed value exceeds
256 KiB or whose stored JCS exceeds 8 MiB**, so no reader rejects a document a conforming writer made. A write that
would exceed either is not sent: the client keeps it, shows the title as full, and a lease holder treats it as v3
§6's write refused as too large (it stops delivering for that account until a write succeeds).

The cap is set by the worst-case season document, the largest document v4 has. Measured with zlib at level 9 (the
pinned implementation will differ by a few percent):

| Season document, per episode | JCS | Compressed | Episodes in 256 KiB |
|---|---:|---:|---:|
| Typical: watched once, value 1, one play | ~140 B | ~17.5 B | ~11,000 |
| Heavy: 8 plays, fractional progress and seconds, a stamped `cleared` | ~320 B | ~99 B | ~1,980 |
| Maximal: every number at v3 §3's bound, 8 plays, random digits | ~530 B | ~224 B | ~870 |

The longest seasons in practice are daily programmes listed as one season, a few thousand episodes, viewed about
once each; they fit with room to spare. A delivery document's entries are of the same order (a typical episode
receipt ~45 B of JCS, v3's worst case ~520 B with a full `sending`). v3's 32 KiB would hold ~1,400 typical episodes,
so a cap by coordinate needs the higher value. The stored JCS of a document at the cap is under ~1.5 MiB for every
row in the table; the 8 MiB inflate cap leaves room for documents that compress far better (an imported season with
no plays) while bounding what one row can make a TV allocate.

## 5. Stamps

v2 §4 and v3 §4, unchanged: hybrid logical clock, timeless when `t == 0` whatever `c` and `d`, merges on stamps as
stored, deriving and deciding treat a stamp more than a day ahead as timeless, issuing past a seen reset, and ties
broken by the byte-greater JCS — always of the **logical** form, so a device index never decides a tie.

## 6. Merge

A merge of two versions of one document MUST give the same result in either order and in any grouping, and merging
a document with itself changes nothing. Merges run on logical forms.

- **Title documents**: v2 §5 per field — `status`, `reaction`, `deleted`, `dismissed` by the later stamp; `resume` by
  viewing, then value, then stamp, then `seconds`; `episodesReset` by the later stamp, null lowest; `addedAt` the
  minimum; `watchedAt` the minimum of the non-null values. A field only one version has is kept. `watch` merges as a
  v3 register: `plays` by v3 §3 (union, the lesser watchedAt per key, then the least key and the 7 greatest), `cleared`
  by v3 §3.
- **Season documents**: `seasonReset` by the later stamp, null lowest; `episodes` per valid key, each register by v3
  §3 (`progress` by v2 §5's progress rule, `imported` by OR, `plays` and `cleared` as above). A key only one version
  has is kept.
- **Delivery documents**: §9.
- `v`: both versions are 4 (a writer never writes a higher one).
- **Ties** in any per-field rule: v3 §4 (byte-greater JCS of the logical field).

### Unknowns

A document keeps what it does not understand, at three levels:

- **Document level**: its **unknown set** — every top-level member this spec does not define for its kind, together
  with every entry under an invalid key of `episodes` (season) or `entries` (delivery). The unknown set comes,
  wholesale, from the version whose **newest stamp** is later: for a title or season document the greatest stamp in
  its indexed positions; for a delivery document the greatest settle order of its entries. Equal newest stamps: the
  version whose unknown set has the byte-greater JCS. (The tie compares the unknown sets themselves, not whole
  versions, so the choice stays a join.)
- **Entry level**: a delivery entry of unknown shape (§9).
- **Register level**: members of a register (an episode register, or a film's `watch`) this spec does not define,
  wholesale from the version whose register's newest stamp — over its `progress.at` and `cleared` stamp — is later,
  then by the byte-greater JCS of the members.

A v4 writer never adds an unknown member, never changes one, and never indexes a device id inside one.

## 7. Deriving state

v3 §5, unchanged, reading:

- an episode's register from its season document, and its **covering resets** from the series title document's
  `episodesReset` and the season document's `seasonReset`;
- a film's status, resume and reaction from its title document, and its plays and `cleared` from its `watch`.

## 8. Writing

Every write is a set-to-value applied to the logical form of the documents it touches, decided by den-core
`apply_write` (§14) on the stored state, exactly as v3 §7 decides it on rows:

| Write | Touches | Rule |
|---|---|---|
| Playback progress, finishing by playing | the season document (episode); the title document (film: `resume`, and the play in `watch` on reaching 0.95) | v3 §7 *Playback*, *Films* |
| Mark watched (episode, season, series, film) | each season document with an episode it marks; the film's title document | v3 §7 *Mark watched*; a film's status, resume and play are one document write |
| Un-watch (episode, film) | the season document; the film's title document (`cleared` and `resume` in one write) | v3 §7 *Un-watch*, *Films* |
| Rewatch | as playback | v3 §7 *Rewatch* |
| Season reset | the season document's `seasonReset` | v3 §7 |
| Series reset | the series title document's `episodesReset` | v2 |
| Episode import | the season document | v3 §7 *Import*, without its viewing-window paragraph (§9 *Removed rules*) |
| Film import, title imports (rating, watchlist add, watchlist removal) | the title document | v3 §7 *Title imports* |
| Status, list, reaction, delete, dismiss | the title document | v2 §3 |

- **Protocol.** A writer reads the document at seq `s`, applies the write, encodes, and sends `{k, base: s, v}`
  (v2 §2). On a conflict it re-derives the write from the returned version (v3 §10 *Batches are not atomic*) and sends
  again, with jittered backoff, until the write is applied or no longer holds. A writer skips a write whose resulting
  logical form equals the one it read.
- What v3 calls "in the same batch" for a film (status with its play; `cleared` with the bump) is now one document
  write, so it is atomic.
- **No history rows.** A v4 client writes no `ep` row, no `set:tracker-event:*` row and no per-change record of any
  kind, on any path. History is what the documents hold: plays with their watchedAt, as in v3.
- **Imports** run only after pulling the log to its head (v3 §7).

## 9. Tracker delivery

v3 §6 applies — accounts, the one channel, credentials, the lease, the `removals` latch, targets and values, the
command table, regressions, receipts with settle order, timeless values, unverified receipts, `decide`, intents
(`sending`), ratings, remote plays, floors, rewatch, un-watch then re-mark, against a `u` receipt, earlier viewings,
settling, Remote → Den — with the changes below. Nothing in delivery reads a document's seq except as a write base,
and the lease row's seq for v3 §6 *Taking*.

### Account settings

`set:deliver:<provider>:<account id>` keeps `since`, `lease`, `removals` and `unverified` with v3's rules, and is
still a settings row. v4 reads none of `seededThrough`, `seedBound` or `connectedFrom`: they stay as the switch
carried them (§10), no v4 client writes them, and a re-switch from a restored v3 log reads them there (§11).
Write-back writes back `since` only.

### Delivery documents

```json
{
  "v": 4, "kind": "delivery",
  "provider": "simkl", "account": "4812736",
  "title": {"type": "tv", "id": 1399}, "season": 1,
  "devices": ["", "0f1e2d3c4b5a6978", "a1b2c3d4e5f60718"],
  "entries": {
    "1": ["w", 0, 1788900000000, [1788900000000, 0, 1], [3, 41, 2]],
    "2": ["w", 1, 1789000000000, [1789000000000, 0, 2], [3, 42, 2], [[-1, 1700000000000]]],
    "3": ["n", 0, null, [0, 0, 0], [1, 7, 1]]
  }
}
```

```json
{
  "v": 4, "kind": "delivery",
  "provider": "trakt", "account": "7781203",
  "title": {"type": "movie", "id": 550},
  "devices": ["0f1e2d3c4b5a6978", "a1b2c3d4e5f60718"],
  "entries": {
    "watch": ["w", 1, 1789000000000, [1789000000000, 0, 1], [4, 9, 1]],
    "list": ["out", [1789000000000, 0, 1], [4, 9, 1]],
    "rating": ["love", [1789000100000, 0, 0], [4, 10, 1], 8]
  }
}
```

- A **season delivery document** holds the account's episode receipts for one season, keyed by episode (valid keys
  as §3). A **title delivery document** holds the title's own targets, keyed `watch` (films only), `list` and
  `rating`. They replace v3's `snt:…:<watch row name>` rows and `snt:…:t<shard>` rows. Names use the account id, not
  a key-derived value, so a key reset or link carries them intact (§12).
- **Entries** keep v3 §6's shapes and meanings, unchanged:
  - watch (episode, film): `[class, p, watchedAt, valueStamp, settleOrder, sending?]`, class `w`/`u`/`n` (an `n` with
    `p` −1 as v3 §6 *Earlier viewings*);
  - list: `[class, valueStamp, settleOrder]`, class `in`/`gone`/`out`;
  - rating: `[reaction, valueStamp, settleOrder, remoteRating?]`;
  - owed as baseline: `["b", value, valueStamp, settleOrder]` (v3 §6 *Seeded accounts*, carried from v3);
  - **open**: `["o", settleOrder, inner]` (below).

  The indexed positions (§4) are the third element of `valueStamp` and of `settleOrder` in each shape (for `o`, of its
  `settleOrder` and of the inner entry's positions). `sending` holds times, not stamps, and is never indexed.
- An entry of **unknown shape** (an unknown first element, or a known one with the wrong arity or types) is kept. Two
  versions of one key merge by settle order when both are of known shapes; when either is of unknown shape, the
  byte-greater JCS wins. A target whose entry is of unknown shape is **held** (`unknown_receipt`), never decided as
  if it had no receipt.
- **Merge**: per key, by settle order; at equal settle orders an open entry beats the entry it wraps; then v3 §4's
  JCS tie. Keys only one version has are kept; unknowns by §6.

### Open entries

`["o", <settle order>, <inner>]`, `inner` null or an `n` entry with `p` −1 (whose settle order then equals the
wrapper's). An open entry carries v3's one seq-based case across the switch: **a target whose entry is open is
decided exactly as v3 §6 decides a target without a receipt whose row has seq above its account's `seededThrough`**
— with `inner` null as no receipt, with an inner `n` at −1 as that entry — including *Seeded accounts*' "counts as
stamped later than `since`, and all its plays count" for *Rewatch* and *Earlier viewings*, and *Earlier viewings*'
"never for a target above `seededThrough`".

- Open entries are written only by `v4_form` (§10) and by write-back of converted v2 work (§11), and only for an
  account whose `set:deliver` row holds `seededThrough`.
- A write that leaves the target's entry an `n` at `p` −1 (v3's imported-first step and its settle) writes it as the
  inner entry of an open entry with the new settle order; any other settle replaces the open entry with the settled
  entry.

### Pending

- A target is **pending** when its current value differs from its receipt (class, viewing, or field value) and is not
  a regression (v3 §6).
- A target with **no receipt** is pending when its value stamp is later than the account's `since`; otherwise it is
  caught up additively — watched, a list add, a rating — as v3 §6 *No receipt*, and any other value settles silently.
- A target with an open entry: as above.

### Writing delivery documents

- **Who.** Only the account's lease holder writes its delivery documents, except write-back after a generation
  change (§11), which merges only entries that were already settled (intents included) and open entries, and
  `v4_form` and moves to another key (§10, §12).
- **Compare-and-set.** The holder writes a delivery document only by a batch whose `base` is the document's seq it
  read before deciding the commands the write settles, checking the lease immediately before the write (v3 §6).
  Settles decided against one read of a document go in one write.
- **Chaining.** A holder may instead base a write on the seq its **own previous write** to that document produced,
  provided no read since has shown that document at a seq other than that one. An intent and the settle that clears
  it are chained this way (v3 §6 *Intent*). A write whose outcome is unknown ends the chain: the holder re-reads the
  document; if it holds the write's entries at their settle orders, the write was applied and the chain continues
  from the read seq; otherwise every settle in it is decided again.
- **Conflict.** A conflicted or lease-lapsed write is **discarded**, never merged and resent: the holder re-reads,
  and every target in it is decided again in a later pass against a fresh snapshot (v3 §6). A conflicted intent sends
  nothing. Kept refused writes (§11) never include a delivery document or a `lease`.
- **State at head.** A holder decides a pass only on state read to the log's head (documents and delivery documents
  alike), and pulls imports only after reading to the head.
- The epoch rule of a lease take reads every settle epoch in the account's delivery documents (v3 §6 *Accounts*).

### Removed rules

Every v3 rule that exists for the v2→v3 switch or compares a row's den-edge seq is gone from v4:

- *Seeded accounts*' `seededThrough` test and its compaction renumbering — replaced by open entries, set once at the
  switch (§10).
- §9's handoff qualification (`head`, `headAt`, `readyFrom`, `unsettled`, `held`, `discarded`), facade, `delivers`,
  waiting commands and drain — v4 has no v2 outbox.
- The `seedBound` and `connectedFrom` windows: v3 §9's acknowledgement of a viewing by a play within a day of its
  watchedAt (Trakt and Simkl) and of a `[p, null, T]` element, and v3 §7's import matching windows. The switch waits
  until no pending target depends on them (§10 *Delivery gate*). `[p, null, T]` elements are carried in `sending`
  and read by no v4 rule; writers keep them as v3 does. **Known limit**: a v2-era Trakt scrobble, recorded at the
  tracker's server time rather than at the viewing's watchedAt, may be written as an imported play on a later pull.
  Plays never set watched state and the 8-kept rule bounds them, so only play history changes.

## 10. The switch

A library becomes v4 in one step: den-edge replaces its log with the **v4 form** and raises its wire minimum to 4,
atomically, by v3 §9's fenced rewrite.

**Who and when.** Every device reports `<d>.format` = `{"int": 4}` in `set:devices`. The switch is **offered** once
every device whose `<d>.seen` is stamped within 180 days reports format 4, every `tv`-kind device reports format 4
whatever its `seen` unless a person removed it, no device has settings but no `seen`, no row of the log is rejected
(§4), and the delivery gate holds. It is **performed** by any ready device that has read the log to its head;
delivery state is in the log, so no drain or handoff applies.

**Delivery gate.** For every account whose `set:deliver` row holds `seedBound`, no target may be pending under v3 —
nor an unverified entry that equals its current value and awaits its re-check — in a viewing that v3 §9's windows
cover: one with a visible Den play whose watchedAt is at or before `seedBound`, or one with a `[p, null, T]` element
in that account's entry. While any is, the switch is not offered; the offering client names the titles, and the
lease holder delivers them under v3. With the gate held, no pending decision depends on a removed rule, so for every
target the v4 decision equals v3's at the switch (§15).

**Sequence.**

1. Open the rewrite and take `base`. From here until commit or abort the performing device sends no tracker command
   and no scrobble `stop`.
2. Read the log through `base`; re-check the offer and the gate on that read; derive the v4 form from every row
   through `base`.
3. Stage it and commit `{base, wireMin: 4}` with `x-den-wire: 4`. On `409`, abort, and start over from 1. A check that
   fails at `base` aborts, and the rewrite is opened again only once it passes on a fresh read.

**The v4 form** (den-core `v4_form`), from v3's compaction form of the log through `base` (den-core `v3_compact`:
`wat` and `snt` rows merged, stray `ep` rows and v1 events folded through v3 §8):

- **Title documents.** For each title, the `rec` row's fields and unknown fields; for a film, `watch` = the `"0"`
  register of `wat:movie:<id>:0:0` (`plays`, `cleared`, and its other members as register-level unknowns). A `wat`
  row with no `rec` row makes a title document with no `rec` fields.
- **Season documents.** For each series and season, the union of its blocks' valid-key registers; `seasonReset`
  from block 0 (v3 ignores it on other blocks, so it is dropped there); the unknown set is the blocks' row-level
  unknown fields and invalid keys, chosen by §6's rule as if each block were a version of the document.
- **Delivery documents.** Every v3 receipt carried as stored, settle order and all: an episode's entry into its
  season delivery document; a `t<shard>` row's `rec:<type>:<id>#watch|list|rating` entries into that title's
  delivery document. A `t<shard>` row's unknown fields are copied into every delivery document made from it (a join,
  so copies are harmless); an invalid key that names a title (`rec:<type>:<id>#<x>`) becomes an invalid key of that
  title's delivery document; any other invalid key of a `t<shard>` row has no title to belong to and is dropped.
- **Seeding.** For every account and every target with no receipt or an `n` entry at `p` −1, v3's rules decide what
  v4 stores, so no target's delivery behaviour changes:
  - when the account's `set:deliver` holds a `seededThrough` at or below `base`, and the target's v3 row has seq
    above it — the `wat` row for an episode; for a film's watch, the greater seq of its `rec` and `wat` rows; the
    `rec` row for a list or rating; a folded row taking the greater of its parts' seqs (v3 §9) — `v4_form` writes
    the open entry `["o", [0, n, <performer>], null]` (`n` counting from 1 per account), or wraps the `n` entry as
    `["o", <its settle order>, <it>]`;
  - otherwise it stores nothing new: v4's no-receipt rule (§9 *Pending*) reads the same `since` and decides as v3
    does.
- **Settings.** Every `set` row except `set:tracker-event:*` is staged with `k` and `v` unchanged, `set:deliver`
  and `set:trackers` included; the commit's new generation ends every lease (v3 §6).
- **Unknown kinds.** Every row of a kind `v4_form` does not know is staged with `k` and `v` unchanged. A v3 row
  that v3 itself drops (failed identity) is dropped.

It holds no `rec`, `wat`, `snt`, `ep` or `set:tracker-event:*` row. `v4_form` **fails**, and the switch is not
performed, when the form would exceed den-edge's 50,000-row limit or its stored-bytes cap, when any document would
exceed §4's caps, or when a v3 row carries a `schema` above 3 (a `rec` above 2); the offering client shows why,
naming the title.

## 11. Clients around the switch, restores and generations

- **Headers.** Every request from a v4-capable build to `/lib/{id}/…` carries `x-den-wire: 4` and
  `x-den-generation`, from install, before the switch included (`0` when creating a library). A batch's rows and its
  `x-den-generation` come from the same read.
- **Ready builds write v3 until the switch**, and deliver under v3 §6. A ready build observes the commit as a
  generation change with minimum 4.
- **v3 builds after the switch** get `426 {"error": "upgrade_required", "min": 4}` on every request but v3 §10's
  single home-check exemption, keep their writes as v3 §10 says, and once updated write them back in v4 form (below).
- **Refused writes are kept** — on `409 rewrite_in_progress`, `409 generation_changed` or `426` — durably, as
  logical writes (ops), never as encoded rows, and sent after `Retry-After`, through write-back when the generation
  changed. A kept write never includes a delivery document or a `lease`.
- **Write-back after a generation change** (a switch or a restore), in v4 form only. The client forgets its head and
  bases, reads from 0, and for every document it holds writes the merge of its copy with the log's when that differs
  from the log's (§6), applies its kept writes as ops, merges every **settled** delivery entry it holds (intents and
  open entries included) by settle order, and writes back `since` (v3 §10's credential rules apply to
  `set:trackers`). Never a `lease`. Kept v3 rows (a v3 build's kept work, once it updates) are converted as
  `v4_form` converts a row and merged the same way. **Converted v2 work** — kept `ep` rows, v1 events, a v2 build's
  kept journal, folded through v3 §8 — additionally writes, for each target whose value it changes and that has no
  entry, the open entry `["o", [0, 0, ""], null]` for every account whose `set:deliver` holds `seededThrough`, as
  v3 places such work above `seededThrough`. Its settle order is the lowest there is (no writer settles under the
  empty device id), so any entry a holder or a seed wrote beats it.
- **A minimum never falls back.** A v4 client remembers the highest `x-den-wire-min` it has seen per library id, and
  whether it observed a commit with minimum 4 under the current generation. Before writing anything else, it
  **switches again** when it reads:
  - a minimum below the highest it has seen (a store restored with its index lost), or
  - a log holding any `rec`, `wat`, `snt`, `ep` or `set:tracker-event:*` row, unless it observed a commit with
    minimum 4 under the current generation (den-edge keeps answering 4 for a library restored alone, v3 §10; a device
    holding no generation for this library switches again).

  Otherwise such rows are **strays**: a v4 reader converts each as `v4_form` converts a row, merges it into its
  documents, writes the result on its next write to that title, and keeps reading the stray until a compaction drops
  it. A restored v4 log at minimum 4 needs no re-switch: the generation change triggers write-back.
- **The re-switch** is performed by any v4 client, through the same fenced rewrite with `wireMin` 4, from the log it
  reads through `base`:
  1. if the log holds v2 rows and no `wat` row (a log restored to minimum 2), its v3 form by v3 §10 *A minimum never
     falls back* (den-core `v3_form` in its re-switch mode), treating the delivery documents the client holds as the
     receipts it holds; that form's rows are all at or below the `seededThrough` it writes, so it yields no open
     entry;
  2. `v4_form` of the result (or of the log, when it is v3);
  3. merged with every document and every settled delivery entry the client holds (§6, §9), never a `lease`.

  One rewrite, one commit; never a commit at minimum 3 on the way.
- **Delivery after a generation change** waits as v3 §6 *Taking* says: 10 minutes of observation under the new
  generation, a fresh take, and decisions only on state read to the head.

## 12. Moving a library: key reset, linking, new libraries

- **Key reset** (v2 §1) and **linking a device's own library** decode every document and re-seal it, re-encoded
  from its logical form, under the destination key's name for its identity — which the document carries — merged
  with the destination's version of that name. Delivery documents move the same way (their names carry the account
  id). Settings rows move as v2 says. No row is copied as sealed bytes.
- A device's own library is held in v4 form. Linking it into a library at minimum below 4 writes it as v3 rows (den-core
  `v3_rows`: each title and season document to its `rec` and `wat` rows, each delivery document's entries to v3's
  `snt` rows), since ready builds write v3 there. An own library has no `seededThrough`, so it holds no open entry.
- **New libraries.** A library's first batch carries `x-den-wire-min: 4`; den-edge sets the minimum to the greater of
  that and the minimum of the library named in `x-den-library-member` (v3 §10). A key reset whose `DELETE` of the old
  library gets `generation_changed` re-copies first.

## 13. den-edge

den-edge interprets no row. Beyond v3 §13:

- **Value cap**: 256 KiB of `v` for a library whose wire minimum is 4 or more; 32 KiB below. Rows staged in a rewrite
  may be up to 256 KiB; a commit whose `wireMin` is below 4 is refused with `400 {"error": "value_too_large"}` while
  any staged row exceeds 32 KiB. A batch still holds at most 200 writes and a 2 MiB body; a client splits by bytes.
- **Charge**: a library is charged by its stored bytes, the sum of `k` + `v` over its rows (den-edge 0.241.2), against
  its stored-library cap (32 MiB today); staging is charged the same way, so a staging that is accepted also commits.
- **Rows**: the 50,000-row limit stays. A v4 library holds about (1 + seasons) rows per title plus, per connected
  account, one delivery document per title and per season that has a receipt.
- Accepting `x-den-wire: 4`, and applying v3 §10's refusal, generation and highest-minimum rules with minimum 4,
  need no new mechanism.

## 14. den-core

The rules live in den-sync so both clients share them. Clients do the sealing (v2 §2) and the HMAC check against the
name den-core returns.

- `doc_decode`: plaintext → logical document and its name, or a rejection with its reason (§4: framing, inflate and
  nesting caps, JSON, shapes, device table). JSON plaintexts decode to the v2/v3/settings row they hold.
- `doc_encode`: logical document → plaintext, or `too_large` (§4 caps).
- `doc_name`: identity → name. `doc_merge`, `doc_newest` (§6, §9).
- `title_state`, `episode_state`, `film_state`: documents + resets + clock → v3 §5's derived state.
- `apply_write`: write kind + the documents it touches + clock → the documents to write, or nothing (§8: every write
  kind, imports included).
- `pending_targets`: documents + delivery documents + `set:deliver` + clock → commands (§9, v3 §6); `decide` (v3 §6);
  `settle`: outcome + built-from value + entry → the entry, or nothing; `delivery_write`: delivery document + settles
  and intents → the document; `lease` (v3 §6).
- `v4_switch_ready`: `set:devices` + the log's rejected rows + every account's targets and receipts + clock →
  offered, or not with the reasons it waits (§10).
- `v4_form`: every row through `base`, with seqs, + the performer id → the switch's rows, or a failure with its
  reason (§10). `v4_reswitch`: the log through `base` + held documents and delivery documents → the re-switch's rows
  (§11); keeps `v3_compact` and `v3_form` for it.
- `write_back`: held documents, delivery documents and kept writes + the new log → the writes to send (§11).
- `v3_rows`: documents → v3 rows (§12).

## 15. Vectors the implementation MUST pin

- **Round trip.** For every v3 vector row set, `doc_decode(doc_encode(L)) == L` in JCS for the documents `v4_form`
  makes of it, and `v3_rows` of them gives back the v3 rows' state. The stored form's `devices` is sorted with `""`
  first; indices, inline strings and a mix decode alike; an index out of range, an unsorted table and a duplicate
  are rejected. A stamp inside an unknown member keeps its string through a re-encode that changes `devices`.
- **Framing.** A `0x00` plaintext and a `{` plaintext both read; a JSON `set` row is never compressed.
- **Bounds.** An 8 MiB + 1 inflate rejected; depth 33 rejected; a lone surrogate rejected; an HMAC mismatch rejected;
  a rejected row never overwritten and stopping delivery for every account; `doc_encode` refusing a document over
  256 KiB sealed or 8 MiB of JCS; a `v: 5` document read but never written, its targets held.
- **Identity.** A season document under a title's name, and a delivery document of another account, rejected.
- **Merges.** Merge laws (commutative, associative, idempotent) over random triples of title, season and delivery
  documents, with 30 plays, the same imported play from two sources, `cleared` in one part, settle orders from skewed
  clocks, and unknowns at all three levels with equal newest stamps (the unknown-set JCS tie).
- **Deriving.** v3 §12's deriving cases, run on documents; a series reset in the title document hiding a season
  document's registers.
- **Every write kind** in §8's table, including a film finished by playing writing `resume` and its play in one
  document, an un-watch writing `cleared` before the bump, mark-watched writing nothing on an imported or watched
  episode, and each import rule (v3 §12 *Imports*).
- **Delivery.** v3 §12 *Delivery* run on delivery documents; a no-receipt `watched` stamped before `since` caught up
  additively and one stamped after it pending; a no-receipt `unwatched` before `since` settling silently and after it
  sent; an open entry decided as v3's above-`seededThrough` target (an un-watch stamped before `since` sent; a
  rewatch with every Den play counting), its inner `n` at −1 kept through the imported-first step and dropped by the
  next settle; an entry of unknown shape held; a conflicted settle discarded and decided again; a chained settle on
  the holder's own previous seq applied; a chain broken by another writer's version refused; an unknown outcome
  ending the chain; an intent then settle chained.
- **Switch seeding equivalence.** For a corpus of v3 libraries passing the gate, for every account and target, v4's
  `pending_targets` and `decide` on `v4_form`'s output give the same commands and outcomes as v3's on the v3 log, for
  the same snapshots — including targets below and above `seededThrough`, accounts with and without it, `["b", …]`
  entries, `n` entries at −1, unverified epochs, and accounts connected after the v3 switch.
- **Gate.** A pending viewing with watchedAt at or before `seedBound` blocking the offer; one with a `[p, null, T]`
  element blocking it; both settled, the offer made.
- **v4 form.** Blocks merged into one season document; `seasonReset` taken from block 0 only; a `t<shard>` row's
  unknowns copied to each title's delivery document; a stray `ep` row and a v1 event folded; `set:tracker-event:*`
  dropped; unknown kinds and every other `set` row staged unchanged; failure on rows, bytes and a too-large season.
- **Restores.** Minimum read below the highest seen → re-switch; minimum 4 with `rec`/`wat`/`snt` rows and no
  observed commit → re-switch; the same with an observed commit → strays folded; a v2 log → one rewrite to v4 with no
  open entry from `v3_form`; held delivery entries settled after the backup merged and not re-sent; a restored v4 log
  → write-back only.
- **Write-back.** A held document merged and written only when the merge differs; kept ops re-applied; settled
  entries merged by settle order; never a `lease`; converted v2 work adding an open entry only where the target has
  none and only for accounts with `seededThrough`.
- **Moves.** A key reset re-sealing every document under its new name; a link into a v3 library writing `v3_rows`; a
  new library's first batch at minimum 4.
- **den-edge.** A 256 KiB value accepted at minimum 4 and refused at minimum 3; a commit at `wireMin` 3 holding a
  33 KiB staged row refused; `426` with `min` 4.

## 16. Open questions

1. **Seasons over the cap.** A season that outgrows 256 KiB (§4: ~11,000 typical episodes, ~870 maximal) cannot be
   written. A fixed sub-season coordinate — for example `season:tv:<id>:<season>:<floor(e / 4096)>` — would keep the
   split by coordinate and remove the limit, at the cost of one more name shape. Is any real title close enough to
   warrant it now?
2. **A delivery gate that never clears.** A pending window-covered viewing that stays held (an account mismatch that
   is never resolved, say) holds the switch back indefinitely. Should a person be able to settle it without sending,
   or should v4 keep v3 §9's window acknowledgement as a frozen rule for viewings at or before `seedBound` instead of
   gating on it?
3. **v3 §7's import windows.** Dropping them lets a v2-era Trakt scrobble be written as an imported play (§9 *Removed
   rules*). Trakt is switched off today; should the rule be kept frozen for the same viewings anyway?
4. **Pinned compressor.** Which DEFLATE implementation and level den-core pins.
5. **`set:deliver` as a settings row.** v4 keeps the account's `since`, `lease`, `removals` and `unverified` in v3's
   settings row so the lease's compare-and-set is unchanged. Moving them into an account delivery document would put
   all delivery state in one shape; it would also move the lease's row.
6. **Row count.** One delivery document per season per account roughly doubles a series' rows when an account is
   connected. At den-edge's 50,000 rows a library of very many long series could reach the limit before its 32 MiB;
   should v4 ask den-edge for a higher row limit, or group an account's season receipts into its title delivery
   document (bounded by the same cap)?
7. **v3 §14's open items** are about v2-era handoffs. v4 reaches that code only when re-switching a log restored to
   minimum 2; are they acceptable as known limits there?
