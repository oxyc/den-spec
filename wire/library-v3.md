# Library wire format, v3

v3 changes how **episode progress**, **play history** and **tracker delivery** are stored. Everything else —
keys, sealing, title and settings rows, the log protocol — is [library v2](library-v2.md) unchanged, and a v3
client follows every rule there unless a section below replaces it. Vectors: `../vectors/library-v3.json`
(generated with the first implementation; §12 lists what it MUST pin). Every rule below is implemented once, in
den-core, as ops both clients call (§11); a client never re-implements a merge, fold, gate or delivery decision.

Why: in v2 each episode is its own row (~1 KiB charged), and each change a person makes is also kept forever as a
`tracker-event` row carrying whole rows (~3.6 KiB per episode, ~6 KiB per film). A heavy viewer fills a library in
a few thousand episodes (oxyc/den#163). v3 stores 32 episodes per row, keeps no per-change history, records
rewatches, and does it without any client losing or contradicting state another wrote.

Words: *MUST* is a rule a client breaks at the cost of other clients; *should* is advice.

## 1. How a library moves to v3

There is **no mixed period**: no library is ever written in both formats at once.

1. **Ready builds.** v3-capable builds read and write **v2 only** on a v2 library, and report `format` 3 for
   their device (§9). Nothing about the library changes.
2. **The switch.** Once every device is ready, the delivering device rewrites the library into its v3 form and
   raises its wire minimum in one atomic step (§9).
3. **v3.** From then on every client writes v3 only, and den-edge refuses any request without `x-den-wire ≥ 3`
   (§10), so a v2 build can neither write nor delete.

## 2. Guarantees

1. **Convergence.** Clients that have read the same rows derive the same state for every episode and title, in any
   arrival order.
2. **No lost update.** Changes to different episodes or fields never overwrite each other; changes to the same one
   resolve by v2's rule for that field (v2 §5).
3. **No lost state at the switch.** The v3 form derives, for every coordinate and title, the state v2's reference
   reading (§8) derives from the log it replaces. It replaces the log atomically, and only while no other write can
   land (§9).
4. **Imports never override a person.** State learned from a tracker or an import file loses to any change a person
   made on any device, in any order.
5. **Bounded growth.** Rows grow with titles and one per 32 episodes, never with changes; no row can exceed
   den-edge's value cap.
6. **Idempotence.** Replaying any write, any number of times, in any order, changes nothing further.
7. **Nothing delivered twice, nothing held dropped.** A tracker change settled before the switch is not sent again
   after it, and a change unsettled or held before it stays pending.

## 3. Watch row — name `wat:<type>:<id>:<season>:<block>`

```
block = floor(episode / 32)
```

A series' episodes live in the watch row of their season and block. A film has one watch row with `season` 0,
`block` 0 and one entry `"0"`, which holds plays only (§5). The block is a pure function of the coordinate, so every
client addresses the same row. Season 0 (specials) is a season like any other.

```json
{
  "kind": "wat", "schema": 3,
  "title": {"type": "tv", "id": 1399}, "season": 1, "block": 0,
  "seasonReset": null,
  "entries": {
    "2": {
      "progress": {"value": 1, "at": [1789000000000, 0, "a1b2c3d4e5f60718"], "viewing": 1, "seconds": 3000},
      "imported": false,
      "plays": {"-9005499254740992": 1700000000000, "0": 1760000000000, "1": 1789000000000},
      "cleared": null
    }
  }
}
```

- **Identity** is `title`, `season`, `block`; a reader rebuilds the name and checks the HMAC (v2 §2). A film row MUST
  have `season` 0, `block` 0 and no entry but `"0"`.
- **`entries`** — key → **register**. A valid key is the canonical decimal form of an integer `0 ≤ e ≤ 99999` with
  `floor(e / 32) == block`. A reader derives nothing from any other key but keeps it through merges and rewrites
  (§5), so a key a later version allows is never erased.
- A **register** (no other fields; a future field arrives with a new `schema`):
  - `progress` (series only): a v2 progress value `{value, at, viewing, seconds?}` (v2 §3). It holds **real**
    progress only — a writer MUST NOT put a timeless stamp here (§4). Absent when the episode has none.
  - `imported`: true when an import said the episode is watched and gave no stronger claim (§7). Merge: OR.
  - `plays`: viewing key (decimal string) → `watchedAt` (Unix ms). Non-negative keys are Den viewings. Negative keys
    are imported plays, keyed `watchedAt − 2⁵³` so they order oldest first, sit below every Den viewing, and the
    same imported play from two sources is one key. Keys and times within |x| ≤ 2⁵³ − 1.
  - `cleared`: the highest viewing an explicit un-watch cleared, or null. Merge: the maximum; null is lowest.
- **`seasonReset`**: a stamp or null. Written only to block 0; a reader ignores it on any other block.
- **Numbers**: `value` in [0, 1]; `seconds` in [0, 10⁷]; every stamp within den-core's bounds (|t|, c ≤ 2⁵³ − 1).
  A stamp's `d` MUST be `""`, `"local"` or 16 lowercase hex characters; a writer never writes another.
- `schema` is 3. Row-level unknown fields are kept (v2 §6), at most 1 KiB of JSON in total.

**Size.** A worst-case register — maximal numbers, a 16-hex `d`, 8 plays with maximal keys and times — is under
600 bytes of JSON, so a full block with 1 KiB of row-level unknowns is under 21 KiB plaintext, under 29 KiB once
sealed and base64-encoded: below den-edge's 32 KiB value cap, which bounds the encoded value (about 24 KiB of
plaintext). A typical register is ~120 bytes and a typical full block ~5 KiB sealed. den-edge charges
`2(k + v) + fragment + 192` per row, so a typical full block costs ~15 KiB of budget, ~0.5 KiB per episode
(v2: ~4.6 KiB per episode marked).

## 4. Stamps

v2 §4, with these additions:

- A stamp is **timeless** when `t == 0`, whatever `c` and `d` are. Shipped v2 clients write timeless watched bits
  as `[0, 0, ""]`, `[0, 0, "<device>"]` or `[0, 0, "local"]`; all are the same thing.
- **Row merges use stamps as stored**, so every client keeps the same winner. **Deriving** state (§5) and
  **deciding** delivery (§6) read that winner and treat it as timeless if its stamp is more than a day ahead
  (v2 §4). Neither ever rewrites a stored stamp.
- A client issuing a stamp after it has seen a reset stamp `R` (a `seasonReset`, or v2's `episodesReset`) issues one
  with `t ≥ R.t + 1`, so a change made after a reset always shows past it. Reset comparisons use `t` only.
- **Ties.** Where two versions carry equal stamps and differ, the one whose RFC 8785 (JCS) canonical JSON is
  byte-greater wins, so every client resolves the tie the same way.

## 5. Deriving state (v3 library)

### Episodes

For episode `(s, e)` of series `T`, with register `R` from its watch row:

- **Hidden**: `R.progress` whose `t` is not greater than the series' `episodesReset.t` or the season's
  `seasonReset.t` counts as none.
- **Watched**: `R.progress` (not hidden) with `value ≥ 0.95`. With no unhidden progress: watched if `imported` and
  no reset covers it (a reset hides imported state too; an import after it writes again, §7).
- **Resume position**: `R.progress` (not hidden) that is a viewing in progress, under v2's rules (`RESUME_FLOOR`,
  `continue_entry`).
- **Current viewing** = `R.progress.viewing`, hidden or not, or 0 when there is none.
- **Plays**: `R.plays` with key > `cleared`, and with watchedAt later than any reset's `t` covering the episode.
  The **first play** is the one with the least key. **Watched-at**, the time trackers receive, is the play of the
  current viewing, or else the greatest play, or null.

### Films

A film's status, resume and reaction are v2's (`rec`). Its plays are the `"0"` register's plays with key >
`cleared`. Status `none` or `watchlist` shows no play (a rewatch in progress keeps them). Watched-at is the play of
`rec.resume.viewing`, or else the greatest play.

## 6. Tracker delivery

Delivery is driven by state and settled with **receipts**, one engine only.

- **Delivering device.** One device per tracker account delivers: the holder of a **lease**, a `set:deliver`
  setting `<provider>:<account>` = `{"string": "<device id>@<expiry ms>"}` written by compare-and-set with a 10-minute
  expiry and renewed while delivering. Another device delivers for that account only after the lease expires.
- A **target** is an episode, a film's `rec.status`, or a title's `rec` `reaction` or `deleted`.
- Its **deliverable value**:
  - Episode — class and viewing: `watched` (watched, §5), `unwatched` (real `value == 0` progress, **or** progress
    hidden by a reset whose stamp is later than the receipt's; its time is the reset's `t`), or `none` (in
    progress, or nothing). With the watched-at for `watched`.
  - Film — `rec.status`, with the viewing and watched-at.
  - `reaction`, `deleted` — their values.
- A **receipt** records, per account and target, the deliverable value a command was **built from** when it settled,
  with a stamp. A target is **pending** when its current value differs from its receipt: class, or viewing, or
  field value. Values, not stamps: v2's merge lets an older-stamped version win on viewing.
- Each pending target yields v2's command for its value — watched (with watched-at), unwatched, list add or
  remove, rating — with `at` = the value's stamp `t` (a reset's `t` for a reset-hidden episode). den-core `decide`
  sends, acknowledges, supersedes or holds it against the tracker's snapshot exactly as for a v1 journalled event,
  with every v2 hold reason: remote order unknown or newer (including a remote watch with no time, or the same
  time), incomplete coverage, account mismatch, independent state (a film/show removal that would remove a list
  entry or rating). A **rewatch** — `watched` in a viewing greater than the receipt's — is sent as a new play even
  when the remote already holds a watch.
- **Settling** — sent and acknowledged, already present, superseded, or `not_found` — first re-reads the log head;
  if the target's value still equals the value the command was built from, it writes that value as the receipt,
  otherwise the target stays pending. A coordinate the tracker cannot hold (`not_found`, or one that does not map
  back) settles as final. A held command writes nothing and is decided again later.
- **No receipt**: each account has a `since` stamp, set when it is connected. A target with no receipt whose value
  changed after `since` is pending. One unchanged since is caught up additively only — watched, a list add, a
  rating — as v2's catch-up; any other value settles silently. So connecting an account never removes anything
  there, and a change after connecting is always delivered.
- **Sent rows** hold receipts: name `snt:<provider>:<account>:<shard>`, where `<account>` = first 16 hex of
  HMAC-SHA256(`macKey`, `<provider>:<tracker account id>`) and `<shard>` = first 4 hex of HMAC-SHA256(`macKey`, the
  target row's name). A shard row is written only when it holds a receipt.

  ```json
  {"kind": "snt", "schema": 3, "provider": "simkl", "account": "0f3a…", "shard": "7c1e",
   "since": [1789000000000, 0, "a1b2c3d4e5f60718"],
   "receipts": {
     "wat:tv:1399:1:0#1": {"v": ["w", 0], "at": [..]},
     "wat:tv:1399:1:0#2": {"v": ["u", 2], "at": [..]},
     "rec:movie:550#status": {"v": ["watched", 0], "at": [..]}}}
  ```

  Receipts are keyed per target (`<row name>#<entry or field>`) and merge per key by their own stamp (ties §4), so
  two devices settling different targets never lose each other's. `since`: the later stamp.
- **Remote → Den**: v2's tracker pull, written as imports (§7), only after pulling the log to its head.
- **Size**: ~40 bytes charged per episode per account, bounded by the library, never by changes. With 65 536 shards,
  a library at den-edge's 50 000-row limit puts a handful of target rows in any shard.

## 7. Writing (v3 library)

Every write is a set-to-value; replaying one changes nothing.

- **Playback** updates `progress` in the current viewing as v2 updates an `ep` row. When `value` first reaches 0.95
  in a viewing, the client adds the play `viewing → stamp.t` if that viewing has none.
- **Mark watched**: `progress` = value 1 in the current viewing with a fresh stamp; the play for that viewing if
  none.
- **Un-watch**: `cleared` = the current viewing (the one that was watched); then `progress` = value 0 in current
  viewing + 1 with a fresh stamp.
- **Rewatch**: progress in current viewing + 1, as v2; earlier plays stay.
- **Season reset**: block 0's `seasonReset` = a fresh stamp. **Series reset**: `rec.episodesReset` (v2).
- **Import** (a tracker's watched list, an import file) of episode `(s, e)`, after pulling the log to its head:
  only if the episode has no unhidden `progress` and is not in progress. It writes `imported: true` and one play per
  known viewing time, keyed `watchedAt − 2⁵³`. It writes no `progress`, so it can never outrank or erase a person's
  progress, even when a concurrent write merges with it. A film import writes `rec.status` watched with the stamp
  `[0, 1, ""]` (timeless, so any real status wins) only if the film has no real status stamp, and its plays.
- **Films**: status, resume and reaction stay in `rec` (v2). Finishing or marking a film watched also writes the
  play for `resume.viewing`. Un-watching sets `cleared` to `resume.viewing` **before** v2 bumps it.

## 8. v2's reference reading

The v3 form (§9) is built from what v2 web derives, which is den-core's reading. For episode `(s, e)`:

- **Claims**: the `ep:tv:T:s:e` row and the `after` of every v1 `tracker-event` whose `after` is that row
  (Appendix A), each read with v2's future-stamp demotion first (v2 §4).
- **Merged progress** = den-core `merge` over **all** claims, real and timeless (v2 §5). **State** =
  `episode_mark(merged, held: null, authoritative: true)` with the title's `episodesReset` applied by `t`. This is
  what a v2 web client shows.
- Known shipped differences, which the fold does not follow: a v2 TV keeps a held real mark where the web flags a
  timeless watch, and ignores a timeless value-0 row the web clears; v2 web's detail page compares whole stamps
  where its library compares `t`.
- **Plays** folded from v2: for every viewing with a real claim of `value ≥ 0.95`, the least such `t`; the effective
  `cleared` is the greatest `viewing − 1` over real claims of `value == 0`.
- **Titles**: each `rec` row merged with every title event's `after` (v2 merge), as a v2 TV repairs an interrupted
  projection.

## 9. The switch

A library becomes v3 in one step: den-edge replaces its log with the **v3 form** and raises its **wire minimum**,
atomically.

**Who and when.** Every device in `set:devices` reports its format as the setting `<d>.format` = `{"int": 3}` (a
settings value in a `set` row, merged per setting as v2 §5). The switch is **offered** once every device whose
`<d>.seen` setting is stamped (`at.t`) within 180 days reports format 3, and no device has entries but no `seen`
(unknown counts as not ready). It is **performed** only by a device that delivers for every connected tracker
account (the TV), after draining its outbox so it holds nothing but held commands. Any other client only shows
that it is offered.

**The v3 form** is, from §8's reading of every row:
- a `wat` row per block with a claim: the merged progress (if real) as `progress`, a timeless-watched state as
  `imported`, the folded plays and `cleared`;
- every `rec` row merged with its title events;
- every other row it does not rewrite — `set` rows other than `set:tracker-event:*`, and rows of any kind it does
  not know — **staged with `k` and `v` unchanged**;
- receipts, seeded from the performing device's own per-event settlement: for each target, its current deliverable
  value if no journal command for it is unsettled or held, otherwise the value **before** its earliest unsettled or
  held event; `since` = the switch's stamp.

It holds no `ep` or `set:tracker-event:*` row. §12 pins that it derives the same state as §8 for every coordinate
and title.

**den-edge** stores, inside each library's own store (backed up and restored with the rows), a **wire minimum**
(2 by default) and a **per-library generation**, and adds:

1. `POST /lib/{id}/rewrite` → `{"rewrite": <r>, "base": <head>}`: opens a staging area, one per library at a time,
   kept on disk (it survives a restart). From then on the library is **fenced**: any write without this rewrite id
   gets `409 {"error": "rewrite_in_progress"}` with `Retry-After`, until commit, abort, or 5 minutes without a
   staging request.
2. `POST /lib/{id}/rewrite/{r}/rows {writes: [{k, v}]}`: stages rows in batches under the usual count and byte
   limits, counted against a separate allowance equal to the library's cap, so a full library can still switch.
3. `POST /lib/{id}/rewrite/{r}/commit {base, wireMin}`: in one transaction, only if the head is still `base`,
   replaces the rows with the staged ones, **keeps the library's token and member hashes**, raises the wire minimum
   to `wireMin` (never lowers it) and increments the library's generation. Sequence numbers **continue above the
   old head**, so any stale `base` conflicts. Otherwise `409 {"head"}` and nothing changes.
4. `DELETE /lib/{id}/rewrite/{r}`: aborts and lifts the fence.

Every `/lib/{id}` response carries `x-den-generation`; a batch carrying an older one gets
`409 {"error": "generation_changed"}`.

The same rewrite, with an unchanged `wireMin`, compacts a v3 library later.

## 10. Clients around the switch

- **Headers.** Every v3-capable request to `/lib/{id}/…` carries `x-den-wire: 3` (also before the switch). Every
  `/lib` response carries `x-den-wire-min: <n>` and `x-den-generation`. den-edge's CORS allows and exposes them.
- **Refusal.** With a wire minimum of 3, den-edge answers every `/lib/{id}` request without `x-den-wire ≥ 3` —
  batches, member writes and `DELETE /lib/{id}` included — with `426 {"error": "upgrade_required", "min": 3}` (with
  CORS headers). The single exemption: `GET /lib/{id}/changes?since=0&limit=1` is answered normally (the lowest
  row, `head`, `generation`), because a v2 TV's routes home check opens that row to keep its LAN routes.
- **New libraries.** A library created by a batch naming another in `x-den-library-member` (a key reset, a join)
  takes that library's wire minimum. A client never sends a higher one.
- **What shipped v2 builds do on 426**, stated so nobody expects more: a v2 TV logs it and stops syncing, keeping
  its local state and outbox — and keeps delivering from that outbox, which may re-add a watch v3 removed. The v3
  deliverer therefore re-verifies settled targets against each tracker snapshot during catch-up. A v2 web page
  keeps showing its kept copy and keeps its unsent work in the browser. Neither says "update"; a v3 client lists
  every device below format 3 as needing one. A cached v2 web shell is replaced on the next visit, when its service
  worker fetches the current build.
- **A minimum never falls back.** A v3 client remembers the highest `x-den-wire-min` it has seen per library id.
  If a response shows less (a store restored from an older backup), it compares minimums first and, before writing
  anything, performs the switch again on what the restored log holds.
- **After a switch** (a new generation), every client forgets its head and bases, reads from 0, and writes back
  what it holds that the new log lacks — **in v3 form only**: it first converts any `ep` rows, v1 events and kept
  unsent work it holds (including a v2 build's kept journal, once that device updates) through §8 into registers,
  receipts untouched, and never uploads an `ep` or `tracker-event` row. Its converted changes are pending targets
  (§6) and are delivered.

## 11. den-core

The rules live in den-sync so both clients share them:

- `name`, `merge` and `newest` accept `wat` and `snt` rows (§3, §6, §13).
- `episode_state`, `film_state`: row + resets → watched, resume, current viewing, plays, watched-at (§5).
- `register_write`: action + current register → the register to write (§7). `import_write`: register + import item
  → the register, or nothing (§7).
- `pending_targets`: state + receipts → commands for `decide`; `settle`: outcome + built-from value + current value
  → the receipt, or nothing (§6).
- `v2_reading`: v2 rows → §8's state; `v3_form`: every row + settlement state → the switch's rows (§9);
  `write_back`: held state + new log → the v3 rows to write after a generation change (§10).
- `switch_ready`: devices row + outbox summary → offered, performable, or neither (§9).

## 12. Vectors the implementation MUST pin

- Names and keys: `(1, 31)` → block 0, `(1, 32)` → block 1, season 0, a film `wat:movie:550:0:0`; keys `"01"`,
  `"-1"`, `"32"` in block 0 and `"100000"` ignored when deriving and kept through a merge; `d` other than `""`,
  `"local"` or 16 hex refused by writers.
- Stamps: `[0, 0, "a1b2c3d4e5f60718"]` and `[0, 0, "local"]` are timeless; a future winner derives as timeless but
  merges as stored; equal stamps break by JCS bytes.
- Merge laws for `wat` and `snt` over random triples, including plays beyond 8, the same imported play from two
  sources, duplicate viewings with different times, `cleared` changing in one part, and receipts settled on two
  devices.
- Deriving: resets hiding progress, resume, imported state and plays; a stamp issued after seeing a reset shows
  past it; current viewing under a hidden progress; first play and watched-at with imported plays.
- Films: status `inProgress` keeps plays, `none` hides them; un-watch clears the viewing before the bump.
- Imports: no claim → written; real progress or in progress → nothing; never a `progress`; film import stamp
  `[0, 1, ""]` loses to any real status.
- Delivery: pending by value (an older-stamped winner on viewing); a reset delivering an un-watch at the reset's
  `t`; each `decide` outcome's receipt; a target changed in flight stays pending; no receipt → additive only unless
  changed after `since`; a rewatch sent though the remote has a watch; `not_found` final; the lease.
- Switch: `v3_form` derives §8's state for every coordinate and title (including timeless-over-real in viewing 0,
  and title events repairing a `rec`); unknown rows staged unchanged; seeded receipts leave a held un-watch pending
  and a settled one settled; fence refuses outside writes; commit refused on a moved head; generation and sequence
  continue; token and members kept; `426` on batch, member and delete, and the home-check exemption; a created
  library inherits the minimum; `wireMin` never lowered; a restored lower minimum switched again; `write_back`
  converts a kept v1 journal and emits no `ep` or event row.

## 13. Versions and den-edge

- `wat` and `snt` rows are `schema: 3`. `rec` and `set` rows stay `schema: 2`. v3 adds no field to a `rec` row: v2
  carries a newer version's unknown fields wholesale (v2 §5), which would let an old write roll a new field back.
  `<d>.format` and `set:deliver` are settings values, which v2 clients merge per setting and keep.
- v2 clients never see `wat` or `snt` rows: before the switch none exist, after it they are refused.
- den-edge adds, and interprets no row: the per-library wire minimum and generation, `x-den-wire`,
  `x-den-wire-min` and `x-den-generation` with CORS, the 426 and its one exemption, and the fenced rewrite (§9, §10).

## Appendix A — v1 tracker events (as shipped)

A v1 event is a settings row `set:tracker-event:<id>`, `schema` 2, whose `values.event` is
`{"value": {"string": <event JSON>}, "at": <event.at>}`. The event is
`{"schema": 1, "id", "at", "before": <row>, "after": <row>, "changes": {<field>: {"before", "after"}}}`, made by
den-core `capture` from the row before and after an action; `before` and `after` name the same `rec` or `ep` row.
A reader ignores an event that fails any of: `schema == 1`, the row name is `tracker-event:` + `id`, `values.event.at
== at`, and `before` and `after` name the same non-settings row.

v2 clients write one event per explicit action a person takes, and none otherwise:

| Action | Event row(s) |
|---|---|
| Mark / un-mark an episode | one per episode (`ep`, field `progress`) |
| Mark / un-mark a season or series | one per episode, plus one for the series `rec` (`status`) |
| Watchlist add / remove, film watched / un-watched, reaction, delete | one per changed `rec` field |
| Playback, progress, finishing by playing, dismissing, imports | none |
