# Library wire format, v4 — proposal (revision 2)

**Status: draft for audit.** Nothing here is implemented. v4 replaces how a v3 library is *stored*, not what it
*means*: the v3 rows a v4 library decodes to are exactly the rows v3 would hold, and every rule of
[library v3](library-v3.md) §3–§8 runs on them. Where a v3 rule reads something v4 storage no longer has (a
per-row seq), §6 replaces the test and says so. Keys, sealing and settings rows stay [library v2](library-v2.md).

Revision 2 answers the audit of revision 1 (oxyc/den-spec#19): exact numbers and stamps (§4), a carrier for
everything v3 keeps (§4), bounded rows for long series (§3), per-target markers instead of row seqs (§6), local
shard splits with no fence (§5), no shard count in a setting (§3), regrouping on key changes (§7), and the v3→v4
switch written out (§8).

Words: *MUST* is a rule a client breaks at the cost of other clients; *should* is advice.

## 1. Why

A real household library after its v3 switch:

| | |
|---|---:|
| Titles | 1,640 |
| Watched episodes and films (registers) | 8,942 |
| Tracker accounts | 1 |
| Rows | 6,878 |
| Sealed bytes (`k` + `v`) | 6.67 MB |
| Of which `set:tracker-event:*` rows v3 §9 says the switch drops (den-core bug, oxyc/den-core#21) | 2.08 MB |
| **v3 like-for-like, without those** | **~4.6 MB, ~5,240 rows** |

Where the like-for-like bytes are: `wat` 2.10 MB (1,886 rows, 844 of them one film each), `snt` 1.34 MB (1,707
rows; 55% of their plaintext is stamps), `rec` 1.13 MB (1,640 rows), settings 0.02 MB. The causes are structural:

1. **A row per small thing.** Each row pays ~100 bytes of name, nonce and tag, then base64's third.
2. **JSON stamps** (`[1585148400000, 6982, "3d93261e815f664b"]`, ~35 bytes) several times per register and receipt,
   repeating a handful of device ids.
3. **JSON field names and written-out defaults** on every register (`"cleared":null,"imported":false` alone is
   284 KB of plaintext here).

## 2. Goals

1. **Exact.** `decode(encode(rows))` is the same set of v3 rows, byte for byte in JCS (RFC 8785): every register,
   play, reset, title field, receipt, unknown field, invalid key and `schema`. Nothing is rounded or dropped.
2. **Same meaning.** v4 adds no rule about state or delivery except §6's replacement for per-row seq tests.
3. **Flexible.** Self-describing encoding with numbered fields, unknowns kept at every level, versions per title.
   Smallest-possible is not a goal.
4. **Bounded rows.** No row exceeds den-edge's value cap, for any library v3 can hold, including a 99,999-episode
   series (§3).
5. **No library-wide events in normal use.** Growth splits one shard at a time, with no fence, no generation change
   and no lease loss (§5).
6. **A size budget, tested** (§10).
7. **No new trust.** den-edge still sees opaque rows and learns no more than in v3 (§9).

## 3. Rows

| Name | Holds | Written by |
|---|---|---|
| `shard:<S>:<n>` | the title entries of every title whose shard at width `S` is `n` (§5), or a split stub | any client |
| `part:<type>:<id>:<season>:<block>` | one block of a long title's episodes (spill, below) | any client |
| `rcpt:<provider>:<account>:<S>:<n>` | that account's receipts for the titles of shard `n` at width `S`, or a split stub | the account's lease holder, and write-back (v3 §10) |
| `rcptpart:<provider>:<account>:<type>:<id>:<season>:<block>` | one block of a long title's receipts | as `rcpt` |
| `set:<name>` | settings, unchanged from v3 (JSON, uncompressed) | any client |
| any other | kept with `k` and `v` unchanged, as v3 keeps rows of kinds it does not know | – |

- `<type>` is `movie` or `tv` and `<id>` the canonical decimal TMDB id, as v2 names titles.
- **No `S` in any setting.** A row's width is in its name and its content (§4); a client learns the library's shards
  from the rows it reads. Write-back never invents a width (§7).
- **Spill.** A title entry whose encoded CBOR (§4) would exceed **4 KiB** keeps its `rec` fields and a spill marker
  in its shard, and its seasons move to `part:` rows, one per v3 block (`floor(episode / 32)`). A `part:` row holds at
  most 32 registers plus that block's carrier (§4), so its bound is v3's: under 23 KiB plaintext worst case (v3 §3
  *Size*). Receipts spill the same way into `rcptpart:` rows (v3 §6 *Size*: under ~17 KiB). A title never un-spills.
- **No `ep` rows and no v1 events.** A v4 client uploads neither on any path. A v4 reader that finds one — or a stray
  v3 `wat`, `snt` or `rec` row — decodes it as the v3 row it is, merges it into the title's v4 rows on its next write
  to that title (v3 §10 *No v2 rows*), and keeps it until then.

## 4. Encoding

The plaintext of every v4 row (shard, part, rcpt, rcptpart) is **deterministic CBOR** (RFC 8949 §4.2.1), compressed
with raw DEFLATE (RFC 1951), then sealed exactly as v2 seals a row's JSON. Settings rows stay JSON: they hold
credentials, and §9 explains why only data rows are compressed.

### Determinism

- Map keys are integers sorted per §4.2.1; a writer omits a key only where the v3 field it stands for is absent.
- Numbers that are integers in v3 JSON are CBOR integers. **`value`, `seconds` and `resume.value` are CBOR floats in
  preferred serialization** (RFC 8949 §4.2.2: the shortest float that represents the value exactly); a writer never
  scales or rounds them. A v3 number that is an integer-valued float in JSON (`1`, `0`) is decoded as JSON writes it.
- **`devices`**, the row's device table, is sorted bytewise; entry 0 is the empty device `""`.
- **Equality, ties and no-op detection always compare decoded v3 rows** (JCS), never CBOR or compressed bytes.
  Sealed bytes differ anyway (random nonce). v3 §4's JCS tie rule therefore resolves exactly as in v3.
- **den-core compresses and decompresses** with one pinned DEFLATE implementation and level. No client uses a
  platform DEFLATE (zlib, Apple Compression, `CompressionStream` and miniz_oxide produce different bytes), and no rule
  depends on compressed bytes. Size thresholds (§3, §5) are measured on **CBOR length**, which is deterministic.

### Device table and stamps

- `devices` entries are byte strings of 8 bytes for a 16-hex `d`, or text strings for any other stored `d`
  (`"local"`, or a `d` v3 readers keep though writers never write it).
- A **stamp** is `[t, c, i]` with `i` an index into `devices`. **Only `[0, 0, ""]` encodes as `0`.** Every other stamp,
  timeless or not, keeps its `t` and `c` (v3 §7 orders imports by `c`).

### Shard row

A map:

| Key | Field |
|---:|---|
| 0 | `S` — the row's width (identity, v2 §2) |
| 1 | `n` — the row's index (identity) |
| 2 | `devices` |
| 3 | `titles` — array of title entries sorted by (`type`, `id`) |
| 4 | `split` — `true` on a stub (§5); a stub holds no titles |
| 5… | unknown keys, kept (below) |

### Title entry

| Key | Field |
|---:|---|
| 0 | `type` — text `"movie"` / `"tv"` |
| 1 | `id` |
| 2 | `schema` — the entry's format version (§4 *Versions*) |
| 3 | `rec` — the v3 `rec` row (below), or absent when v3 holds no `rec` row |
| 4 | `seasons` — season → season entry |
| 5 | `wb` — set of targets marked written back (§6) |
| 6 | `spilled` — `true` when the seasons live in `part:` rows |
| 7… | unknown keys, kept |

**`rec`** is a map from v3 field to its value, one key per field the v3 row holds: 0 `schema`, 1 `status`,
2 `resume`, 3 `reaction`, 4 `deleted`, 5 `dismissed`, 6 `episodesReset`, 7 `addedAt`, 8 `watchedAt`, 9 unknown fields
(a map, text keys). A field **absent in v3 is absent here**; a stamped default (`{"value": null, "at": <real>}`) is
stored as such, so v3 §7's import ownership is unchanged. Enum values (`status`, `reaction`) are small integers for
the values v3 defines and **text for any other**, so a value a later build adds survives.

**Season entry**: a map, block → **block carrier**: 0 `schema` (absent when 3), 1 `seasonReset` (a stamp, kept on
any block though v3 reads it only on block 0), 2 `registers` (episode number → register, valid keys only), 3
`invalid` (text key → register, v3 §3's invalid entry keys), 4 row-level unknown fields (a map, text keys). One block
carrier is exactly one v3 `wat` row; decoding rebuilds `wat:<type>:<id>:<season>:<block>` with that row's fields.
A film's `"0"` register is season 0, block 0, as in v3.

**Register**: a map: 0 `progress` (a map: 0 `value`, 1 `viewing`, 2 `at`, 3 `seconds`, 4 unknowns), 1 `imported`
(present only when `true`), 2 `plays` (key → watched-at, keys as v3 writes them), 3 `cleared` (`[viewing, stamp]`).
v3 §3 says a register has no other fields; v4 adds none.

### Part row

A map: 0 `type`, 1 `id`, 2 `season`, 3 `block`, 4 `devices`, 5 the block carrier. Identity is keys 0–3.

### Receipt rows

`rcpt`: a map: 0 provider, 1 account, 2 `S`, 3 `n`, 4 `devices`, 5 `rows` (array), 6 `split`, 7… unknowns. Each
element of `rows` is one v3 `snt` row in compact form: its `target` (a `wat` row name) or `shard` (v3's own
SHA-256/4096 title shard, `t<shard>`), its entries with stamps and settle orders' device ids as device-table
indices, and its unknowns. Decoding rebuilds v3's `snt:<provider>:<account>:<target>` and
`snt:<provider>:<account>:t<shard>` rows, so v3's receipt names, rules and vectors apply unchanged. `rcptpart`:
provider, account, title, season, block, `devices`, and the `snt` rows for that block.

### Versions and unknowns

- `schema` lives on the **title entry**, not the row, so v2 §6's "MUST NOT write a row whose `schema` is higher"
  freezes one title, not a shard of unrelated ones. A client that finds a title entry with a higher `schema` keeps
  it byte for byte and does not write that title.
- Unknown keys at the row level, title-entry level and register level are kept through merges. Of two versions,
  the unknowns of the one whose newest stamp is later win (v3 §3's rule), per level. Each level's unknowns are at most
  1 KiB of CBOR.

### Bounds

A reader rejects a row that inflates past **256 KiB**, nests deeper than 16 levels, or fails its identity check
(v2 §2: rebuild the name from the payload's identity keys and check the HMAC), and never writes over it.

### Round-trip vector

For every row in every v3 vector, `JCS(decode(encode(row))) == JCS(row)`. §12 of v3 already includes invalid keys
(`"01"`, `"-1"`, `"32"` in block 0, `"100000"`), unknowns and higher schemas; all must round-trip.

## 5. Shards and splits

- **Shard function.** `h` = the first 8 bytes, big-endian, of HMAC-SHA256(`shardKey`, `"movie:" | "tv:"` + canonical
  decimal id), where `shardKey` = HKDF-SHA256(library key, salt `den/library/v4`, info `shard`, 32 bytes). A title's
  shard at width `S` is `h mod S`. `S` is a power of two, at least 16.
- **Extendible hashing.** `shard:<S>:<n>` splits into `shard:<2S>:<n>` and `shard:<2S>:<n+S>` (titles go to
  `h mod 2S`). Different shards have different widths.
- **Where a title lives**: start at `shard:16:<h mod 16>`; while that row is a stub, go to `shard:<2S>:<h mod 2S>`.
  A row whose parent (at `S/2`) is not a stub is **not yet live**: readers ignore it, and its contents merge into
  the children when the parent splits.
- **Split** (any client, when a write would make a shard's CBOR exceed **8 KiB**):
  1. Read the parent at seq `s`.
  2. Write both children with the parent's titles, each merged with what that child row already holds (an
     abandoned split may have left one).
  3. Write the parent as a stub `{0: S, 1: n, 4: true}` with base `s`. This compare-and-set is the commit.
  4. On a conflict at step 3, another write landed on the parent: re-read it, merge it into the children, retry.
  A writer that read the parent before the stub conflicts on its next write to the parent, re-reads, and follows the
  stub. Every v3 merge is a join, so a stale child never overrides newer state.
- **No fence, no generation change, no lease loss.** A split is ordinary writes.
- Receipt rows split the same way, by their own width, independently of shard rows.
- **Stubs are permanent.** A library never merges shards back.

## 6. Merging, writing, and what replaces per-row seq

- **Merge two versions of a v4 row** = decode both into v3 rows, merge each pair by v3's rules, keep rows only one
  side holds, encode. Ties are v3's, on the decoded rows (§4).
- **Write** = read the row, apply the v3 op to the decoded rows, encode, write with the base seq it was read at
  (v2 §5). On a conflict, merge the returned version and write again, **until the write is applied or no longer
  holds** (v3 §10), with jittered backoff. Merges always converge, so no retry limit is needed.
- **More conflicts than v3, by design.** Two devices editing different titles of one shard conflict and merge. A
  bulk write (an import, marking a season) groups its changes by shard, at most 200 writes per batch, and sends far
  fewer requests than v3. A playback progress write rewrites one shard (≤ 8 KiB CBOR) where v3 rewrote one block
  (~5 KiB); at one write per 30 s of playback this is ~0.3 KB/s.
- **A title found in the wrong shard** (a bug, or a write from before a split) is kept, merged into its correct
  shard on the next write to it, and removed from the wrong one in the same batch. **A title found in two rows** is
  the merge of both.
- **`seededThrough` → `wb`.** v3 §6 decides a no-receipt target by whether "its row has seq above `seededThrough`".
  A shard's seq moves with every title in it, so v4 replaces the test: a no-receipt target counts as above
  `seededThrough` **if and only if its title entry's `wb` set holds it**. `wb` merges by union.
  - `v4_form` (§8) sets `wb` on every target with no receipt whose v3 row seq is above `seededThrough`.
  - v3 §10 write-back sets `wb` on every target it writes from kept or converted work.
  - `seededThrough` is read only by a re-switch from a v3 log (§8).
- **Receipt `base`.** v3 §6 bases a receipt write on "the receipt row's seq it read before deciding the command".
  With coarser rows the holder's own settles would conflict with each other, so v4 generalises v3's intent → settle
  case: **a holder may base a receipt write on the seq its own previous write to that row produced, if no read since
  has shown another writer; settles decided against one read of a row go in one write.**
- **§6 *Taking*** watches `set:deliver`'s seq and is unaffected. **v3 §9's handoff test** ("row seq above `head`")
  arises only when re-switching from a v2 log, where `v4_form` evaluates it on the v3 rows before encoding.

## 7. Key changes, linking and write-back

- **Moving a library to another key** (v2 key reset, linking a device's own library, write-back into a library under
  another key) decodes every shard, part and receipt row into titles and receipts, regroups them under the
  destination's `shardKey` and its live shards, and merges each into the destination's rows (§6). A v4 row is never
  copied as a row.
- **Write-back after a switch or restore** (v3 §10) regroups what it holds under the shards the new log has. It
  writes no stub, never a split it did not perform after reading the log, and no `set:format` (none exists).
- **New libraries** (v3 §10): a library's first batch carries `x-den-wire-min: 4`.

## 8. The switch from v3

- **Offer.** v3 §9 *Who and when* with `<d>.format` = 4: every device seen within 180 days reports 4 (TVs count
  whatever their `seen`).
- **Performer.** Any ready device. No drain or handoff applies: v3 keeps delivery state in the log, not in a device.
- **Rows.** `v4_form` = v3's compaction form (v3 §9, last paragraph: every `wat` and `snt` merged, stray `ep` and v1
  events folded through §8), with `wb` set per §6, then grouped and encoded, splitting any shard whose CBOR exceeds
  8 KiB (writing the stubs above it) and spilling any title over 4 KiB. Every `set` row except
  `set:tracker-event:*`, and every row of a kind it does not know, is staged with `k` and `v` unchanged. Each
  `set:deliver` setting is kept, `seededThrough` included (it now only serves a re-switch). `lease` is kept as
  stored; the commit's new generation ends it, as in v3.
- **Commit.** The fenced rewrite of v3 §9 (den-edge items 1–4) with `wireMin` 4, sent with `x-den-wire: 4`.
- **v3 builds after the switch** get `426` and keep their writes. When they update to v4, they write their kept v3
  rows back as v4 ops (§7).
- **Restore to a log at minimum 3.** Any v4 client re-switches from the restored v3 log, merging in every `snt` and
  `rcpt` receipt it holds (v3 §10 *A minimum never falls back*, applied to v4 rows).
- **Restore to a log at minimum 2.** A v4 build switches it straight to v4 through v3 §9 and §10, so v4 builds keep
  `v3_form`.

## 9. den-edge and privacy

- **Charge** = stored bytes (the sum of `k` + `v`) for every v3 and v4 library. This replaces v2's in-memory formula
  in v3 §9's 32 MiB check and in the rewrite allowance (v3 §9 den-edge item 2). den-edge still interprets no row.
- **Compression and secrecy.** Compression before encryption leaks something only when an attacker can place chosen
  text beside a secret in one compressed message. v4 rows hold viewing data only; the one secret a library holds,
  tracker credentials, lives in `set` rows, which are not compressed.
- **What den-edge learns.** Row count (roughly library size / 8 KiB), when splits happen (which tracks growth), and
  per-write size changes of a shard. In v3, row count approximated titles plus blocks, so v4 reveals less. The shard
  function is keyed (§5): den-edge cannot tell which titles share a row.
- **redb compaction and the old v2 log** are den-edge fixes that do not wait for v4: oxyc/den-edge#217.

## 10. Size budget

A prototype over the library in §1 (a hand-packed binary stand-in for CBOR, the same grouping, raw DEFLATE, today's
JSON transport) measured:

| Shards | Rows | Sealed total | Largest shard (compressed) |
|---:|---:|---:|---:|
| 64 | 71 | 228 KB | 6.2 KB |
| 256 | 263 | 285 KB | 4.0 KB |

CBOR with exact floats, kept stamps and carriers will be larger than that stand-in. The budget:

- **The library in §1** MUST be ≤ **500 KB sealed** (v3 like-for-like ~4.6 MB: ≥ 9×).
- **A synthetic heavy viewer** — 50,000 titles, 200,000 episodes, one account — MUST average ≤ **400 bytes sealed
  per title**, with every row under den-edge's value cap.
- **A 10,000-episode series** with 8 plays per episode and a full `sending` on every receipt entry MUST spill into
  `part:` and `rcptpart:` rows that are each under the cap.
- den-core carries all three as tests; the first uses a fixture built from §1's row shapes (no real titles).

## 11. Not in scope

- A binary transport between clients and den-edge (raw `k`, raw `v`): another ~25%, and a den-edge protocol change.
- Any change to v3's rules about state or delivery beyond §6, including v3 §14's open items.
- Shrinking v3's history: 8 plays per register, resets and receipts are all kept.
