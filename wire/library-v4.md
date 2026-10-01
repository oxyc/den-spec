# Library wire format, v4 — proposal (revision 3)

**Status: draft for audit.** Nothing here is implemented.

v4 stores a v3 library **one row per title**: a title's `rec` row and its `wat` rows travel together in one sealed,
compressed row, as do an account's receipts for that title. The rows inside are **v3 rows, verbatim**: v4 adds no
encoding of its own beyond bundling and compression, so every rule of [library v3](library-v3.md) §3–§8 runs on them
unchanged, and nothing v3 holds can be lost in translation. Keys, sealing, settings rows and the log protocol stay
[library v2](library-v2.md).

Revisions 1 and 2 grouped titles into hash shards (~10× smaller) and were audited twice (oxyc/den-spec#19). Every
blocker the audits found came from the shard machinery — shard splits, a custom binary encoding, a device table,
regrouping receipts — and each fix added more. Revision 3 trades that last factor for a design with almost no new
mechanism: per-title bundles of verbatim v3 rows (~3.3× smaller, measured, §9).

Words: *MUST* is a rule a client breaks at the cost of other clients; *should* is advice.

## 1. Why

A real household library after its v3 switch, like-for-like (without the 1,636 `set:tracker-event:*` rows v3 §9 says
the switch drops; den-core bug, oxyc/den-core#21):

| | v3 | v4 (measured, §9) |
|---|---:|---:|
| Titles / watched episodes and films | 1,640 / 8,942 | same |
| Rows | 5,243 | 3,039 |
| Sealed bytes (`k` + `v`) | 4.75 MB | 1.45 MB |
| Largest row (compressed) | – | 2.9 KB (a 329-episode series) |

v3's size comes from a row per small thing (each pays ~130 bytes of name, nonce, tag and base64) and from JSON that
repeats field names, stamps and device ids in every row. Bundling a title's rows removes a row's overhead per block,
and compressing a bundle removes most of the repetition, because a title's registers repeat the same field names and
device ids.

## 2. Goals

1. **Exact.** A bundle holds v3 rows verbatim. `decode` returns the same rows, byte for byte in JCS (RFC 8785).
2. **Same meaning.** No v3 rule changes. Where a v3 rule reads a v3 row's seq, §6 gives each bundled row the seq
   v3 would have given it.
3. **Bounded.** No row exceeds den-edge's value cap, for any library v3 can hold (§5).
4. **Monotonic reads.** A reader that has read the log to any seq never holds less than it held at an earlier seq
   (§5, §7).
5. **No library-wide events in normal use.** Nothing but the switch fences the library.
6. **A size budget, tested** (§9).
7. **No new trust.** den-edge sees opaque rows and learns no more than in v3 (§8).

## 3. Rows

| Name | Holds | Written by |
|---|---|---|
| `title:<type>:<id>` | the title's bundle: its v3 `rec` row and its v3 `wat` rows, except spilled blocks (§5) | any client |
| `rcpt:<provider>:<account>:<type>:<id>` | that account's receipt bundle for the title: the v3 `snt` rows whose `target` is one of the title's `wat` rows, except spilled ones | the account's lease holder, and write-back (v3 §10) |
| `wat:<type>:<id>:<season>:<block>` | a **spilled** block: exactly the v3 `wat` row of that name | any client |
| `snt:<provider>:<account>:<target>` | a spilled receipt row: exactly that v3 `snt` row | as `rcpt` |
| `snt:<provider>:<account>:t<shard>` | v3's title-field receipt rows (lists, ratings, film watches by v3 §6's own SHA-256/4096 shard), unchanged | as `rcpt` |
| `set:<name>` | settings, unchanged (JSON, uncompressed) | any client |
| any other | kept with `k` and `v` unchanged, as v3 keeps rows of kinds it does not know | – |

- `<type>` is `movie` or `tv`, `<id>` the canonical decimal TMDB id, as v2 names titles. Names are hashed into `k` as
  in v2 §2, so den-edge learns nothing about which title a row is.
- A film's watch row (`wat:movie:<id>:0:0`) is part of its bundle, so a film is one row plus its receipts.
- **No `ep` rows and no v1 events.** A v4 client uploads neither on any path. A v4 reader that finds one decodes it
  through v3 §8, merges the result into the title's bundle on its next write to that title (v3 §10 *No v2 rows*), and
  keeps the stray row until then.
- **Ready builds write v3 until the switch.** A v4-capable build on a v3 library reads and writes v3 only and reports
  `format` 4 (§7), exactly as v3 §1 does for v2.

## 4. Bundle encoding

The plaintext of a `title:` or `rcpt:` row is **raw DEFLATE (RFC 1951) of the JCS (RFC 8785) of a bundle object**:

```json
{"v": 4, "rows": [<v3 row>, …], "seq": {"<v3 row name>": <int>, …}, "spilled": ["<v3 row name>", …]}
```

- `rows` holds v3 rows verbatim, sorted by v3 row name; each row's name is rebuilt from its content as v3 does.
- `seq` gives every bundled row its **virtual seq** (§6). `spilled` lists blocks (or receipt rows) that live in their
  own rows (§5), and never shrinks.
- Unknown top-level keys are kept through merges; where two versions disagree on one, the JCS-greater value wins.
  `v` merges by max; a client that finds `v` higher than it knows keeps the row byte for byte and does not write
  that title.
- A spilled `wat:`/`snt:` row is sealed as raw DEFLATE of its v3 row's JCS.
- **Compression is den-core's,** with one pinned DEFLATE implementation and level; no client compresses itself.
  Compressed bytes differ between implementations, and **no rule depends on them**: equality, ties and no-op detection
  compare decoded v3 rows (JCS), as v3 §4 does. Sealed bytes differ anyway (random nonce).
- **Bounds.** A reader rejects a row that inflates past 256 KiB or whose rebuilt names do not match its `k` (v2 §2:
  the bundle's own name is rebuilt from its rows' `title`; each inner row's name from its content), and never writes
  over it.
- **Identity.** Every row in a `title:` bundle has that `title`; every row in an `rcpt:` bundle has that provider,
  account and a `target` of that title. A row that doesn't is kept and moved to its right place on the next write.

## 5. Spill: bounded rows, monotonic reads

v3 bounds a row by its 32-episode block. A bundle grows with a title, so a long series spills blocks into rows of
their own, which are exactly v3's rows and so carry v3's bound.

- **When.** A write whose resulting bundle compresses to more than **16 KiB** spills blocks, highest block number
  first, until the bundle compresses to at most **8 KiB**. The rec row never spills. Thresholds are measured on den-core's
  pinned compressor; two clients that disagree about a threshold only spill earlier or later, which is harmless.
- **Order (the rule that keeps reads monotonic).**
  1. Write each block to its own `wat:` row, merged with any version of that row already there.
  2. Then write the bundle without those blocks and with their names added to `spilled`, based on the bundle's seq
     read before step 1.
  Because step 1's rows get lower seqs than step 2's bundle, any reader that has read the bundle without a block has
  already read that block's row. Content is never absent from the log at any seq.
- **Reading.** A title's state is the merge (v3 §3) of its bundle's rows and every spilled row of that title. A block
  present in both (two concurrent versions merged) is merged, never chosen between.
- **Concurrent writers.** A writer that read the bundle before a spill conflicts on step 2's compare-and-set, re-reads,
  sees `spilled`, and writes its change to the block's own row. A bundle version that both lists a block in `spilled`
  and holds it (a merge of concurrent versions) decodes as the merge of both, and the next writer moves it out by the
  same two steps.
- **Never un-spilled.** A spilled block stays in its own row.
- Receipt bundles spill the same way: an `snt` row moves out to its own `snt:` row, then the bundle drops it.

## 6. Merging, writing, and virtual seqs

- **Merge two bundle versions** = merge each pair of same-named v3 rows by v3's rules, keep rows only one side holds,
  union `spilled`, take the max of each `seq` entry, then re-encode. Ties are v3's, on the decoded rows.
- **Write** = read the bundle, apply the v3 op to its decoded rows, encode, write with the base seq it was read at
  (v2 §5). On a conflict, merge the returned version and write again until the write is applied or no longer holds
  (v3 §10), with jittered backoff.
- **Virtual seq.** v3 §6 compares "the row seq" of a target's row with each account's `seededThrough` (Seeded
  accounts, Rewatch, Earlier viewings) and, in a compaction, gives a folded row "the greater of its parts' seqs". In
  v4 a bundled v3 row has no den-edge seq of its own, so it carries one in `seq`:
  - `v4_form` (§7) sets each row's virtual seq to the seq v3's compaction would give it if staged alone, and writes
    each account's `seededThrough` by v3 §6's compaction rule over those virtual seqs. No target changes side.
  - A v4 write that changes a bundled row (its JCS differs after the op) sets that row's virtual seq to
    `max(current, H + 1)`, where `H` is the log head the writer had read. A v4 writer has read the switch commit, so
    `H + 1` exceeds every `seededThrough` the switch wrote, exactly as any v3 write after the switch lands above it.
  - A row the write did not change keeps its virtual seq. So a rating change does not move the title's film watch
    across `seededThrough`, as in v3, where they are different rows.
  - A spilled row has a real den-edge seq and uses it, as in v3.
  Every v3 rule that reads a row's seq reads the virtual seq for a bundled row. Nothing else reads it.
- **Receipt `base`.** v3 §6 bases a receipt write on "the receipt row's seq it read". In v4 that is the receipt
  bundle's den-edge seq. A holder may base a receipt write on the seq its own previous write to that bundle produced,
  if no read since has shown another writer; settles decided against one read of a bundle go in one write. (v3 already
  chains intent → settle this way.)
- **Taking** (v3 §6) watches `set:deliver` and is unaffected.
- **Decisions on partial state.** A delivery pass decides only on state read to the log's head, as in v3.

## 7. Key changes, linking, the switch, restores

- **Moving a library to another key** (v2 key reset, linking a device's own library, write-back into a library under
  another key) decodes every row into v3 rows and writes each title's bundle, receipts and spilled rows under the
  destination's names, merged with what the destination holds. Bundles are rebuilt, never copied as sealed rows.
  Virtual seqs are recomputed as for any v4 write.
- **The switch from v3.**
  - **Offer:** v3 §9 *Who and when* with `<d>.format` = 4 (devices seen within 180 days; TVs whatever their `seen`).
  - **Performer:** any ready device. No drain or handoff applies: v3 keeps delivery state in the log.
  - **Rows:** `v4_form` = v3's compaction form (v3 §9, last paragraph: `wat` and `snt` rows merged; stray `ep` and v1
    events folded through §8 and dropped), bundled per title and account with virtual seqs (§6), spilling any bundle
    over 16 KiB (§5). Every `set` row except `set:tracker-event:*`, every `snt:…:t<shard>` row, and every row of a kind
    it does not know, is staged with `k` and `v` unchanged except that `snt` rows are compressed. `seededThrough` is
    rewritten per §6. `lease` is kept as stored; the commit's new generation ends it, as in v3.
  - **Commit:** v3 §9's fenced rewrite with `wireMin` 4, sent with `x-den-wire: 4`.
  - **v3 builds after the switch** get `426` and keep their writes; once updated, they write them back as v4 ops.
- **Write-back after a switch or restore** (v3 §10) is v3's, in v4 form: decoded, regrouped into bundles, merged.
- **Restore to a log at minimum 3:** a v4 client re-switches from it, merging in the receipts it holds. **At minimum
  2:** a v4 build switches it to v3 and then v4 (it keeps `v3_form`).
- **New libraries:** the first batch carries `x-den-wire-min: 4`.

## 8. den-edge and privacy

- **No den-edge change** beyond accepting `x-den-wire: 4`. Charging libraries by stored bytes (k + v) and compacting
  the v3 store are already in oxyc/den-edge#217 and apply to v4 unchanged.
- **Compression and secrecy.** Compression before encryption leaks something only when an attacker can place chosen
  text beside a secret in one compressed message. Bundles hold viewing data only; tracker credentials live in `set`
  rows, which stay uncompressed.
- **What den-edge learns.** Row count (≈ titles + spilled blocks + receipt rows), as in v3 it learned titles + blocks,
  and per-write size changes of a row. No more than v3.

## 9. Size budget

Measured over the library in §1 with the encoding above (JCS, DEFLATE-raw, today's JSON transport):

| | v3 | v4 |
|---|---:|---:|
| Rows | 5,243 | 3,039 |
| Sealed bytes | 4.75 MB | 1.45 MB |
| Largest row (compressed) | – | 2.9 KB |

The budget den-core MUST test:

- **The library in §1** (as a fixture with its row shapes, no real titles): ≤ **1.7 MB sealed**.
- **A synthetic heavy viewer** — 50,000 titles, 200,000 episodes, one account: ≤ **1 KiB sealed per title** on
  average, every row under den-edge's value cap.
- **A 10,000-episode series** with 8 plays per episode and a full `sending` on every receipt entry: spills into rows
  that are each under the cap (each is a v3 row, so v3 §3 and §6 *Size* bound it).

## 10. Not in scope

- Grouping several titles per row (revisions 1–2): ~3× more saving, at the cost of shard splits and their failure
  modes. Revisit only with a measured need.
- A binary transport between clients and den-edge (raw `k`, raw `v`): another ~25%, a den-edge protocol change.
- Any change to v3's rules about state or delivery, including v3 §14's open items.
