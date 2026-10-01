# Library wire format, v4 — proposal

**Status: draft for audit.** Nothing here is implemented. It replaces how a v3 library is *stored*, not what it
*means*: every rule of [library v3](library-v3.md) §3–§8 (registers, merges, derivation, delivery, writing) applies
unchanged to the decoded form. Keys, sealing, settings rows and the log protocol stay [library v2](library-v2.md).

Words: *MUST* is a rule a client breaks at the cost of other clients; *should* is advice.

## 1. Why

v3 fixed what devices agree on. It did not make the library small, and no size budget was set or measured before
it shipped. A real household library after its switch to v3:

| | |
|---|---:|
| Titles | 1,640 |
| Watched episodes and films (registers) | 8,942 |
| Tracker accounts | 1 |
| Rows | 6,878 |
| Sealed bytes (`k` + `v`) | 6.67 MB |
| den-edge charge (`2(k+v) + fragment + 192`) | 21.5 MB of the 32 MiB cap |

Where the bytes are:

| Row kind | Rows | Sealed | Note |
|---|---:|---:|---|
| `wat` | 1,886 | 2.10 MB | 844 of them hold one film each |
| `set:tracker-event:*` | 1,636 | 2.08 MB | v3 §9 says the switch drops these; den-core's `v3_form` keeps every `set` row, so they survived |
| `snt` | 1,707 | 1.34 MB | 55% of their plaintext is stamps |
| `rec` | 1,640 | 1.13 MB | |
| other `set` | 9 | 0.02 MB | |

The causes are structural, not the data:

1. **The same fact three times.** A watch is a `wat` register, again a receipt in `snt`, and (here) again a v1 event.
2. **Stamps.** `[1585148400000, 6982, "3d93261e815f664b"]` is ~35 bytes of JSON and appears several times per
   register and receipt, repeating the same few device ids.
3. **JSON field names** (`"progress"`, `"viewing"`, `"imported"`, `"cleared"`, `"plays"`) on every register, and
   default values (`"cleared":null`, `"imported":false`) written out: 284 KB of plaintext here.
4. **A row per small thing.** Each row pays ~100 bytes of HMAC name, nonce and tag, then base64's third, and den-edge
   charges each again at 2× plus 192 bytes. 6,878 rows is ~0.7 MB of envelope alone.

## 2. Goals

1. **Everything is kept.** Every register, play (with its watched-at), reset, title field, receipt and setting v3
   holds, bit for bit after decoding. No history or precision is dropped to save space.
2. **Same semantics.** v4 adds no rule about state or delivery. den-core decodes a v4 row into the v3 rows it
   stands for, runs the v3 op, and encodes the result. Every v3 vector holds on the decoded form.
3. **Flexible.** A self-describing encoding with numbered fields: a later field is added without a new format, and
   a reader keeps fields it does not know (as v3 keeps unknowns). Smallest-possible is not a goal.
4. **A size budget, tested.** §8. A change that breaks it fails den-core's tests.
5. **No new trust.** den-edge still sees only opaque rows, and learns no more than it does today.

## 3. Rows

A v4 library holds three kinds of row. Names are hashed into `k` as in v2 §2.

| Name | Holds | Written by |
|---|---|---|
| `shard:<n>` | every title whose shard is `n`: its v3 `rec` row and all its `wat` rows | any client |
| `rcpt:<provider>:<account>:<n>` | that account's receipts (`snt` entries) for the titles of shard `n` | the account's lease holder only (v3 §6) |
| `set:<name>` | settings, unchanged from v3 | any client |

- **Shard of a title** = the first 4 bytes of HMAC-SHA256(`macKey`, `"shard/" + <type> + ":" + <id>`), big-endian,
  modulo `S`. Keyed, so den-edge cannot map a title to its shard.
- **`S`, the shard count**, is a power of two between 16 and 4096, chosen at the switch (§6) and recorded in
  `set:format` as `{"shards": {"int": S}}`. It changes only by a fenced rewrite (§6, *Reshard*), never by a plain
  write, so every client always addresses the same rows.
- Receipts stay in their own rows because only the lease holder writes them (v3 §6). Keeping them apart from
  `shard:n` means a delivery settle never conflicts with a person's edit, and a device without credentials never
  rewrites receipts.
- **No v1 events and no `ep` rows** exist in a v4 library. The switch folds any it finds (v3 §8) and drops them; a v4
  client uploads neither on any path.

## 4. Encoding

The plaintext of `shard:n` and `rcpt:…:n` is **deterministic CBOR** (RFC 8949 §4.2.1), compressed with raw DEFLATE
(RFC 1951), then sealed exactly as v2 seals a row's JSON. Settings rows stay JSON.

Deterministic CBOR because it is compact (integers are 1–9 bytes, byte strings are raw), self-describing (a reader
can skip a field it does not know), and canonical (one value has one encoding, so two clients that hold the same
state produce the same bytes and §5's ties are well defined).

**Compression before sealing** leaks only the compressed length, which an observer cannot steer: a row holds only
the library's own data, never attacker-chosen text beside a secret. Each row is compressed alone, so den-edge learns
nothing it does not already learn from today's row sizes.

### Shard row

A map with integer keys; a key absent means its default.

| Key | Field | Value |
|---:|---|---|
| 0 | `v` | format version, `4` |
| 1 | `devices` | array of 8-byte device ids (the 16-hex `d` of every stamp in the row, as bytes); index 0 is always `""` |
| 2 | `titles` | array of title entries, sorted by (type, id) |
| 3… | | reserved; a reader keeps unknown keys through merges (≤ 1 KiB, as v3 §3) |

A **stamp** is `[t, c, i]`: v3's `t` and `c` as CBOR integers, `i` an index into `devices`. The timeless stamp is
`0`. `"local"` is a reserved device entry (`h'00'`). Stamps are compared as v3 §4 compares the stamps they stand
for.

A **title entry** (map):

| Key | Field | From v3 |
|---:|---|---|
| 0 | type | 0 film, 1 series |
| 1 | id | TMDB id |
| 2 | rec | the `rec` row's fields as a map (status, resume, reaction, deleted, dismissed, episodesReset, addedAt, watchedAt, unknowns), each stamped value `[value, stamp]`; enums as small integers |
| 3 | seasons | map season → `{0: seasonReset, 1: episodes}`; `episodes` maps episode number → register |
| 4 | film | a film's `"0"` register |

A **register** (map): 0 `progress` `[value×10⁶ as integer, viewing, seconds?, stamp]`, 1 `imported` (true only),
2 `plays` (map key → watched-at, as v3), 3 `cleared` `[viewing, stamp]`. Absent = v3's default (no progress,
`false`, `{}`, `null`).

v3's 32-episode blocks disappear from the wire: a title's episodes are one map. The decoded form rebuilds v3's
`wat:<type>:<id>:<season>:<block>` rows, blocks and all, so every v3 op and vector applies.

### Receipt row

A map: 0 `v`, 1 `devices`, 2 `targets`: map from target (title entry coordinates plus `season`/`episode`, or a
title field) → v3's receipt entry tuple with its stamps and settle orders encoded as above. Every v3 receipt rule,
including `sending`, `unverified` and seeding, applies to the decoded `snt` rows.

## 5. Merging and writing

- **Merge two versions of a shard** = decode both into v3 rows, merge each v3 row pair by v3's rules (§3, §4 ties on
  the decoded rows), encode the result. Rows only one side holds are kept. The same for receipt rows.
- **Write** = read the shard row (or start from an empty one), apply the v3 op to the decoded rows, encode, and
  write `shard:n` with the base seq it was read at (v2 §5). On a conflict, merge the returned version and write again,
  as v2 does today (three rounds).
- **More conflicts than v3, by design.** Two devices editing different titles of one shard now conflict and merge. At
  household rates this is rare; a bulk write (an import, marking a season) touches far fewer rows than v3 and sends
  far fewer requests.
- **den-core owns encoding.** Clients call `shard_apply(shard, op)`, `shard_merge(a, b)`, `shard_decode(shard)` and
  the receipt equivalents; no client encodes CBOR itself (v3 §11).

## 6. The switch, and resharding

- **v3 → v4** uses v3 §9's machinery unchanged: ready builds report `format` 4; `switch_ready` gates on it; the
  device holding delivery state performs one fenced rewrite whose rows are `v4_form(rows through base)` and commits
  with `wireMin` 4. `v4_form` folds any `ep` or v1 event through v3 §8 first, then groups by shard and account.
- **`S` at the switch** = the smallest power of two ≥ 16 for which no encoded shard row exceeds 8 KiB compressed.
- **Reshard.** When a write would make a shard row exceed 16 KiB compressed, the writing client performs a fenced
  rewrite with `S` doubled (the same rewrite, `wireMin` unchanged) and then makes its write. Every client sees the new
  `set:format` in the same commit. A library never shrinks `S`.
- **Write-back after a switch or restore** follows v3 §10 in v4 form.

## 7. den-edge

- Unchanged, except that a library's **charge is its stored bytes** (the sum of `k` + `v` it holds) rather than v2's
  in-memory formula, and the cap is reviewed against §8. den-edge still interprets no row.
- The **v3 store compacts** (redb's compaction) when more than half its file is free pages; measured here, 25.3 MB →
  10.7 MB. This is a den-edge fix that does not wait for v4.

## 8. Size budget

Measured by prototyping the encoding over the library in §1 (a hand-packed binary stand-in for CBOR, the same
shard grouping, raw DEFLATE, today's JSON transport):

| Shards | Rows | Sealed total | Largest shard (compressed) |
|---:|---:|---:|---:|
| 64 | 71 | 228 KB | 6.2 KB |
| 256 | 263 | 285 KB | 4.0 KB |

CBOR with numbered fields is somewhat larger than the hand-packed stand-in. The budget leaves room for it:

- **The library in §1** MUST be ≤ **500 KB sealed** in v4 (v3: 6.67 MB; ≥ 13× smaller).
- **Per title** (including its registers and one account's receipts), a synthetic library MUST average ≤ **400 bytes
  sealed** for a viewer of 50,000 titles and 200,000 episodes, with every shard row under den-edge's value cap after
  resharding.
- den-core carries both as tests: the first against a fixture built from §1's row shapes (no real titles), the second
  generated.

## 9. Not in scope

- A binary transport between clients and den-edge (raw `k`, raw `v`): another ~25%, and a den-edge protocol change.
  Possible later; v4 does not need it.
- Changing any v3 rule about state or delivery, including v3 §14's open items.
- Dropping v3's per-row history guarantees: nothing about plays (8 kept per register), resets or receipts changes.

## 10. Open questions for the audit

1. Is CBOR the right base, or should den-core reuse an encoding the den-dataset store already has?
2. Shard rows trade per-title isolation for size. Is three-round conflict retry enough for a bulk import racing a
   playing TV, or should a write that conflicts repeatedly back off by shard?
3. Should receipts share the shard row after all (one row per shard), giving up the "only the lease holder writes"
   separation, for another ~20% fewer rows?
4. Is 8 KiB / 16 KiB the right reshard threshold given sync re-downloads a whole shard per changed title?
5. Does any v3 rule depend on per-row seq numbers (`seededThrough`, receipt `base`) in a way shard rows break?
   v3 §6 counts staged rows and compares a target's row seq to `seededThrough`; with shards, a target's seq is its
   shard's.
