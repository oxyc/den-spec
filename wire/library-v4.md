# Library wire format, v4 — proposal (revision 8)

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
| Plaintext | UTF-8 JSON | `0x00` + raw DEFLATE of RFC 8785 JCS (§4) |
| Receipts | `snt` rows per watch row, and 4,096 hashed title shards | one **delivery document** per account and title, and per account and season (§9) |
| Rules on den-edge seqs | `seededThrough`, the `seedBound` and `connectedFrom` windows, handoff `head` | none: a seq is only a compare-and-set base, and the lease row's is observed for liveness (§9) |
| Per-change history | none (v3 folds `ep` rows and v1 events) | none; v4 writes neither (§8) |
| den-edge value cap | 32 KiB | 256 KiB for a library at minimum 4 (§13) |
| Switch | from v2: drain, handoffs, facade, waiting commands | from v3: an automatic, checked conversion by the first v4 build to open the library (§10) |

Everything else — keys, names hashed with `macKey`, AES-GCM sealing, settings rows, credentials in `set:trackers`,
`set:deliver` (less three settings v4 reads only at the switch), the lease, the generation and wire-minimum headers —
is unchanged.

## 1. How a library moves to v4

A household has one or two libraries, and every device is updated by its owner. The first v4 build to open a v3
library converts it, with no prompt, and checks the conversion before committing it (§10). From the commit on, only
v4 builds can read or write it. den-edge refuses an older build's reads and writes with `426`; a shipped v3 build
logs that, shows nothing, and keeps showing the state it last read, so its owner has to update it (§10 *Other
devices*). Nothing an old build writes is ever converted.

## 2. Guarantees

v3 §2's guarantees hold, restated where documents change them:

1. **Convergence.** Clients that have read the same document versions derive the same state, in any arrival order
   (clock caveat as v3 §2.1).
2. **No lost update.** Changes to different fields, episodes or seasons never overwrite each other; changes to the
   same one resolve by its merge rule (§6). Two writers of one document meet at its compare-and-set and merge.
3. **Monotonic reads.** Content never moves between rows: a coordinate lives in exactly one document name for the
   life of the library. A reader that has read the log to any point holds, for every document, a version that is a
   merge-ancestor of the current one, so a partial read only ever holds older state, never less state.
4. **No lost state at the switch.** At the rewrite's `base`, the v4 form derives the same state for every title and
   coordinate as shipped v3 derives from the log; the dry run (§10) verifies it before the commit. Pending commands
   are compared and their differences reported; after the commit, v4's own rules decide every target (known limits:
   §9 *Removed rules*, §10).
5. **Imports never override a person** (v3 §2.4).
6. **Bounded growth.** Rows grow with titles and seasons — per account, one delivery document per title and per
   season with a receipt — never with changes. A document's size grows with its season's episodes, and is capped
   (§4).
7. **Idempotence.** Replaying any write, any number of times, in any order, changes nothing further.
8. **Nothing delivered twice, nothing dropped.** One device at a time delivers for an account; a settled change is
   not sent again; an unsettled or held one stays pending (known limits: §9, §10).

## 3. Documents

A document is a JSON object whose **identity fields** name it. Every document carries `"format": 4`, its `kind`, and
its identity:

| Kind | Name | Identity fields | Holds |
|---|---|---|---|
| `title` | `title:<type>:<id>` | `title` | a film: every v2 `rec` field and its watch register; a series: every v2 `rec` field |
| `season` | `season:tv:<id>:<season>` | `title`, `season` | a series season: its `seasonReset` and every episode register |
| `delivery` | `dlv:<provider>:<account>:<type>:<id>` | `provider`, `account`, `title` | an account's receipts for a title's own targets (§9) |
| `delivery` | `dlv:<provider>:<account>:tv:<id>:<season>` | `provider`, `account`, `title`, `season` | an account's receipts for one season's episodes (§9) |

- `title` is `{"type": "movie" | "tv", "id": <TMDB id>}` as v2 §3; `<type>` and `<id>` (canonical decimal) spell it
  in the name. `season` is a non-negative integer, spelled in canonical decimal. Season 0 (specials) is a season like
  any other. `provider` and `account` are v3 §6's provider name and account id; an account id matches
  `[0-9A-Za-z_-]+`, so it never holds a `:`. Connecting an account whose id does not match is refused.
- **Names** are hashed into the row key as v2 §2: `k` = hex HMAC-SHA256(`macKey`, name). A reader rebuilds the name
  from the document's identity fields; a row whose HMAC is not `k` is unreadable (§4 *Unreadable rows*).
- **The split is fixed by coordinate.** A film is always one document; a series always one title document plus one
  season document per season it has state in. No rule moves content between documents, and no rule depends on a
  document's size other than the caps of §4.
- A document is created by its first write (`base` 0). Rows are removed only as §4 *Unreadable rows* says.
- **Settings rows** (`set:<name>`) are v2's and v3's, unchanged and uncompressed (§4).
- **Rows of other kinds** — a kind this version does not know — are kept with `k` and `v` unchanged.
- **v2 and v3 rows** (`rec`, `wat`, `snt`, `ep`, `set:tracker-event:*`): a v4 client that reads one runs the switch
  (§10).

### Title document — `title:<type>:<id>`

Its known fields are v2 §3's `rec` fields, with v2's meanings and value sets: `status`, `resume`, `reaction`,
`deleted`, `dismissed`, `episodesReset`, `addedAt`, `watchedAt`. A **film** document also has `watch`: the film's
watch register, `{"plays", "cleared"}` with v3 §3's meanings (v3's `"0"` register of `wat:movie:<id>:0:0`). A
`watch` on a `tv` document, and `episodesReset` on a `movie` document, are kept and merged by their own §6 rules (not
as part of the unknown set), and read by no rule.

An **absent** field keeps v3's meaning: an absent stamped field counts as `[0, 0, ""]` (import-owned, v3 §7), which
is not the same as a present field with a stamped default. A writer never adds a field it did not write.

```json
{
  "format": 4, "kind": "title",
  "title": {"type": "movie", "id": 550},
  "status": {"value": "watched", "at": [1789000000000, 0, "a1b2c3d4e5f60718"]},
  "resume": {"value": 1, "at": [1789000000000, 0, "a1b2c3d4e5f60718"], "viewing": 1, "seconds": 8340},
  "reaction": {"value": "love", "at": [1789000100000, 0, "0f1e2d3c4b5a6978"]},
  "deleted": {"value": false, "at": [1788000000000, 0, "a1b2c3d4e5f60718"]},
  "dismissed": {"value": false, "at": [1788000000000, 0, "a1b2c3d4e5f60718"]},
  "addedAt": 1788000000000,
  "watchedAt": 1760000000000,
  "watch": {
    "plays": {"-9005499254740992": 1700000000000, "0": 1760000000000, "1": 1789000000000},
    "cleared": null
  }
}
```

A series title document has the same fields without `watch`, and `episodesReset` a stamp or null.

### Season document — `season:tv:<id>:<season>`

```json
{
  "format": 4, "kind": "season",
  "title": {"type": "tv", "id": 1399}, "season": 1,
  "seasonReset": null,
  "episodes": {
    "1": {
      "progress": {"value": 1, "at": [1788900000000, 0, "0f1e2d3c4b5a6978"], "viewing": 0, "seconds": 3120},
      "imported": false,
      "plays": {"0": 1788900000000},
      "cleared": null
    }
  }
}
```

- `seasonReset`: a stamp or null (v3 §3), one per season.
- `episodes`: key → **register** with v3 §3's fields and rules: `progress`, `imported`, `plays` (with the 8-kept
  selection, which a reader applies too: `doc_decode` keeps the least key and the 7 greatest of a `plays` holding
  more, so the merge laws hold over every document it returns), `cleared`. A **valid key** is the canonical decimal form of an integer `0 ≤ e ≤ 99999` (v3's range,
  without the block condition). A reader derives nothing from any other key and keeps it (§6 *Unknowns*).
- Numbers and stamp bounds are v3 §3's, unchanged.

## 4. Encoding

### Plaintext

A row's sealed plaintext (v2 §2) is one of:

- **Compressed:** the byte `0x00`, then raw DEFLATE (RFC 1951) of the UTF-8 JCS (RFC 8785) of the document. Every
  document is written this way. The DEFLATE stream ends at its final block; any byte after it makes the row
  unreadable.
- **JSON:** UTF-8 JSON whose first byte is `{` (`0x7B`), with no leading whitespace. Settings rows are written this
  way, and rows of unknown kinds are kept this way. A document of a known kind in this form is read like a
  compressed one; its next write compresses it.

A reader tells them apart by the first byte. A plaintext whose first byte is neither is of a **newer framing**
(below).

**Why settings are not compressed.** Compressing secret bytes beside bytes another party chooses leaks the secret
through the length, so the rows holding credentials stay uncompressed. Documents are not free of sensitive content:
the title id and the viewing history are exactly what sealing hides from den-edge. **Known limit**: a document's
compressed length reveals a little more about its content than v3's plaintext length did (row lengths already reveal
episode counts), and the only channel that puts chosen bytes into a document is a tracker's import data, which that
tracker already holds.

There is no stored-versus-logical split: the document as written is the document every rule reads. Device ids are
written inline; DEFLATE turns a repeated id into a back-reference (a device table saved at most ~1 % in
measurement). Equality of documents is equality of their JCS bytes.

### Compression

den-core compresses with **`miniz_oxide` at level 9**, pinned in den-core's `Cargo.lock`, and inflates with
`decompress_to_vec_with_limit` at **8 MiB**: inflation is streamed against that limit, never buffered whole first.
No client uses a platform compressor. Compressed bytes may differ between den-core versions, and **no rule depends
on them**; every comparison is on the JCS (sealed bytes differ on every write anyway: v2's nonce is random). The
growth headroom under *Size* is what makes a den-core bump harmless.

### Bounds

**Unreadable** covers only a row that cannot be attributed to a name: it fails to open (AES-GCM), its compressed
plaintext inflates past 8 MiB or has trailing bytes, it is not valid UTF-8 JSON, it nests deeper than 32 levels, it
holds a string JCS cannot serialize (a lone surrogate), it is a document of a known `kind` whose `format` is not an
integer, is below 4 or is missing, or whose identity fields are missing or wrongly typed, or its rebuilt name does not
HMAC to its `k`. (A document of an unknown `kind` is kept, §3.)

**Malformed parts.** A document whose name verifies but which has a malformed known part — a top-level field, a
register member (`progress`, `imported`, `plays`, `cleared`) or a delivery entry — is **read**: `doc_decode` drops
the malformed part, logs the document name and the reason, and returns the rest. It is not unreadable, it is not
removed, and it does not pause delivery. A dropped delivery entry means that target has no receipt; §9 *Pending*
decides it against the snapshot.

**Shape** of a known part: its required members, with their types. Any other member inside a known object is kept,
travels with the version of that field that wins its merge, and is never read. Only a missing or wrongly typed
required member makes a part malformed.

**Writers check themselves.** Before sending any document, a writer decodes its own encoded value and does not send
one that fails to decode or that decodes to a different document; it logs the document name and drops the write.

### Unreadable rows

An unreadable row is ignored for state, and while it remains no device delivers for any account (it may be a
delivery document). A v4 client that reads one logs its `k` and the reason.

**Only a row that fails to open is ever removed.** A row whose AES-GCM open fails is corruption: no writer sealed
those bytes under this key. Once the log is read to its head, a client removes such a row by a **compaction**: a
fenced rewrite (v3 §9) at the unchanged minimum that stages every other row read through the rewrite's `base` as
stored, and skips the removal if that `k` opens at `base`. A write to that `k` waits for the removal. A removed
delivery document's targets are then decided again against the snapshot (§9).

A row that opens is exactly what some writer sealed. If it then fails — in den-core's `doc_decode` (bad framing,
invalid JSON, a failed identity) or in a client's own decoding of a settings row (an unknown value tag, a
non-integral `int`) — this reader disagrees with that writer. It stays unreadable, delivery stays paused, and it is
**never removed by compaction**: removing it would delete a row another build reads, for every device.

**The compaction guard.** Corruption touches a row or two. Many rows failing to open at once is more likely a wrong
key, and a compaction would then delete good rows for every device. So a compaction removes the rows it found through
`base` only when den-core's `compaction_guard` allows it: at most **10** rows, and, past a single row, at most
**10 %** of the rows read through `base`. When it refuses, the client removes nothing, delivery stays paused, and it
shows "Delivery paused: library rows can't be read" until the rows read again (the right key) or a person resolves
it.

**A `k` is compacted at most once.** A client keeps, across generations, every `k` it has removed by compaction. If
that `k` comes back (another build wrote it back, §11) and still fails to open here, the client does not remove it
again: it stays unreadable and delivery stays paused, so two builds can't remove and restore one row in a loop, each
round a generation change.

**Newer rows are never removed.** A document whose `format` is greater than 4 is read for the fields this spec
defines, if it decodes by these rules (v2 §6), and is **never written**: writes to it are held, and every target it
holds or names is held (not decided). A row of a newer framing is kept unread, and while one remains no device
delivers. Either way the client shows "Library update required" until its build updates.

### Size

den-edge's value cap for a v4 library is **256 KiB** of `v`, the base64url text of `nonce ‖ ciphertext ‖ tag`
(§13), which is 196,580 bytes of compressed plaintext.

- **A write (§8; not a merge, write-back, intent or settle) whose sealed value would exceed 224 KiB is refused.** A
  merge, write-back, intent or settle may use up to 256 KiB, so a document one den-core version wrote near the limit
  still fits when another re-encodes its merge. No writer sends a value above 256 KiB or a JCS above 8 MiB, so no
  reader finds a conforming document unreadable.
- A refused write is not sent: the client shows the title as full. A kept write (§11) that can never fit is dropped,
  and the title shows why. A write-back merge over 256 KiB: §11.
- Delivery documents never reach the cap through a send: §9 *Writing delivery documents*.

The cap is set by the worst-case season document, the largest document v4 has. Measured with zlib at level 9
(`miniz_oxide` differs by a few percent):

| Season document, per episode | JCS | Compressed | Episodes in 224 KiB |
|---|---:|---:|---:|
| Typical: watched once, value 1, one play | ~140 B | ~17.5 B | ~9,800 |
| Heavy: 8 plays, fractional progress and seconds, a stamped `cleared` | ~320 B | ~99 B | ~1,700 |
| Maximal: every number at v3 §3's bound, 8 plays, random digits | ~530 B | ~224 B | ~770 |

The longest seasons in practice are daily programmes listed as one season, a few thousand episodes, viewed about
once each; they fit with room to spare. A delivery document's entries are of the same order (a typical episode
receipt ~45 B of JCS, v3's worst case ~520 B with a full `sending`). v3's 32 KiB would hold ~1,400 typical episodes,
so a split by coordinate needs the higher cap. Every write rewrites, recompresses and reseals the whole document:
about 50 KB per progress write for a 3,000-episode daily season, which is fine at real sizes.

## 5. Stamps

v2 §4 and v3 §4, unchanged: hybrid logical clock, timeless when `t == 0` whatever `c` and `d`, merges on stamps as
stored, deriving and deciding treat a stamp more than a day ahead as timeless, issuing past a seen reset, and ties
broken by the byte-greater JCS.

## 6. Merge

A merge of two versions of one document MUST give the same result in either order and in any grouping, and merging
a document with itself changes nothing.

- **Title documents**: v2 §5 per field — `status`, `reaction`, `deleted`, `dismissed` by the later stamp; `resume` by
  viewing, then value, then stamp, then `seconds`; `episodesReset` by the later stamp, null lowest; `addedAt` the
  minimum; `watchedAt` the minimum of the non-null values. A field only one version has is kept. `watch` merges as a
  v3 register: `plays` by v3 §3 (union, the lesser watchedAt per key, then the least key and the 7 greatest), `cleared`
  by v3 §3.
- **Season documents**: `seasonReset` by the later stamp, null lowest; `episodes` per valid key, each register by v3
  §3 (`progress` by v2 §5's progress rule, `imported` by OR, `plays` and `cleared` as above). A key only one version
  has is kept.
- **Delivery documents**: §9.
- `format`: both versions are 4 (a writer never writes a higher one).
- **Ties** in any per-field rule: v3 §4 (byte-greater JCS of the field).

### Unknowns

A document keeps what it does not understand, at three levels, and every choice below is a maximum under a total
order, so the merge stays a join:

- **Document level**: the **unknown set** — every top-level member this spec does not define for the kind, together
  with every entry under an invalid key of `episodes` (season) or `entries` (delivery) — comes, wholesale, from the
  version whose **monotone newest stamp** is later. That stamp is the greatest among the fields merged by later stamp
  alone: for a title document `status.at`, `reaction.at`, `deleted.at`, `dismissed.at` and `episodesReset`; for a
  season document `seasonReset`. (`resume`, `progress` and `cleared` merge by viewing first, so a merge can lose
  their greatest stamp; they never count.) A version with no such stamp ranks below every stamp. Equal stamps, or
  neither version having one, go to the unknown set that **ranks higher**: an empty set ranks lowest, and two
  non-empty sets go by byte-greater JCS. A delivery document has no such stamp, so its unknown set always goes by
  that ranking.
- **Register level**: members of a register (an episode register, or a film's `watch`) this spec does not define,
  wholesale from the version whose set of them ranks higher, by the same ranking.
- **Members inside a known field** (§4 *Shape*) travel with that field's winning version.

A v4 writer never adds an unknown member and never changes one.

## 7. Deriving state

v3 §5, unchanged, reading:

- an episode's register from its season document, and its **covering resets** from the series title document's
  `episodesReset` and the season document's `seasonReset`;
- a film's status, resume and reaction from its title document, and its plays and `cleared` from its `watch`.

## 8. Writing

Every write is a set-to-value applied to the documents it touches, decided by den-core `apply_write` (§14) on the
stored state, exactly as v3 §7 decides it on rows:

| Write | Touches | Rule |
|---|---|---|
| Playback progress, finishing by playing | the season document (episode); the title document (film: `resume`, and the play in `watch` on reaching 0.95) | v3 §7 *Playback*, *Films* |
| Mark watched (episode, season, series, film) | each season document with an episode it marks; the film's title document | v3 §7 *Mark watched*; a film's status, resume and play are one document write |
| Un-watch (episode, film) | the season document; the film's title document (`cleared` and `resume` in one write) | v3 §7 *Un-watch*, *Films* |
| Rewatch | as playback | v3 §7 *Rewatch* |
| Season reset | the season document's `seasonReset` | v3 §7 |
| Series reset | the series title document's `episodesReset` | v2 |
| Episode import | the season document | v3 §7 *Import*, the clauses below |
| Film import, title imports (rating, watchlist add, watchlist removal) | the title document | v3 §7 *Title imports* |
| Status, list, reaction, delete, dismiss | the title document | v2 §3 |

- **Episode import** keeps these clauses of v3 §7 *Import*: the condition (no unhidden `progress`, not in progress, no
  covering reset at or later than the import's newest play) and its reset comparison on the play as written; a
  day-only play written as the start of its UTC day; the day-only matching against Den plays at local noon of D or
  at a multiple of 900,000 ms within [D − 2 h, D + 24 h], with its ordering; setting aside the tracker plays equal to
  `⌊W / 1000⌋ · 1000` of a Den play, `⌊w / 1000⌋ · 1000` of that account's receipt watched-at, or a `[-1, …]`
  element of that account's entry; `imported: true` only under the condition; no `progress`, series `status` or
  `dismissed`; the film import's timeless `status`; and the known limits attached to those clauses. It drops the
  one-to-one **viewing window** matching (the three `seedBound` windows, and the full-history check that serves
  them) and the `connectedFrom` floor. Episode imports skip a series whose title document is `deleted`.
- **A film's status during playback** is decided by `apply_write`, never sent with the progress: a progress write
  at or above 0.95 sets `status` `watched`, any other value above 0 sets `inProgress`, and 0 leaves it — v2's
  status set, as shipped clients write it. It is written when the value changes or the write starts a new viewing.
  So a watched film played again is `inProgress` in its new viewing, and the next progress write stays there (a
  `watched` status left in place would start another viewing on every write, v3 §7 *Films*).
- **Replays write nothing.** A write whose stamp is not later than one the state it writes already holds — a title
  field's own stamp (the field's merge rule, §6), an episode register's `progress.at` or `cleared` stamp, a film's
  `resume.at`, `status.at` or `watch.cleared` stamp — writes nothing to it, a stored stamp more than a day ahead
  counting as none. Fresh writes always pass (§5: issued past everything read); a kept write (§11) or a resend
  replayed after a newer change does not overwrite it (§2.2, §2.7).
- **Un-watch** of an episode that is neither watched nor in progress writes nothing; an in-progress one is
  un-watched as v3 §7 says, clearing its resume point.
- **Protocol.** A writer reads the document at seq `s`, applies the write, encodes, and sends `{k, base: s, v}`
  (v2 §2). On a conflict it re-derives the write from the returned version (v3 §10 *Batches are not atomic*) and sends
  again, with jittered backoff, until the write is applied or no longer holds. A writer skips a write whose result
  equals the document it read.
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
still a settings row. `seededThrough` and `seedBound` are read only by the switch (§10); `connectedFrom` by no rule.
No v4 client writes any of the three. Write-back writes back `since` only.

- **Encodings.** `since`: `{"string": <JSON stamp>}`. `lease`: `{"strings": [<device or "">, "<epoch>"]}`.
  `removals`: `{"string": <JSON>}` holding an object with an `approved` stamp, a `held` stamp, or both, written as
  JCS (no client ever wrote v3's bare `"held"`; it is malformed, and a malformed `removals` reads as a **closed**
  latch with no approval — the safety latch fails closed). `unverified`: `{"ints": [<epoch>, …]}`, ascending.
- **Merges** are den-core's `merge` of the settings row, by v3 §6's per-setting rules: `since` to the earlier stamp
  it holds; `lease` by epoch, then an empty holder, then the JCS of the stamped value; `unverified` as the union of
  its epochs, at the later stamp; `removals` by the later `approved` and the later `held`, each on its own, at the
  later stamp; every other setting by the later stamp. A value not in its setting's form ranks below every value that
  is, and two of them merge by the later stamp, so each rule stays a join (commutative, associative, idempotent) over
  any mix of versions.
- **`unverified`** is computed by `pending_targets_v4`, which answers the account's epochs after its read: the ones
  `set:deliver` lists, and every settle epoch ≥ 2 the account's delivery documents hold from two devices, less any
  epoch no entry holds any more. Entries at those epochs are decided as unverified in that same pass. The lease holder
  writes the answer back by compare-and-set on `set:deliver`, **replacing** the setting with it (never merging: a merge
  is a union, which could never drop an epoch), when it differs from the row.
- **`removals`** (the latch, v3 §6). Only list removals whose value stamp is later than `approved` count. The latch is
  **closed while `held` is later than `approved`** (or `held` is set and `approved` is not), and while it is closed
  every counted removal is held. It **closes**:
  - **by count**, when the counted removals pending now, together with the list removals this holder sent for the
    account in the last 120 s (and after `approved`'s time), come to more than 20: a mass removal at once, or a
    trickle whose passes fall within 120 s of each other. A trickle slower than that is not caught;
  - **by rate**, when more than 20 list removals were sent in that window and any list removal stamped at or before
    `approved` is pending. Every pending list removal is then held, and `held` is set past the approval (the later of
    `now` and the approval's next counter, under the approval's device), so the person sees the batch before it goes.
  `pending_targets_v4` then answers `"removals": {"held": <the latest stamp among the removals it closed on, or that
  stamp past the approval>}`. The holder writes that `held` beside the stored `approved`, by compare-and-set, unless
  the stored `held` is already as late. A stored `held` keeps the latch closed with nothing pending: any later removal,
  even one, is held until someone approves, which is intended — it is the person's look at the list that opens it.
- **`removals_sent`** is **required** by `pending_targets_v4` (absent is `invalid_account`), so a binding that forgets
  it fails instead of turning the rule off. Each device keeps its send times for each account **across restarts**
  (and across a browser's tabs), and passes those of the last 120 s. A send it still counts as recent on a monotonic
  clock, after its wall clock stepped back, is passed as `now`: den-core counts no send later than `now`.
- **Approving.** A person approves held removals on any client after seeing **every** one of them listed. The
  approval is `pending_targets_v4`'s `approval`: the latest value stamp among the removals it holds, or the latch's
  `held` (stored, or closing in that pass) when that is later — **never a fresh stamp**. A removal stamped before that
  `held` and still pending is itself held, so listed: a removal made after the list was shown, or by a device whose
  clock runs ahead, is never approved unseen, and a latch whose closing removal was since undone still opens. The
  client writes `{"approved": <that stamp>}` beside the stored `held`, by compare-and-set on `set:deliver`; on a
  conflict it reads the row again and shows the list again.
- **Known limit.** An approval covers every list removal stamped at or before it, including ones a device that was
  offline delivers **after** it. Such a late batch is held only by the rate rule: once more than 20 list removals were
  sent within 120 s. A late batch read with no more than 20 sent in the window goes out under the approval unseen. An
  approval that covers exactly the set it showed would need its count stored beside it; v4 does not do that.

### Delivery documents

```json
{
  "format": 4, "kind": "delivery",
  "provider": "simkl", "account": "4812736",
  "title": {"type": "tv", "id": 1399}, "season": 1,
  "entries": {
    "1": ["w", 0, 1788900000000, [1788900000000, 0, "0f1e2d3c4b5a6978"], [3, 41, "a1b2c3d4e5f60718"]],
    "2": ["w", 1, 1789000000000, [1789000000000, 0, "0f1e2d3c4b5a6978"], [3, 42, "a1b2c3d4e5f60718"], [[-1, 1700000000000]]],
    "3": ["n", 0, null, [0, 0, ""], [0, 7, "a1b2c3d4e5f60718"]]
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
  - owed as baseline: `["b", value, valueStamp, settleOrder]` (v3 §6 *Seeded accounts*, carried from v3).
- **A new entry shape comes with `format` 5** (§4 *Newer rows*). In a format-4 document, an entry that matches no
  shape — an unknown first element, or a known one with other arity or types — is malformed and dropped on decode
  (§4 *Malformed parts*).
- **Merge**, per key: the greater settle order, then the byte-greater JCS. Keys only one version has are kept; the
  unknown set by §6.

### Pending

- A target is **pending** when its current value differs from its receipt (class, viewing, or field value) and is not
  a regression (v3 §6).
- A target with **no receipt** is pending when its value stamp is later than the account's `since`; otherwise it is
  caught up additively — watched, a list add, a rating — as v3 §6 *No receipt*, and any other value settles silently.
  Pending still means a change the command table sends: a list `gone` with no receipt settles silently, since only
  `in → gone` is a removal.
- A target held by a newer document (§4) includes every episode of a series whose **title** document is newer: it
  carries their covering `episodesReset`.

### Writing delivery documents

- **Who.** Only the account's lease holder writes its delivery documents, except write-back after a generation
  change (§11), which merges only entries that were already settled (intents included), the switch's `v4_form`, and
  moves to another key (§12).
- **Fit before sending.** Before sending a pass's commands, the holder builds, with `delivery_write`, each delivery
  document as it would be after **every** intent and settle the pass writes to it, together, at the 256 KiB cap. It
  adds the pass's commands for that document in order; from the first command whose intent and settle no longer fit
  with those before it, every command is held (`receipt_full`) and not sent. So a document that can only grow never
  stops delivery through v3 §6's "stop until a write succeeds".
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
  nothing. Kept writes (§11) never include a delivery document or a `lease`.
- **State at head.** A holder decides a pass only on state read to the log's head (documents and delivery documents
  alike), and pulls imports only after reading to the head.
- The epoch rule of a lease take reads every settle epoch in the account's delivery documents (v3 §6 *Accounts*).

### Removed rules

Every v3 rule that exists for the v2→v3 switch or compares a row's den-edge seq is gone from v4:

- *Seeded accounts*' `seededThrough` test and its compaction renumbering — applied once, by the switch's seeding
  (§10).
- §9's handoff qualification (`head`, `headAt`, `readyFrom`, `unsettled`, `held`, `discarded`), facade, `delivers`,
  waiting commands and drain — v4 has no v2 outbox.
- The `seedBound` and `connectedFrom` windows: v3 §9's acknowledgement of a viewing by a play within a day of its
  watchedAt (Trakt and Simkl) and of a `[p, null, T]` element, and v3 §7's import matching windows (§8).
  `[p, null, T]` elements are carried in `sending` and read by no v4 rule; writers keep them as v3 does.

**Known limit**: a viewing that v3 §9's windows acknowledged (a Den play at or before `seedBound`, or a
`[p, null, T]` element) and that is decided after the switch is checked without the window, so a v2-era scrobble of
it may be sent once more as a play, and on a later pull a v2-era Trakt scrobble may be written as an imported play.
Plays never set watched state and the 8-kept rule bounds them, so only play history changes. Trakt is switched off,
and the switch's dry run (§10) reports how many entries hold either.

## 10. The switch

The first v4 build to open a library below minimum 4 converts it, automatically, with no prompt. A v4 client also
runs the switch, whatever the minimum, when it reads any `rec`, `wat`, `snt`, `ep` or `set:tracker-event:*` row (a
restore, §11).

1. **Open** the rewrite (v3 §9's fenced rewrite) and take `base`. Read the log through `base`. From here until commit
   or abort the device sends no tracker command and no scrobble `stop`.
2. **Dry run.** Build `v4_form` (below). `doc_encode` and `doc_decode` every document. The reference is den-core's
   shipped `library_v3` (`v3_compact`, `episode_state`, `film_state`, `pending_targets`) run on the log through
   `base`, with the same clock. **Derived state** — every title's `rec` fields, and every coordinate's
   `episode_state` or `film_state` — must match the decoded documents exactly. A register the reference cannot
   derive (a malformed member, which `v4_form` drops, §4 *Malformed parts*) is derived on both sides with that member
   dropped, and counted. **Pending commands**, v4's `pending_targets` on the decoded documents against the
   reference's, are compared on (target key, command kind, `added`, `rating`, `p`). **Known limit**: shipped v3 has
   no target builder of its own, so the reference builds its targets with v4's rules and no receipts. The comparison
   therefore cannot catch a target-building bug, and the receipt-dependent rules (*An un-watch survives playback*,
   *Un-watch then re-mark*) show as differences that are not real; it is a log, not a verification of v4's target
   rules (the §15 delivery vectors are). Check the row count, stored bytes and every document's cap. Where the log already holds a
   document, its coordinates and targets are the §6/§9 merge of the converted rows and that document, which v3 never
   saw: they are checked for the round trip and caps only. **Receipts and rows**: every receipt entry the form
   carries (below) must decode in its delivery document as stored — or, for an `n` at −1, as seeding stores it — and
   every v3 row that v3 itself keeps must be placed; anything else is a `receipt_dropped` or `row_dropped` abort
   naming the row or the document and key. The only rows the form drops are the ones it says it drops.
3. A **derived-state** difference, a dropped receipt or row, a round-trip or cap failure, or a `v4_form` failure aborts: write nothing, log the
   diff (titles and coordinates) to the device log, and show "Library update failed" with the reason. The device
   keeps the library read-only and retries on its next launch and every hour while the app runs. A
   **pending-command** difference does not abort: it is logged with its titles and counted, since after the commit
   v4 decides every target by its own rules and no v3 build may be left to deliver.
4. Otherwise **stage** the form and commit `{base, wireMin: 4}`. On `409`, abort and start over from 1. If the fence
   lapsed during the dry run (v3 §9: 5 minutes without a staging request), staging is refused; start over from 1.

The device that committed shows the toast "Library updated to v4". Settings › About shows "Library format: v4" as a plain
read-only value beside the existing "Version" row ("v3" while a failed switch leaves the library unconverted). There is no other UI.

The dry run also logs the row count, stored bytes, the largest document, the pending-command differences, and the
number of entries the §9 known limit covers.

**Other devices.** den-edge refuses a build below v4 on reads and writes (`426 {"min": 4}`). A shipped v3 build
logs "library log requires wire 4; local writes remain queued", shows nothing, and keeps showing the state it last
read until its owner updates it. Its queued writes are never converted. A v4 build shows "Library update required"
on a `426` (§13 *Client work*). A v4 build that meets `rewrite_in_progress` keeps its writes (§11) until it observes
the generation change, then writes back.

**Pre-v3 logs.** A log that holds an `ep` or `set:tracker-event:*` row and no `wat`, `snt` or `set:deliver:*` row is
a pre-v3 backup. The client does not convert it: it shows "Library backup predates v3" and writes nothing.

**Known limit**: a v3 lease holder that sent a command just before the rewrite opened and whose settle is then
refused leaves the target unsettled in the log; v4 decides it again against the snapshot (v3 §6), so a list or rating
command may be sent twice. A watch play is covered by its intent.

### The v4 form

`v4_form` takes every row through `base`, with seqs, and the performer's id. It starts from v3's compaction form
(den-core `v3_compact`: `wat` and `snt` rows merged, stray `ep` rows and v1 events folded through v3 §8).

- **v3 row metadata.** A v3 row's `kind`, `schema`, `block` and `entries` are consumed. No writer of v3 rows adds
  unknown members (v3 has no newer format), so row-level unknown fields and invalid keys of `rec`, `wat` and `snt`
  rows are dropped and counted in the log; v3 reads none of them. A `wat` or `snt` key that fails v3's block condition
  is dropped too, since v3 never read it and it would collide with another block's register.
- **Title documents.** For each title, the `rec` row's known fields; for a film, `watch` = the `"0"` register of
  `wat:movie:<id>:0:0` (`plays`, `cleared`, and its own unknown members as register-level unknowns). A film `wat`
  row's other keys are dropped (v3 reads only `"0"`). A `wat` row with no `rec` row makes a title document with no
  `rec` fields.
- **Season documents.** For each series and season, the union of its blocks' valid-key registers, with their
  register-level unknown members; `seasonReset` from block 0 (v3 ignores it on other blocks).
- **Delivery documents.** Every v3 receipt carried as stored, settle order and all: an episode's entry into its
  season delivery document; a `t<shard>` row's `rec:<type>:<id>#watch|list|rating` entries into that title's
  delivery document. Both shipped v3 clients write a title's receipts instead as a row with `target`
  `rec:<type>:<id>` and entries keyed `watch` (films), `list` and `rating`, stored under the same `t<shard>` name;
  those entries go into that title's delivery document under the same keys. A key v3 never reads (`watch` on a
  series, any other key) is an invalid key. Two rows holding one entry merge by §9's entry merge. An `snt` row
  whose `target` is neither `wat:<type>:<id>:<season>:<block>` nor `rec:<type>:<id>` cannot be placed.
- **Seeding** (v3 §10's default receipts). For an account whose `set:deliver` holds a `seededThrough` at or below
  `base`, a target whose v3 row has seq above it — the `wat` row for an episode; for a film's watch, the greater seq
  of its `rec` and `wat` rows; the `rec` row for a list or rating; a folded row taking the greater of its parts' seqs
  (v3 §9) — is seeded:
  - with no receipt: the default entry for its kind (`n`, `out`, `null`), value stamp `[0, 0, ""]`, settle order
    `[0, n, <performer>]` (`n` counting from 1 per account);
  - with an `n` entry at `p` −1: the same entry stored at `p` 0, its watched-at kept.

  Against an `n` receipt at 0, v3 §6 *Rewatch* and *Earlier viewings* already count every viewing, as v3's
  above-`seededThrough` rule does. Any other target stores nothing new: §9 *Pending* reads the same `since` and
  decides as the v3 spec does. Shipped v3 code reads no `seededThrough`, so targets above it can differ from the
  reference (a no-receipt null rating, say); the dry run logs and counts them (step 3).
- **Documents already in the log** are merged (§6, §9) with the documents converted from v3 rows; seeding applies
  only to a target with no entry after that merge.
- **Settings.** Every `set` row except `set:tracker-event:*` is staged with `k` and `v` unchanged; the commit's new
  generation ends every lease (v3 §6).
- **Unknown kinds.** Every row of a kind `v4_form` does not know is staged with `k` and `v` unchanged. A v3 row that
  v3 itself drops (failed identity) is dropped.

It holds no `rec`, `wat`, `snt`, `ep` or `set:tracker-event:*` row. `v4_form` **fails** when the form would exceed
den-edge's 50,000-row limit or its stored-bytes cap, when any document would exceed 224 KiB sealed, or when a v3 row
carries a `schema` above 3 (a `rec` above 2); that aborts as step 3 says.

## 11. Clients, restores and generations

- **Headers.** Every request from a v4 build to `/lib/{id}/…` carries `x-den-wire: 4` and `x-den-generation` (`0`
  when creating a library). A batch's rows and its `x-den-generation` come from the same read.
- **Refused writes are kept** — on `409 rewrite_in_progress`, `409 generation_changed` or `426` — durably, as ops
  (never as encoded rows), and sent after `Retry-After`, through write-back when the generation changed. A kept write
  never includes a delivery document or a `lease`. Kept v2 or v3 work is discarded.
- **Write-back after a generation change** (a switch or a restore), in v4 form only. The client forgets its head and
  bases, reads from 0, and for every document it holds writes the merge of its copy with the log's when that differs
  from the log's (§6), applies its kept writes as ops, merges every **settled** delivery entry it holds (intents
  included) by §9's order, and writes back `since` (v3 §10's credential rules apply to `set:trackers`). Never a
  `lease`. When a merge would exceed 256 KiB sealed (two devices that added episodes offline, say), the log's
  version stands, the held copy's extra registers are dropped, and the title shows why. Each kept write stands
  alone: one that touches a newer document (§4), or a series whose title document is newer, is held and reported;
  one that no longer applies is reported and dropped; neither stops the others.
- **Restores.** A restored v4 log needs only write-back. A restored v3 log, or a log in which v2 or v3 rows appear
  (a store restored with its minimum lost, written by an old build), runs the switch (§10), merging the log's
  documents, and then write-back. A v4 client that reads a minimum below 4 runs the switch too, which then only
  raises the minimum.
- **Delivery after a generation change** waits as v3 §6 *Taking* says: 10 minutes of observation under the new
  generation, a fresh take, and decisions only on state read to the head. A device that holds documents to write back
  also waits until its write-back has landed: a receipt it still holds would otherwise be missing from the log, and a
  target decided without it could settle past it (a list removal settled `gone` silently, its held `in` then losing to
  that newer settle). A held document den-core refuses is reported and dropped, so one bad document can't hold back
  the rest.

## 12. Moving a library: key reset, linking, new libraries

- **Key reset** (v2 §1) and **linking a device's own library** decode every document and re-seal it under the
  destination key's name for its identity — which the document carries — merged with the destination's version of
  that name (over 256 KiB: as §11 write-back). Delivery documents move the same way (their names carry the account
  id). A `format` > 4 document is re-sealed under its rebuilt name with its plaintext unchanged. Settings rows move as
  v2 says. No row is copied as sealed bytes.
- **A row that cannot be renamed** — a newer framing, or an unknown kind — has a name this build cannot rebuild. While
  one exists, a key reset or link does not start, and the client shows "Library update required".
- Linking into a library below minimum 4 runs the switch there first (§10).
- **New libraries.** A library's first batch carries `x-den-wire-min: 4`; den-edge sets the minimum to the greater of
  that and the minimum of the library named in `x-den-library-member` (v3 §10). A key reset whose `DELETE` of the old
  library gets `generation_changed` re-copies first.

## 13. den-edge

den-edge interprets no row. Beyond v3 §13:

- **Value cap**: 256 KiB of `v` for a library whose wire minimum is 4 or more; 32 KiB below. Rows staged in a rewrite
  may be up to 256 KiB; a commit whose `wireMin` is below 4 is refused with `400 {"error": "value_too_large"}` while
  any staged row exceeds 32 KiB. The cap is enforced in four places today, and each takes the per-minimum cap: the
  batch path, `rewrite_rows` (`src/library.rs`), `apply_bounded` and the store-level `rewrite` (`src/library/v3.rs`).
- **Batches**: still at most 200 writes and a 2 MiB body; a client splits by bytes. A batch response carries at most
  **2 MiB of conflict values**; beyond that a conflict carries `{k, seq, "omitted": true}`, and the client reads the
  row from `/changes`. (A missing `v` already means "no row": den-edge answers one as `{seq: 0, v: null}`.)
- **Charge**: a library is charged by its stored bytes, the sum of `k` + `v` over its rows (den-edge 0.241.2), against
  its stored-library cap (32 MiB today); staging is charged the same way, so a staging that is accepted also commits.
- **Rows**: the 50,000-row limit stays. A v4 library holds about titles × (1 + seasons) × (1 + accounts) rows; the
  dry run reports the count (§16.6).
- Accepting `x-den-wire: 4`, and applying v3 §10's refusal, generation and highest-minimum rules with minimum 4,
  need no new mechanism.

### Client work

- **TV staging cap.** `LibraryLogClient.rewriteBatches` refuses a staging request over 256 KiB of body
  (`maxRewriteBodyBytes`), so a near-cap document plus its envelope cannot be staged. It rises to den-edge's 2 MiB
  `/rewrite/:rid/rows` body cap.
- **"Library update required" on the TV.** A shipped v3 TV shows nothing on `426` (§10 *Other devices*). The v4 TV
  shows "Library update required" on `426` and wherever §4 or §12 call for it.

## 14. den-core

The rules live in den-sync so both clients share them. Clients do the sealing (v2 §2) and the HMAC check against the
name den-core returns. An op v3 also defines has its own name for v4: `episode_state_v4`, `film_state_v4`,
`pending_targets_v4`, `settle_v4` and `write_back_v4`. The v3 names keep exactly v3's shapes, so no op chooses a
version by which fields a request carries.

- `doc_decode`: plaintext → document and its name with the malformed parts it dropped, a JSON row, or unreadable /
  newer with its reason (§4).
- `doc_encode`: document → plaintext, or `too_large` against the cap it is given (224 KiB for a §8 write, 256 KiB
  otherwise; §4).
- `doc_name`: identity → name. `doc_merge` (§6, §9).
- `title_state`, `episode_state`, `film_state`: documents + resets + clock → v3 §5's derived state.
- `apply_write`: write kind + the documents it touches + clock → the documents to write, or nothing (§8: every write
  kind, imports included).
- `compaction_guard`: unreadable rows + rows read through `base` → whether a compaction may remove them (§4).
- `merge` of a `set:deliver` row: §9 *Account settings*' per-setting rules.
- `pending_targets`: documents + delivery documents + `set:deliver` (with the holder's `removals_sent`) + clock →
  commands, the `held` to write when the latch closes, the `approval` of what it holds, and the account's
  `unverified` epochs (§9, v3 §6); `decide` (v3 §6);
  `settle`: outcome + built-from value + entry → the entry, or nothing; `delivery_write`: delivery document + every
  settle and intent of one write → the document, or `too_large`; `lease` (v3 §6).
- `v4_form`: every row through `base`, with seqs, + the performer id → the switch's rows, or a failure (§10). Keeps
  `v3_compact` for it.
- `v4_dry_run`: the log through `base` + `v4_form`'s rows + clock → pass or abort, the pending-command differences,
  and the counts §10 logs. Its reference is the shipped `library_v3` module, kept unchanged for it.
- `write_back`: held documents, delivery documents and kept writes + the new log → the writes to send (§11).

## 15. Vectors the implementation MUST pin

- **Round trip.** For every v3 vector row set, `doc_decode(doc_encode(L)) == L` in JCS for the documents `v4_form`
  makes of it.
- **Framing.** A `0x00` plaintext and a `{` plaintext both read; a JSON `set` row is never compressed; a `0x00`
  plaintext with a trailing byte after the final block, and a JSON plaintext with leading whitespace, unreadable; a
  plaintext starting with another byte read as a newer framing, kept, and stopping delivery.
- **Bounds.** An 8 MiB + 1 inflate unreadable; depth 33 unreadable; a lone surrogate unreadable; an HMAC mismatch
  unreadable; a known-kind document at `format` 3, or with no `format`, unreadable; a known-kind document in the `{`
  form read; an extra member inside `resume` kept through a merge and not unreadable; `doc_encode` refusing a §8
  write over 224 KiB, accepting a merge or settle up to 256 KiB, and refusing anything over 256 KiB sealed or 8 MiB
  of JCS; a `format: 5` document read but never written, its targets held.
- **Malformed parts.** A season document with a wrongly typed `plays` value in one register: that `plays` dropped on
  decode, the rest of the season read, no compaction, delivery not paused; a malformed delivery entry dropped and
  its target decided as having no receipt.
- **Unreadable rows.** A row that fails to open stopping delivery, then removed by a compaction staged through
  `base`, with delivery resuming and the removed delivery document's targets decided against the snapshot; the
  removal skipped when that `k` opens at `base`; a row that opens but fails a reader's decoding kept. The guard: one
  row always removable; ten of a hundred removable; eleven, or two of nineteen, refused.
- **Account settings.** 21 removals approved and then 21 more: only the later 21 held, closing at the latest of them,
  and an approval of what is held answered as that same stamp; a stored `held` later than the approval holding the
  five of them still pending and none of the approved; an approval reaching `held` opening it; 16 removals sent in
  the last 120 s and 5 pending closing it, 15 and 5 not, sends older than 120 s not counting; `unverified` gaining an
  epoch two devices settled under and losing a listed epoch no entry holds; one device's receipts at one epoch
  staying verified; `set:deliver` merges — the earliest `since` with a malformed version ranked below (`a` 1000 at
  5, malformed `b` at 6, `c` 2000 at 7: `a⊔b = a`, `b⊔c = c`, `a⊔c = a`), the greater lease epoch and an empty
  holder at one epoch, the union of `unverified`, and the later `approved` beside the later `held` — and the laws
  over random triples of them, malformed values included.
- **Identity.** A season document under a title's name, and a delivery document of another account, unreadable.
- **Merges.** Merge laws (commutative, associative, idempotent) over random triples of title, season and delivery
  documents, with 30 plays, the same imported play from two sources, `cleared` in one part, settle orders from skewed
  clocks, and unknowns at all three levels. Fixed vectors: §6's counterexample (A: `resume` viewing 2 at t=5 with
  unknown set UA; B: `resume` viewing 1 at t=9 with UB; C: `status` at t=7 with UC — both groupings give UC), its
  season form through `progress.at` and `cleared`; an unstamped version (A: newest stamp 9 with unknown set `a`; B:
  no stamp with `z`; C: newest stamp 5 with `b`; JCS order a < b < z — both groupings give `a`); an empty unknown set
  meeting a non-empty one at equal stamps, at document and register level, keeping the non-empty one; and a delivery
  entry triple at settle orders 5, 3 and 7 giving the one at 7 in both groupings.
- **Deriving.** v3 §12's deriving cases, run on documents; a series reset in the title document hiding a season
  document's registers.
- **Every write kind** in §8's table, including a film finished by playing writing `resume` and its play in one
  document, an un-watch writing `cleared` before the bump, mark-watched writing nothing on an imported or watched
  episode, and each kept import clause (v3 §12 *Imports*); a tracker play inside a former viewing window written;
  a watched film played again `inProgress` in one new viewing, its next tick in the same one; a finished film's or
  episode's next tick at or above 0.95 staying in its viewing with no second play; a replayed title write
  and a replayed un-watch older than the stored stamps writing nothing.
- **Delivery.** v3 §12 *Delivery* run on delivery documents; a no-receipt `watched` stamped before `since` caught up
  additively and one stamped after it pending; a no-receipt `unwatched` before `since` settling silently and after it
  sent; a timeless rating not pending against a receipt with its own value stamp; an in-progress replay of an
  unhidden imported watch sending no un-watch; a film re-marked after a delivered un-watch sending the un-watch
  first; a list `gone` with no receipt settling silently; a command whose settle would not fit held as
  `receipt_full` and not sent; a pass whose intents and settles
  each fit alone but not together, holding every command from the first that does not fit; a conflicted settle
  discarded and decided again; a chained settle on the holder's own previous seq applied; a chain
  broken by another writer's version refused; an unknown outcome ending the chain; an intent then settle chained.
- **Switch.** For a corpus of v3 libraries, `v4_dry_run` against shipped `library_v3` passes with no derived-state
  difference — including targets below and above `seededThrough`, accounts with and without it, `["b", …]` entries,
  `n` entries at −1 above `seededThrough`, unverified epochs, and accounts connected after the v3 switch; a
  no-receipt null rating above `seededThrough` logged and counted as a pending-command difference, and the switch
  committing; a derived-state difference aborting. Blocks merged into one season document; `seasonReset` from block 0 only; row-level
  unknowns, invalid keys, a film `wat` row's non-`"0"` keys and a key failing the block condition dropped and
  counted; a stray `ep` row and a v1 event folded; `set:tracker-event:*` dropped; unknown kinds and every other `set`
  row staged unchanged; a log holding documents and v3 rows merged; failure on rows, bytes and a too-large season;
  a pre-v3 log refused, and a v3 log with no `wat` row but an `snt` or `set:deliver:*` row converted; title receipts
  in the `rec:<type>:<id>` target form carried into the title's delivery document with no pending-command
  difference, an unplaceable `snt` row and a lost receipt entry each aborting with `receipt_dropped`; the window
  known-limit count.
- **Write-back.** A held document merged and written only when the merge differs; kept ops re-applied; settled
  entries merged by §9's order; kept v3 work discarded; never a `lease`; a merge over 256 KiB leaving the log's
  version.
- **Moves** (`../vectors/library-v4-moves.json`, from `tools/move-vectors.mjs`: a move is the clients' sealing, so
  den-core's vectors can't hold it). A key reset re-sealing every document under its new name; a `format: 5` document
  re-sealed with its plaintext unchanged; a key reset refused while a newer-framing or unknown-kind row exists; a new
  library's first batch at minimum 4.
- **den-edge.** A 256 KiB value accepted at minimum 4 and refused at minimum 3 on each of the four paths; a staging
  request carrying a 256 KiB value accepted; a commit at `wireMin` 3 holding a 33 KiB staged row refused; `426` with
  `min` 4; a batch whose conflicts exceed 2 MiB returning the rest as `{k, seq, "omitted": true}`.

## 16. Settled questions

1. **Seasons over the cap**: no sub-season coordinate. Typical seasons fit ~9,800 episodes and heavy ones ~1,700;
   the dry run reports the largest document and `v4_form` fails on one over the cap. A sub-season shape can come in a
   later switch if a real title needs it.
2. **The delivery gate** is deleted; the windows' loss is a stated known limit (§9), counted by the dry run.
3. **v3 §7's import windows** are dropped (§8); the cost is play history only, and Trakt is off.
4. **Compressor**: `miniz_oxide` level 9, pinned in `Cargo.lock`; inflate capped at 8 MiB, trailing bytes rejected
   (§4).
5. **`set:deliver` stays a settings row**: the lease's compare-and-set on a small uncompressed row is unchanged.
6. **Row count**: per-season delivery documents stay. If the dry run shows the owner's library above half of
   den-edge's 50,000 rows, ask den-edge for 100,000 (stored bytes are the real bound); otherwise change nothing.
7. **v3 §14's open items** are about v2-era handoffs, which v4 never reaches: a pre-v3 log is refused (§10).

## 17. The download queue

The debrid downloads a household has asked for, shared by every client (oxyc/den#202): a download queued in one
place shows, and plays, in every other. The queue is **settings rows** (v2 §3): sealed, uncompressed (§4), merged by
den-core, and invisible to den-edge. Vectors: `../vectors/library-v4-downloads.json`.

### Rows

**One row per download**, `set:download:<content>`, where `<content>` names what is downloaded:
`<type>:<id>:<season or -1>:<episode or -1>` (`movie:550:-1:-1`, `tv:1399:2:3`). A season is one row per episode.
Each value is stamped and tagged as v2 §3 says:

| Value | Tag | Meaning | Written by |
|---|---|---|---|
| `release` | `string`: JCS of `{identity, label, url, sizeBytes?, cached?}` | the release being fetched. `identity` is the info-hash, else the lowercased release file name, else the URL. `url` is the writer's play ticket (below) | the starter; the holder on a fallback |
| `title` | `string`: JCS of `{mediaType, mediaId, imdbId?, season?, episode?, title, posterPath?, stillPath?, originalLanguage?, preferredLanguage?}` | what to show, the IMDb id den-scout lists the content under, and the languages the dub rule ranks against | the starter; any client filling in a title or poster that was empty |
| `queuedAt` | `int` (ms) | when the add was made: the start grace, the stall clock and every lifetime count from it | the starter; the holder on a fallback or a resume |
| `tried` | `strings` | identities given up on for this content, oldest first | the holder |
| `candidates` | `int` | how many releases the last resolve offered (not a dead swarm) | the starter, the holder |
| `exhausted` | `bool` | no working release is left | the holder |
| `announced` | `bool` | "ready" was announced | the holder |
| `reported` | `bool` | den-scout has described the download at least once | the holder |
| `reannounced` | `bool` | the current release had its one reannounce | the holder |
| `resumeAt` | `int` (ms), or `null` | a held-back add is made again at this time | the starter, the holder |
| `progress` | `string`: JCS of `{lastProgress, progressAt}` | the stall clock | the holder, coarsely (below) |
| `removed` | `bool` `true` | a tombstone: the download was cancelled, removed or pruned | anyone |

- **Merge** (den-core `download_merge`, and `merge` of any `set:download:*` row): each value by the later stamp, as
  every setting; then every value stamped **earlier than `removed`** is dropped. A removal therefore ends the row
  in every merge order, and a later start — a fresh stamp on every value — lives after it. `removed` is never
  cleared.
- A row is **live** while it holds `queuedAt` and `release`. A row holding only `removed` is a tombstone and is kept:
  a client that read the row before it was removed would otherwise write the download back.
- **Starting** writes, in one write, `release`, `title`, `queuedAt` and `candidates` (and `resumeAt` when the add
  was held back), all with fresh stamps. Starting a content whose row is live with the same `identity`, and not
  `exhausted`, writes only a fresh `queuedAt` and makes the add again (den-scout de-duplicates it); with another
  release, or after `exhausted`, it restarts: `removed` at a fresh stamp, then every value at a later one. Starting
  needs no lease: two clients starting the same content write the same row, and the merge keeps one `release`.
- **Never synced**: the live figures (percent, rate, ETA, seeds, peers), which every client asks den-scout for
  itself with `?probe=1`, and a release's `proxyHeaders`, which carry the debrid account's bearer token. The play
  ticket *is* synced, on purpose: it is a bearer capability for one release for a day (to play it, and to cancel or
  reannounce it), and the row is sealed under the library key like every row.
- **Cancelling** at the debrid (a Cancel, or a fallback giving up on a release) is allowed only when den-core's
  `download_cancel_safe` says so: never while another live, non-exhausted row names the same `release.identity`,
  whatever that row's state. One season pack is one torrent across every episode, and a sibling reading "not started",
  "unreachable" or "ready" may still be fetching or playing it. The row is tombstoned either way.
- **Play tickets** are minted per client and per resolve, so `release.url` is the writer's. A client whose own
  addon routes cannot reach that URL — a browser reading a TV's LAN URL, a TV reading a browser's relay path — or
  that is told the ticket expired (`410 ticket_expired`) resolves the content again and takes the release with the
  same `identity`. Only the holder writes the new `url`.
- **Live rows** are capped at 100 (`download_prune`). A row is well under 1 KiB.

### The lease

`set:download-lease` holds one setting, `lease`: `{"strings": ["<device id or empty>", "<epoch>"]}`, merged as
`set:deliver`'s `lease` (§9): by epoch, then an empty holder, then JCS. It is taken, renewed and released by
compare-and-set on the row's seq, exactly as v3 §6 *Holding* and *Taking* say, with den-core's `lease` deciding:
renew at 60 s, expire at 120 s, take after 10 minutes of observing the row's seq unchanged (or at once when it names
no device). It is its own row, so it never contends with a tracker's lease.

- **A row naming this device that this process never took** (a relaunch, a second window of one browser, which
  shares its device id) is **another holder**: the process takes only after 10 minutes of observing the row's seq
  unchanged, as for any other device. A live holder renews every 60 s, so a second window never takes from a first
  that is still running.
- **Holding** is checked against the row as well as the clock: a process holds the lease only while its own take or
  renewal succeeded less than 120 s ago **and** the row as last read still names this device at the epoch it took.
  A row at another epoch means another process took it. So a holder away for more than 120 s (a TV in the
  background, a hidden tab) comes back to its own row as another holder's: observation counts from its last renewal,
  so after 2–10 minutes away it waits out the rest of the 10 before moving any download on, and after longer it takes
  again at once. A relaunch always waits the full 10. That is a pause in fallbacks and prunes, never a stuck queue.

**Only the holder** writes `tried`, `exhausted`, `announced`, `reported`, `reannounced` and `progress`, a fallback's
`release`, `queuedAt` and `candidates`, a resumed add's `queuedAt`, `progress` and `resumeAt`, a prune's tombstones
and a renewed `url`. It checks the lease immediately before each such write and writes it by compare-and-set on the
seq of the row **as read when it decided** (never a seq read afterwards); a conflicted write is **discarded** and
decided again on the next pass against the row as read. Every client may start, cancel and remove.

**A prune** is decided only on rows read since the last pull of the log, by the holder, and each tombstone is that
compare-and-set write: a device back after days away never tombstones a row from its own old copy, over a start
another device wrote meanwhile.

`progress` is written when the stall clock moved and the row's `progress` is absent or was stamped at least
5 minutes ago — never on every poll. A holder keeps its own clock between writes and passes it to
`download_status`, which reads the later of it and the row's.

A client with no lease still polls the rows it shows and shows its own "ready" notice; the holder writes `announced`,
so a relaunch doesn't announce it again.

### den-core

| Op | Input | Answer |
|---|---|---|
| `download_merge` | `a`, `b`: two versions of one row | the merged row (above) |
| `download_status` | `row`, `answer` (absent before any), `clock?`, `now` | `{state, clock, stalled, reannounce, write_progress, report, announce}`, with `service` (refused), `until` (paused) or `renew` (a lapsed ticket; `state` null) |
| `download_next` | `row`, `releases`, `resolution`, `complete` | `{decision: next \| exhausted \| undecided, index?, candidates?, tried}` |
| `download_prune` | `rows`, `states` (each row's last state, by name), `watched` (each row's own episode or film watched, by name), `now` | `{remove: [row name…]}` |
| `download_cancel_safe` | `row`, `rows` (every download row) | `true` when the row's release may be cancelled |
| `rank_releases` | `releases`, `original?`, `preferred?`, `tried?` | `{order, best, pick}`, indices into `releases` |

**An answer** is what den-scout said to a probe, as the client read it: `{"kind": "ready"}`, `{"kind":
"preparing", "progress"?, "etaSeconds"?, "bytesPerSecond"?, "fetch"?: {"state"?, "seeds"?, "peers"?, "service"?}}`
(den-scout's `202`), `dead` (`404`), `not_queued`, `service_unavailable` (`service`?), `reserved_for_play`
(`until`, ms), `ticket_expired` or `unknown` (no answer).

**`download_status`**: `state` is one of `starting`, `fetching`, `not_started`, `refused`, `paused`, `unreachable`,
`ready` and `no_working_release` (`exhausted`). Silence (`dead`, `not_queued`, `unknown`) within **3 minutes** of
`queuedAt` is `starting`. The stall clock starts at the **later** of `progress.progressAt` and `queuedAt` (so a
release asked for again — a re-press, a resumed add — is not stalled by progress older than the new add), or at the
caller's own clock when that is later. It moves only on a `preparing` answer whose progress rose or whose rate is
above 0, and never while the debrid reports the fetch `stalled` with an empty swarm (seeds and peers known and 0).
`stalled` is true after **20 minutes** without a move, on a `preparing` or `dead` answer, and at once when the debrid
reports the fetch `failed`. `reannounce` is true once, at half that, while the swarm is reported empty and the row's
`reannounced` is not set. `report` and `announce` say when the holder writes `reported` and `announced`.

**`download_next`** counts the row's release as tried and answers `tried` for the holder to write. With
`resolution` `streams`, it picks among the releases that are not a dead swarm (`cached` false and `seeders` 0), less
`tried`, as `rank_releases`' `pick` does with the row's `originalLanguage` and `preferredLanguage`; with none left it
is `exhausted`, or `undecided` when `complete` is false (a source didn't answer). `none` (no sources at all) is
`exhausted`; `undecided` (unreachable) decides nothing.

**`download_prune`** never expires an unfinished row. A request that has no working release is still a user's
request, not a cache entry, and stays available for later source retries until the user removes it or it succeeds.
An `announced` or last-read `ready` row also stays while unwatched. Once `watched` says its own content was watched,
it remains as recent download history for two days from the `announced` stamp (falling back to `queuedAt` before
that stamp is written), then is removed. `watched` is the caller's own watch state for that row's **episode or
film**, never the series' standing: a series record left `watched` from a finished earlier season says nothing
about a new episode still downloading, and must not prune it. A row the user removes by hand is the caller's own
tombstone, not this op's. Past 100 live rows the oldest go too, ready or not. The holder writes `removed` for each.

**`rank_releases`** is the one release ranking both clients use. A release is den-scout's stream `attributes`
(`resolution`, `codec`, `dolbyVision`, `hdr`, `threeD`, `sizeBytes`, `seeders`, `cached`, `probed`,
`audioLanguages`, `untaggedAudioTracks`) with its `identity`. Its **tier**, lowest first, is the sum of: 32 for a
dead swarm, 16 for `cached` false, 8 for a proven dub, 4 for a runt, 2 for a codec the Apple TV only decodes in
software (AV1, VP9, MPEG-4 Part 2, MPEG-2, VC-1, by any of their tags) and 1 for 3D. A **dub** is a probed release
with no untagged audio track, at least one tagged one, and none in `preferred` or `original` (languages compared as
canonical two-letter codes); with neither language given, nothing is a dub. A **runt** has a size below a quarter
of the median of the sizes reported, when at least three are. `order` sorts by tier, stably. `best` is the lowest
tier's best picture (`resolution` × 4, + 2 for Dolby Vision, + 1 for HDR or Dolby Vision), the first of equals.
`pick`, the release a download starts with, leaves out `tried`, then takes `best` of the cached releases, or of all
of them when none is cached; the runt median is over that set.
