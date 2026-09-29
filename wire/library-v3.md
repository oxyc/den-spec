# Library wire format, v3

v3 changes how **episode progress**, **play history** and **tracker delivery** are stored. Everything else —
keys, sealing, title and settings rows, the log protocol — is [library v2](library-v2.md) unchanged, and a v3
client follows every rule there unless a section below replaces it. Vectors: `../vectors/library-v3.json`
(generated with the first implementation; §12 lists what it MUST pin). Every rule below is implemented once, in
den-core, as ops both clients call (§11); a client never re-implements a merge, fold, gate or delivery decision.

Why: in v2 each episode is its own row (~1 KiB charged), and each change a person makes is also kept forever as a
`tracker-event` row carrying whole rows (~3.6 KiB per episode, ~6 KiB per film). A heavy viewer fills a library in
a few thousand episodes (oxyc/den#163). v3 stores 32 episodes per row, keeps no per-change history, records
rewatches, lets any client deliver to trackers, and does it without any client losing or contradicting state
another wrote.

Words: *MUST* is a rule a client breaks at the cost of other clients; *should* is advice.

## 1. How a library moves to v3

There is **no mixed period**: no library is ever written in both formats at once.

1. **Ready builds.** v3-capable builds read and write **v2 only** on a v2 library, and report `format` 3 for
   their device (§9). Nothing about the library changes.
2. **The switch.** Once every device is ready, the device that holds the v2 delivery state rewrites the library into
   its v3 form and raises its wire minimum in one fenced, atomic step (§9).
3. **v3.** From then on every client writes v3 only, and den-edge refuses any request without `x-den-wire ≥ 3`
   (§10), so a v2 build can neither write nor delete.

## 2. Guarantees

1. **Convergence.** Clients that have read the same rows derive the same state for every episode and title, in any
   arrival order.
2. **No lost update.** Changes to different episodes or fields never overwrite each other; changes to the same one
   resolve by v2's rule for that field (v2 §5).
3. **No lost state at the switch.** The v3 form derives, for every coordinate and title, the state v2's reference
   reading (§8) derives from the log at the rewrite's `base`, and replaces it atomically while no other write can
   land (§9). A write refused around the switch is kept by the client that made it and sent afterwards (§10).
4. **Imports never override a person.** State learned from a tracker or an import file loses to any change a person
   made on any device, in any order.
5. **Bounded growth.** Rows grow with titles, one per 32 episodes, and one receipt row per watch row and connected
   account — never with changes. No row can exceed den-edge's value cap (§3, §6).
6. **Idempotence.** Replaying any write, any number of times, in any order, changes nothing further.
7. **Nothing delivered twice, nothing dropped.** A tracker change settled before the switch or a restore is not sent
   again after it; one unsettled or held stays pending; one device at a time delivers for an account.

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
  `floor(e / 32) == block`. A reader derives nothing from any other key but keeps it through merges and rewrites,
  so a key a later version allows is never erased; such keys count against the row's 1 KiB of unknowns.
- A **register** (no other fields; a future field arrives with a new `schema`):
  - `progress` (series only): a v2 progress value `{value, at, viewing, seconds?}` (v2 §3). It holds **real**
    progress only — a writer MUST NOT put a timeless stamp here (§4). Absent when the episode has none.
  - `imported`: true when an import said the episode is watched and gave no stronger claim (§7). Merge: OR.
  - `plays`: key (decimal string) → `watchedAt` (Unix ms). Non-negative keys are Den viewings. Negative keys are
    imported plays, keyed `watchedAt − 2⁵³` with `watchedAt` rounded down to whole seconds and
    `1000 ≤ watchedAt ≤ 2⁵³ − 1`, so they order oldest first, sit below every Den viewing, and one play reported by
    two sources is one key. **Merge**: the union of keys; a key both hold keeps the lesser `watchedAt`; the result
    keeps only the least key and the 7 greatest keys. Writers apply the same selection. (A join: the per-key rule is
    `min`, and the least and greatest 7 keys of a union are those of the parts' selections.)
  - `cleared`: the highest viewing an explicit un-watch cleared, or null. Merge: the maximum; null is lowest.
- **`seasonReset`**: a stamp or null. Written only to block 0; a reader ignores it on any other block. Merge: the
  later stamp; null is older than any.
- **Numbers**: `value` in [0, 1]; `seconds` in [0, 10⁷]; every stamp within den-core's bounds (|t|, c ≤ 2⁵³ − 1).
  A stamp's `d` MUST be `""`, `"local"` or 16 lowercase hex characters; a writer never writes another.
- `schema` is 3; merge takes the max. Row-level unknown fields and invalid keys together are at most 1 KiB of JSON
  and come from the version whose **newest** stamp — the greatest over its registers' `progress.at` and its
  `seasonReset` — is later (ties §4).

**Size.** A worst-case register — maximal numbers, a 16-hex `d`, 8 plays with maximal keys and times — is under
600 bytes of JSON, so a full block with 1 KiB of unknowns is under 21 KiB plaintext and about 29 KiB sealed and
base64-encoded, below den-edge's 32 KiB value cap (about 24 KiB of plaintext). A typical register is ~120 bytes and
a typical full block ~5 KiB sealed. den-edge charges `2(k + v) + fragment + 192` per row, so a typical full block
costs ~15 KiB, ~0.5 KiB per episode (v2: ~4.6 KiB per episode marked).

## 4. Stamps

v2 §4, with these additions:

- A stamp is **timeless** when `t == 0`, whatever `c` and `d` are. Shipped v2 clients write timeless watched bits
  as `[0, 0, ""]`, `[0, 0, "<device>"]` or `[0, 0, "local"]`; all are the same thing.
- **Row merges use stamps as stored**, so every client keeps the same winner. **Deriving** state (§5) and
  **deciding** delivery (§6) read that winner and treat it as timeless if its stamp is more than a day ahead of the
  reader's clock (v2 §4). Nothing ever rewrites a stored stamp — the switch included (§8).
- A client issuing a stamp after it has seen a reset stamp `R` (a `seasonReset`, or v2's `episodesReset`) that is
  not more than a day ahead issues one with `t ≥ R.t + 1`, so a change made after a reset always shows past it.
  Reset comparisons use `t` only.
- **Ties.** Where two versions carry equal stamps and differ, the one whose RFC 8785 (JCS) canonical JSON is
  byte-greater wins, so every client resolves the tie the same way.

## 5. Deriving state (v3 library)

### Episodes

For episode `(s, e)` of series `T`, with register `R` from its watch row:

- **Hidden**: `R.progress` whose `t` is not greater than the series' `episodesReset.t` or the season's
  `seasonReset.t` counts as none; so does `imported` when any such reset exists.
- **Watched**: `R.progress` (not hidden) with `value ≥ 0.95`; with no unhidden progress, `imported` (not hidden).
- **Resume position**: `R.progress` (not hidden) that is a viewing in progress, under v2's rules
  (`RESUME_FLOOR`, `continue_entry`).
- **Current viewing** = `R.progress.viewing`, hidden or not, or 0 when there is none.
- **Visible plays**: `R.plays` with key > `cleared`, and with watchedAt later than any reset's `t` covering the
  episode. The **first play** is the visible play with the least watchedAt. **Watched-at**, the time trackers
  receive, is the visible play of the current viewing, or else the greatest visible play, or null.

### Films

A film's status, resume and reaction are v2's (`rec`). Its plays are the `"0"` register's plays with key >
`cleared`. Status `none` or `watchlist` shows no play (a rewatch in progress keeps them). Watched-at is the play of
`rec.resume.viewing`, or else the greatest play, or else `rec.status.at.t` while the play is still in flight.

## 6. Tracker delivery

Delivery is driven by state and settled with **receipts**. Any client that holds an account's credentials may
deliver for it; one at a time.

- **Credentials.** Each connected tracker account is a setting in `set:trackers`: `<provider>:<account>` =
  `{"string": <JSON of the token and account id>}` (sealed like every row; den-edge never sees it). Removing the
  setting disconnects the account for every device.
- **Delivery row.** `set:deliver:<provider>:<account>` holds that account's `since` and `lease` settings. `<account>`
  = first 16 hex of HMAC-SHA256(`macKey`, `<provider>:<tracker account id>`).
  - `since`: a stamp, written when the account is connected (the later stamp wins).
  - `lease`: `{"string": "<device id>"}`. A device takes or renews it **only** by a batch whose `base` is this row's
    current seq; on conflict it re-reads and re-evaluates, and **never merges** a lease write. Another device treats
    a lease as expired only after its own monotonic clock has seen the row's seq unchanged for 10 minutes. The
    holder renews at least every 4 minutes, stops sending once 2 minutes have passed since its last successful
    renewal, and settles or abandons in-flight sends before it lets go.
- A **target** is an episode; a title's `rec.status` (for a series, only its watchlist membership); or a title's
  `rec` `reaction` or `deleted`.
- Its **deliverable value**, each with the stamp it came from:
  - Episode — class, viewing and watched-at: `watched` (§5); `unwatched` — real `value == 0` progress, or a watched
    state (progress ≥ 0.95 or `imported`) hidden by a reset whose `t` is later than the receipt's `t`, stamped with
    that reset; or `none` (in progress, or nothing).
  - `rec.status`, `reaction`, `deleted` — their values; a film's status with its viewing and watched-at.
- A **receipt** records, per account and target, the value a command was **built from** when it settled, and **that
  value's stamp** (a reset's for a reset-derived `unwatched`) — never the time of settling. A target is **pending**
  when its current value differs from its receipt (class, viewing, or field value). Values, not stamps: v2's merge
  lets an older-stamped version win on viewing.
- Each pending target yields v2's command, built by den-core `commands` with `before` = the receipt's value and
  `after` = the current value, `at` = the current value's stamp `t`. A pending target whose values yield no v2
  command settles silently with its current value.
- **Rewatch vs re-mark.** `watched` in a viewing greater than the receipt's is a **rewatch** only if `cleared` is
  below the receipt's viewing (no un-watch since); it is sent as a new play even when the remote holds a watch.
  Otherwise it is an un-watch and a re-mark: `unwatched` at the un-watch's `t`, then `watched` at watched-at, each
  decided on its own.
- den-core `decide` sends, acknowledges, supersedes or holds each command against the tracker's snapshot exactly as
  for a v1 journalled event, with every v2 hold reason — remote order unknown or newer (a remote watch with no time,
  or the same time), incomplete coverage, account mismatch, independent state (a removal that would drop a list
  entry or rating), and the removal burst guard (more than 20 list removals in 120 s holds the rest) — plus the
  `rewatch` input above.
- **Settling** — sent and acknowledged, already present, superseded, or `not_found` — first re-reads the log head;
  if the target's value still equals the value the command was built from, it writes that value and stamp as the
  receipt; otherwise the target stays pending. A coordinate the tracker cannot hold (`not_found`, or one that does
  not map back) settles as final. A held command writes nothing and is decided again later.
- **No receipt**: a target with no receipt whose value's stamp is later than the account's `since` is pending. One
  from before `since` is caught up additively only — watched, a list add, a rating — as v2's catch-up; any other
  value settles silently. Connecting an account never removes anything there, and a change after connecting is
  always delivered. Catch-up never re-verifies settled targets: an edit made on the tracker's own site stands.
- **Receipt rows**, `schema: 3`:
  - Episodes: `snt:<provider>:<account>:<watch row name>`, one per watch row with a receipt,
    `{"kind": "snt", …, "target": "<watch row name>", "entries": {"2": ["w", 1, 1789000000000, <stamp>]}}` —
    class (`w`/`u`/`n`), viewing, watched-at, value stamp.
  - Titles: `snt:<provider>:<account>:t<shard>`, `<shard>` = first 2 hex of HMAC-SHA256(`macKey`, the `rec` row's
    name), `{"entries": {"rec:movie:550#status": ["watched", 0, <stamp>], …}}`.
  - Entries merge per key by their stamp (ties §4), so devices settling different targets never lose each other's.
- **Remote → Den**: v2's tracker pull, written as imports (§7), only after pulling the log to its head.
- **Size**: a full block's receipts are ~1.4 KiB of JSON, ~4 KiB charged per account (~0.13 KiB per episode); a
  title receipt ~80 bytes. Receipt rows add one row per watch row per account, plus 256 per account at most.

## 7. Writing (v3 library)

Every write is a set-to-value; replaying one changes nothing. `register_write` (§11) decides every case below.

- **Playback** updates `progress` in the current viewing as v2 updates an `ep` row; starting a finished episode
  again starts viewing + 1 (as the shipped TV does). When `value` first reaches 0.95 in a viewing, the client adds
  the play `viewing → stamp.t` if that viewing has none.
- **Mark watched**: `progress` = value 1 with a fresh stamp, in the current viewing — or current viewing + 1 when
  the current viewing already has a play that is hidden by a reset or `cleared` — and the play for the viewing it
  wrote.
- **Un-watch**: `cleared` = the current viewing (the one that was watched); then `progress` = value 0 in current
  viewing + 1 with a fresh stamp.
- **Rewatch**: progress in current viewing + 1, as v2; earlier plays stay.
- **Season reset**: block 0's `seasonReset` = a fresh stamp. **Series reset**: `rec.episodesReset` (v2).
- **Import** (a tracker's watched list, an import file) of episode `(s, e)`, after pulling the log to its head:
  only if the episode has no unhidden `progress`, is not in progress, and has no reset newer than the import's
  newest play. It writes `imported: true` and its plays (§3). It writes no `progress`, so it never outranks or
  erases a person's progress, even when a concurrent write merges with it; and it never lands over a Den un-watch or
  reset, even with a newer remote watch time. A film import writes `rec.status` watched with the stamp `[0, 1, ""]`
  (timeless, so any real status wins) only if the film has no real status stamp, and its plays.
- **Films**: status, resume and reaction stay in `rec` (v2). Finishing or marking a film watched also writes the
  play for `resume.viewing`. Un-watching sets `cleared` to `resume.viewing` **before** v2 bumps it.

## 8. v2's reference reading

The v3 form (§9) folds v2 rows by what v2 web derives, which is den-core's reading. For episode `(s, e)`:

- **Claims**: the `ep:tv:T:s:e` row and the `after` of every v1 `tracker-event` whose `after` is that row
  (Appendix A), as stored.
- **Merged progress** = den-core `merge` over **all** claims, real and timeless (v2 §5). **State** =
  `episode_mark(merged, held: null, authoritative: true)` with the title's `episodesReset` applied by `t`.
- **Fold**: a real merged progress becomes the register's `progress`, stamp unchanged; a timeless watched state
  (`t == 0` and `value ≥ 0.95`) becomes `imported: true`. The fold never demotes a stamp: future stamps are demoted
  only when deriving (§4), as v2 does, so a device's clock never becomes part of the stored library.
- **Plays**: for every viewing with a real claim of `value ≥ 0.95`, the least such `t`. **Cleared**: the greatest
  `viewing − 1` over claims of `value == 0`, real or timeless.
- **Titles**: each `rec` row merged with every title event's `after` (v2 merge), as a v2 TV repairs an interrupted
  projection.
- Known shipped differences the fold does not follow: a v2 TV keeps a held real mark where the web flags a
  timeless watch, and ignores a timeless value-0 row the web clears; v2 web's detail page compares whole stamps where
  its library compares `t`.

## 9. The switch

A library becomes v3 in one step: den-edge replaces its log with the **v3 form** and raises its **wire minimum**,
atomically.

**Who and when.** Every device reports its format as the setting `<d>.format` = `{"int": 3}` in `set:devices`
(merged per setting, v2 §5). The switch is **offered** once every device whose `<d>.seen` setting is stamped
(`at.t`) within 180 days reports format 3, and no device has entries but no `seen` (unknown counts as not ready). It
is **performed** only by the device holding the v2 delivery outbox for every connected account (the TV), after
draining it so it holds nothing but held commands. Any other client only shows that it is offered.

**Sequence.**
1. Open the rewrite (below) and take `base`. From here until commit or abort the performing device **sends no
   tracker command**.
2. Read the log through `base`, and derive the v3 form from **every row through `base`**.
3. Stage it, commit with that `base`. On `409`, abort, and start over from 1.

**The v3 form** is, from §8's reading:
- a `wat` row per block with a claim (§8 fold);
- every `rec` row merged with its title events;
- every row it does not rewrite — `set` rows other than `set:tracker-event:*`, and rows of any kind it does not
  know — **staged with `k` and `v` unchanged**;
- receipts (§6), seeded per account from the performing device's settlement state: a target is **unsettled** if any
  `tracker-event` in the log through `base` for it has an id this device has not settled for that account, or if a
  held or unsent baseline command exists for it. An unsettled target is seeded with its value **before** its
  earliest unsettled event (or no receipt, when that is the first); any other target with its current value. Receipts
  the device already holds from an earlier switch are merged in (§6). `set:deliver` rows get `since` = the switch's
  stamp.

It holds no `ep` or `set:tracker-event:*` row. §12 pins that it derives §8's state for every coordinate and title.

**den-edge** stores, inside each library's own store (backed up and restored with the rows), a **wire minimum**
(2 by default) and a **per-library generation**, and adds:

1. `POST /lib/{id}/rewrite` → `{"rewrite": <r>, "base": <head>}`: opens a staging area, kept on disk (it survives a
   restart). A second open while one exists gets `409 rewrite_in_progress`. From then on the library is **fenced**:
   every write without this rewrite id — batches, `PUT member`, `DELETE` — gets `409 {"error":
   "rewrite_in_progress"}` with `Retry-After`, until commit, abort, or 5 minutes without a staging request.
2. `POST /lib/{id}/rewrite/{r}/rows {writes: [{k, v}]}`: stages rows under the usual count limit and the 2 MiB body
   limit (the client chunks by bytes), counted against a separate allowance equal to the library's cap, so a full
   library can still switch.
3. `POST /lib/{id}/rewrite/{r}/commit {base, wireMin}`: in one transaction, only if the head is still `base`,
   replaces the rows with the staged ones, keeps the library's token and member hashes, raises the wire minimum to
   `wireMin` (never lowers it; never above the request's `x-den-wire`) and increments the library's generation.
   Sequence numbers continue above the old head, so any stale `base` conflicts. Otherwise `409 {"head"}` and nothing
   changes.
4. `DELETE /lib/{id}/rewrite/{r}`: aborts and lifts the fence.

The same rewrite, with an unchanged `wireMin`, compacts a v3 library later; its v3 form is the library's rows with
any stray `ep` or `tracker-event` row folded through §8.

## 10. Clients around the switch

- **Headers.** Every request from a v3-capable client to `/lib/{id}/…` carries `x-den-wire: 3` and, once it knows
  one, `x-den-generation` (also before the switch). Every `/lib` response carries `x-den-wire-min`,
  `x-den-generation` and `Cache-Control: no-store`. den-edge's CORS allows and exposes them.
- **Generations.** den-edge answers any write — batch, member, DELETE, rewrite — whose `x-den-generation` is
  missing (from a client that sends `x-den-wire`) or differs from the library's with `409 {"error":
  "generation_changed"}`. Generations compare for equality; a change in the body's store-wide `generation` or in
  `x-den-generation` both mean: re-read and write back (below).
- **Refusal.** With a wire minimum of 3, den-edge answers every `/lib/{id}` request without `x-den-wire ≥ 3` —
  batches, member writes and `DELETE /lib/{id}` included — with `426 {"error": "upgrade_required", "min": 3}`. The
  single exemption: `GET /lib/{id}/changes?since=0&limit=1` is answered normally (the lowest row, `head`,
  `generation`), because a v2 TV's routes home check opens that row to keep its LAN routes.
- **Refused writes are kept.** A v3-capable client keeps every write refused with `409 rewrite_in_progress`,
  `409 generation_changed` or `426` durably — playback progress, settings and dismissals as well as actions — and
  sends it after `Retry-After`, through write-back when the generation changed.
- **New libraries.** A library's first batch carries `x-den-wire-min`; den-edge sets the minimum to the greatest of
  that and the minimum of the library named in `x-den-library-member`, never above the request's own
  `x-den-wire`. A key reset that gets `generation_changed` on its `DELETE` of the old library re-copies first.
- **What shipped v2 builds do on 426**, stated so nobody expects more: a v2 TV logs it and stops syncing, keeping its
  local state and outbox, and may keep delivering from that outbox. A v2 web page keeps showing its kept copy and
  keeps unsent **actions** (watch marks, list, rating); playback progress and settings it saves after the switch are
  lost. Neither says "update"; a v3 client lists every device below format 3 as needing one. A cached v2 web shell is
  replaced on the next visit, when its service worker fetches the current build.
- **A minimum never falls back.** A v3 client remembers the highest `x-den-wire-min` it has seen per library id. If
  a response shows less (a store restored from an older backup), the performing device switches again on what the
  restored log holds before writing anything else; any other client writes nothing and keeps its work until the
  minimum is 3 again.
- **After a switch or a restore** (a changed generation), every client forgets its head and bases, reads from 0, and
  writes back what it holds that the new log lacks — **in v3 form only**, including **every receipt it holds**
  (merged per key, §6). It converts any `ep` rows, v1 events and kept unsent work it holds (including a v2 build's
  kept journal, once that device updates) through §8 into registers first. Its converted changes are pending targets
  (§6).
- **No v2 rows in a v3 library.** A v3 client uploads no `ep` or `tracker-event` row on any path — write-back,
  imports, linking a local library, recovery. A v3 reader that finds one folds it through §8 into registers and
  writes those back before any compaction drops it.

## 11. den-core

The rules live in den-sync so both clients share them:

- `name`, `merge` and `newest` accept `wat` and `snt` rows (§3, §6, §13); `later` and `merge` break ties by JCS.
- `episode_state`, `film_state`: row + resets → watched, resume, current viewing, plays, first play, watched-at (§5).
- `register_write`: action + current register → the register to write (§7). `import_write`: register + import item
  → the register, or nothing (§7).
- `pending_targets`: state + receipts + `since` → commands (§6), via `commands` with the receipt as `before`;
  `decide` gains the `rewatch` input and the removal burst guard; `settle`: outcome + built-from value + current
  value → the receipt, or nothing; `lease`: row + observation → take, renew, wait or stop.
- `v2_reading`: v2 rows → §8's state; `v3_form`: every row through `base` + settlement state → the switch's rows
  (§9); `write_back`: held state + new log → the v3 rows to write after a generation change (§10).
- `switch_ready`: devices row + outbox summary → offered, performable, or neither (§9).

## 12. Vectors the implementation MUST pin

- Names and keys: `(1, 31)` → block 0, `(1, 32)` → block 1, season 0, a film `wat:movie:550:0:0`; keys `"01"`,
  `"-1"`, `"32"` in block 0 and `"100000"` ignored when deriving and kept through a merge; `d` other than `""`,
  `"local"` or 16 hex refused by writers.
- Stamps: `[0, 0, "a1b2c3d4e5f60718"]` and `[0, 0, "local"]` are timeless; a future winner derives as timeless but
  merges as stored; equal stamps break by JCS bytes; a future reset is not a floor for issuing.
- Merge laws for `wat` and `snt` over random triples, including 30 plays, the same imported play from two sources
  (seconds and ms), duplicate viewings with different times, `cleared` changing in one part, and receipts settled
  on two devices.
- Deriving: resets hiding progress, resume, imported state and plays; a stamp issued after a reset shows past it;
  current viewing under a hidden progress; first play by time with imported plays; film watched-at before its play
  lands.
- Writing: mark watched after a reset writes viewing + 1 with a play; replay starts viewing + 1; un-watch clears the
  viewing before the bump.
- Imports: no claim → written; real progress, in progress or a newer reset → nothing; never a `progress`; film
  import stamp `[0, 1, ""]` loses to any real status.
- Delivery: pending by value (an older-stamped winner on viewing); a reset delivering an un-watch for real and for
  imported episodes; a series watchlist add; receipt stamps are value stamps; each `decide` outcome's receipt; a
  target changed in flight stays pending; a pending value with no command settles silently; no receipt → additive
  unless after `since`; rewatch vs un-watch-and-re-mark; `not_found` final; burst guard; lease take, conflict,
  expiry by observation, and stop.
- Switch: `v3_form` derives §8's state for every coordinate and title (including timeless-over-real in viewing 0, a
  timeless value-0 winner's `cleared`, a future-stamped claim kept as stored, and title events repairing a `rec`);
  unknown rows staged unchanged; seeding leaves a web-written unsettled event and a held baseline command pending and
  a settled one settled; fence refuses batch, member and delete; commit refused on a moved head; generation and
  sequence continue; token and members kept; `426` on batch, member and delete, and the home-check exemption;
  `generation_changed` on a missing or stale generation; a created library inherits the minimum; `wireMin` never
  lowered or raised past `x-den-wire`; a restored lower minimum switched again with receipts written back;
  `write_back` converts a kept v1 journal and emits no `ep` or event row.

## 13. Versions and den-edge

- `wat` and `snt` rows are `schema: 3`. `rec` and `set` rows stay `schema: 2`. v3 adds no field to a `rec` row: v2
  carries a newer version's unknown fields wholesale (v2 §5), which would let an old write roll a new field back.
  `<d>.format`, `set:trackers` and `set:deliver:*` are settings values, which v2 clients merge per setting and keep.
- v2 clients never see `wat` or `snt` rows: before the switch none exist, after it they are refused.
- den-edge adds, and interprets no row: the per-library wire minimum and generation, `x-den-wire`,
  `x-den-wire-min` and `x-den-generation` with CORS and `no-store`, the 426 and its one exemption,
  `generation_changed`, and the fenced rewrite (§9, §10).

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
