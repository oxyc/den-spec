# Library wire format, v3

v3 changes how **episode progress**, **play history** and **tracker delivery** are stored. Everything else —
keys, sealing, title and settings rows, the log protocol — is [library v2](library-v2.md) unchanged, and a v3
client follows every rule there unless a section below replaces it. Vectors: `../vectors/library-v3.json`
(generated with the first implementation; §12 lists what it MUST pin). Every rule below is implemented once, in
den-core, as ops both clients call (§11); a client never re-implements a merge, fold, gate or delivery decision.

Why: in v2 each episode is its own row (~1 KiB charged), and each change a person makes is also kept forever as a
`tracker-event` row carrying whole rows (~3.6 KiB per episode, ~6 KiB per film). A heavy viewer fills a library in
a few thousand episodes (oxyc/den#163). v3 stores 32 episodes per row, keeps no per-change history, records
rewatches, lets any client deliver to trackers (oxyc/den#162), and does it without any client losing or
contradicting state another wrote.

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
   arrival order — given clocks within a day of each other (a stamp more than a day ahead of a reader is read as
   timeless until it is not, §4).
2. **No lost update.** Changes to different episodes or fields never overwrite each other; changes to the same one
   resolve by v2's rule for that field (v2 §5).
3. **No lost state at the switch.** The v3 form derives, for every coordinate and title, the state v2's reference
   reading (§8) derives from the log at the rewrite's `base`, and replaces it atomically while no other write can
   land (§9). A write refused around the switch is kept by the client that made it and sent afterwards (§10). One
   known exception: a stale cached v2 web shell's first visit after the switch (§10).
4. **Imports never override a person.** State learned from a tracker or an import file loses to any change a person
   made on any device, in any order.
5. **Bounded growth.** Rows grow with titles, one per 32 episodes, and one receipt row per watch row and connected
   account plus a fixed number per account — never with changes. No row can exceed den-edge's value cap (§3, §6).
6. **Idempotence.** Replaying any write, any number of times, in any order, changes nothing further.
7. **Nothing delivered twice, nothing dropped.** Among v3 clients, one device at a time delivers for an account; a
   tracker change settled is not sent again, including after the switch or a restore; one unsettled or held stays
   pending. (A refused v2 build may still deliver from its own outbox, and scrobble plays, until it updates, §10.)

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
  - `cleared`: `[viewing, stamp]` — the highest viewing an explicit un-watch cleared and when — or null. Merge: the
    greater viewing, then the later stamp; null is lowest.
- **`seasonReset`**: a stamp or null. Written only to block 0; a reader ignores it on any other block. Merge: the
  later stamp; null is older than any.
- **Numbers**: `value` in [0, 1]; `seconds` in [0, 10⁷]; viewings in [0, 2⁵³ − 1] (a `snt` entry's `p` in
  [−1, 2⁵³ − 1], −1 only in an `n` entry; a `sending` element's `p` may be −1 for an imported play); every stamp within den-core's
  bounds (|t|, c ≤ 2⁵³ − 1). A stamp's `d` MUST be `""`, `"local"` or 16 lowercase hex characters; a writer never
  writes another.
- `schema` is 3; merge takes the max. Row-level unknown fields and invalid keys together are at most 1 KiB of JSON
  and come from the version whose **newest** stamp — the greatest over its registers' `progress.at`, `cleared`
  stamps and its `seasonReset` — is later (ties §4).

**Size.** A worst-case register — maximal numbers, a 16-hex `d`, 8 plays with maximal keys and times, a stamped
`cleared` — is under 660 bytes of JSON, so a full block with 1 KiB of unknowns is under 23 KiB plaintext, within
den-edge's 32 KiB value cap on the base64-encoded sealed value (about 24.5 KiB of plaintext). A typical register is
~120 bytes and a typical full block ~5 KiB sealed. den-edge charges `2(k + v) + fragment + 192` per row, so a typical
full block costs ~15 KiB, ~0.5 KiB per episode (v2: ~4.6 KiB per episode marked).

## 4. Stamps

v2 §4, with these additions:

- A stamp is **timeless** when `t == 0`, whatever `c` and `d` are. Shipped v2 clients write timeless watched bits
  as `[0, 0, ""]`, `[0, 0, "<device>"]` or `[0, 0, "local"]`; all are the same thing.
- **Row merges use stamps as stored**, so every client keeps the same winner. **Deriving** state (§5) and
  **deciding** delivery (§6) read that winner and treat it as timeless if its stamp is more than a day ahead of the
  reader's clock, and treat a play more than a day ahead as not visible. v3 never rewrites a stored stamp — the switch
  included (§8).
- A client issuing a stamp after it has seen a reset stamp `R` (a `seasonReset`, or v2's `episodesReset`) that is
  not more than a day ahead issues one with `t ≥ R.t + 1`, so a change made after a reset always shows past it.
  Reset comparisons use `t` only.
- **Ties.** Where two versions carry equal stamps and differ, the one whose RFC 8785 (JCS) canonical JSON is
  byte-greater wins, so every client resolves the tie the same way.

## 5. Deriving state (v3 library)

### Episodes

For episode `(s, e)` of series `T`, with register `R` from its watch row, and the **covering resets** — the
series' `episodesReset` and the season's `seasonReset`:

- **Hidden progress**: `R.progress` whose `t` is not greater than a covering reset's `t` counts as none.
- **Visible plays**: non-negative keys greater than the `cleared` viewing, and negative (imported) keys whose
  watchedAt is later than the `cleared` stamp's `t`; in both cases with watchedAt later than every covering reset's
  `t` and not more than a day ahead.
- **Hidden imported**: `imported` counts as none when a covering reset exists and no visible imported
  (negative-key) play is later than it.
- **Watched**: unhidden `R.progress` with `value ≥ 0.95`; with no unhidden progress, unhidden `imported`.
- **Resume position**: unhidden `R.progress` that is a viewing in progress, under v2's rules (`RESUME_FLOOR`,
  `continue_entry`).
- **Current viewing** = `R.progress.viewing`, hidden or not, or 0 when there is none.
- The **first play** is the visible play with the least watchedAt. **Watched-at**, the time trackers receive, is the
  visible play of the current viewing, or else the greatest visible play, or null.

### Films

A film's status, resume and reaction are v2's (`rec`). Its visible plays follow the episode rule (§5 Visible plays) on
the `"0"` register: non-negative keys above the `cleared` viewing, negative keys whose watchedAt is later than the
`cleared` stamp's `t`, none more than a day ahead. Status `none` or `watchlist` shows no play (a rewatch in progress
keeps them). Watched-at is the play of `rec.resume.viewing`; else `rec.status.at.t` when that stamp is real; else the
greatest visible play; else null.

## 6. Tracker delivery

Delivery is driven by state and settled with **receipts**. Any client that holds an account's credentials may
deliver for it; one at a time.

### Accounts

- **Account id** is the provider's stable user id (Simkl's and Trakt's user id), fetched from its API when the
  account is connected and at the switch — never a token fingerprint, so a new token is the same account. At most
  one account per provider is connected at a time; connecting another first disconnects the current one.
- **One channel.** A v3 client sends no tracker write outside `pending_targets`. Scrobble start and pause may be sent
  for "now watching"; at or above 80 % a v3 client sends `pause`, never `stop` (a stop there records a watch on
  Trakt, and Den's Simkl adapter turns a stop at ≥ 95 % into a history add); below 80 % `stop` is allowed.
- **Credentials** live in `set:trackers` (sealed like every row; den-edge never sees them), as two settings per
  account:
  - `<provider>:<account id>` — the **connection**, written by a person, or by a device only as §9 allows (a handoff
    reconnect, or nulling the earlier of two non-null connections of one provider; that null is stamped later than
    the connection it replaces, and equal stamps break by JCS, §4): `{"string": <JSON with the tokens and
    `connectedAt` = the connection's whole stamp `[t, c, d]`>}` to connect, `null` to disconnect.
  - `<provider>:<account id>.token` — the **current token**, written only by the lease holder when it refreshes:
    `{"string": <JSON with the tokens and the `connectedAt` it was refreshed from>}`.

  A device uses the account only while the connection is non-null, and uses `.token` only when its `connectedAt`
  equals the connection's element for element; otherwise the connection's own tokens. A failed refresh writes
  neither setting: the holder stops delivering for that account and shows that it needs reconnecting. A refreshed
  token persisted locally, with the current `connectedAt` and a later stamp than the log's, is written back after a
  generation change. So a refresh never resurrects a disconnected
  account and a reconnect always wins. **Only the lease holder refreshes**: it persists the refreshed token locally
  first, then writes `.token` by compare-and-set; on conflict it re-reads and retries if the account's settings are
  unchanged, and discards its token only if they changed. A device holding a refreshed token that is not yet in the
  log writes it the same way whether or not it still holds the lease (the `connectedAt` match guards it). A holder
  whose refresh is refused re-reads `set:trackers` and waits one lease period for a newer `.token` before showing
  that the account needs reconnecting. A write whose outcome is unknown is retried with the same
  value until known. The switch writes every connected account's tokens here — Simkl's from v2's `set:keys`,
  Trakt's from the performing device's own store (v2 keeps Trakt out of the library) — and writes v2's `simkl` key
  in `set:keys` as `null` with a fresh stamp. From then `set:trackers` is the only copy, and v3 clients ignore any
  tracker key in `set:keys`.
- **Delivery row** `set:deliver:<provider>:<account id>` holds that account's `since`, `lease` and `removals`
  settings. Its name is HMACed like every name (v2 §2), so the id never reaches den-edge.
  - `since`: a stamp. A writer never replaces an existing `since`, and two versions merge to the **earlier** one (a
    den-core rule for this setting, not v2's later-stamp rule). Reconnecting the same account keeps it; so removals
    made while it was disconnected are still delivered.
  - `lease`: `{"strings": ["<device id or empty>", "<epoch>"]}`. A device takes, renews or releases it **only** by a
    batch whose `base` is this row's current seq; on conflict it re-reads and re-evaluates, and never merges a lease
    write. A **take** writes its own id and epoch = 1 + the greatest of: the row's epoch, every settle epoch in this
    account's receipts it has read, and the greatest epoch it has held or seen for the account (kept locally) — so
    an epoch is never reused, even after a restore rolls the row back. A **renewal** is sent only if the row it read
    names this device and keeps the epoch; a **release** writes an empty id and keeps the epoch. `lease` merges by
    epoch, then an empty id, then JCS (§4); its stamp is ignored. A write to `set:deliver` that is not a take, renewal or release
    copies `lease` exactly as in the row version it is based on. A holder stops delivering at once when it reads a
    receipt of the account whose settle epoch is **greater** than its own, or **equal** to its own but settled by
    another device (its next take then exceeds that epoch), on any
    `generation_changed`, and on any response whose `x-den-generation` differs from the one it took the lease under:
    a lease holds only within the generation it was taken in. Write-back (§10) never writes `lease`.
  - **Holding.** A device holds the lease only while its own take or renewal succeeded less than 2 minutes ago,
    measured from the **send time** of that request on a clock that counts sleep: `ContinuousClock` on Apple,
    `CLOCK_BOOTTIME` on Linux; in a browser, the **greater** of the elapsed `performance.now` and `Date.now`, and
    `Date.now` going backwards counts as expiry. The holder renews every 60 s, checks the 2 minutes immediately
    before every tracker request, gives every tracker request a total timeout of 60 s, and decides each command
    against a tracker snapshot taken since its take of the current epoch, held continuously since; after a request
    whose outcome is unknown, it reads the target's snapshot again before deciding it again.
  - **Taking.** A device takes only after reading the log to its head, and only after it has seen the row's seq
    unchanged for 10 minutes, measured with the **lesser** of its clocks, or when the row names no device. After a
    generation change, or when the device holds no earlier generation for this library (a fresh browser, cleared
    storage, a new install), it takes only after 10 minutes of observation under the current generation, whatever
    the row names; the performing device's own commit counts as a generation change. A device records a generation
    as observed only once its 10 minutes of observation under it complete; until then, after a restart (page load,
    process launch, reboot), it counts as holding no earlier generation.
  - `removals`: absent, `"held"`, or `{"approved": <stamp>}`. It becomes `"held"` when more than 20 list removals are
    pending for the account, or more than 20 were sent in 120 s, counting only removals whose value stamp is later
    than any `approved`. While held, those removals are not sent. A person approves on a device after seeing the
    list, which writes `{"approved": <fresh stamp>}`: removals stamped at or before it may be sent, and the latch
    re-arms only on removals stamped later. Only list removals count toward it.
- A deliverer sends nothing for an account while its last library write was refused as full or too large, and shows
  why; it resumes once a write succeeds (a person freeing space, or den-edge's cap raised).

### Targets and values

- **Targets** and their **deliverable values**, each with the stamp it came from:
  - **Episode** — a class, a **progress viewing** `p` (the viewing its deciding progress sits in) and watched-at:
    - `watched` (§5), with `p` = the current viewing;
    - `unwatched` — an **explicit** un-watch: real `value == 0` progress in viewing `p` where `cleared` is
      `[p − 1, …]`, with the `cleared` stamp; or a watched state (progress ≥ 0.95 or `imported`) hidden by a covering
      reset, with the latest such reset's stamp and `p` = the current viewing. A `value == 0` that is not an
      explicit un-watch (a replay that started at 0) is `none`;
    - `none` — in progress, or nothing.

    An imported watched value's stamp is `[0, 0, ""]`.
  - **List** (every title): `in` when status is `watchlist` and not `deleted`; `gone` when `deleted` and status is
    `watchlist`; `out` otherwise. Its stamp is the later of the two fields'.
  - **Film watch** (films): `watched` when `rec.status` is `watched`, with `p` = `resume.viewing`, watched-at (§5)
    and the status stamp; `unwatched` only for an **explicit** un-watch — status `none` with `cleared` =
    `[resume.viewing − 1, …]`, with the `cleared` stamp; anything else (`watchlist`, `inProgress`, a `none` that is
    not an explicit un-watch) is `none`.
  - **Rating**: `reaction`.
  - While a title is `deleted`, its film-watch and rating targets are not evaluated: no command, no receipt written.
- **An un-watch survives playback.** Against a `w` receipt, a current value that is not `watched` is `unwatched`
  when the register's `cleared` viewing is ≥ the receipt's `p`, or a covering reset is later than the receipt's floor (§6
  Floor) — whether the current progress is 0 or a new viewing in progress. Its stamp is that `cleared` or reset stamp
  and `p` the current viewing. A film applies the same rule with `resume.viewing` and its `cleared`. So playing an
  episode or film again before an un-watch is delivered never swallows the un-watch.
- A film's `unwatched` value has `p` = `resume.viewing`.
- **Order values.** For the regression order, a class counts as `u` = 0, `n` = 0.5, `w` = 1.
- A `status` change touches two targets, list and film watch; each is decided on its own.
- A value whose stamp is more than a day ahead of the deliverer's clock is not pending until it no longer is.
- **Commands per value** (from the receipt's value to the current one):

  | Target | Change | Command |
  |---|---|---|
  | Episode / film watch | → `watched` | watched (`p`, watched-at) — a rewatch when `p` is greater (below) |
  | Episode / film watch | → `unwatched`, from any receipt but `u` (or none, when pending by §6 No receipt or Seeded accounts) | unwatched |
  | Episode / film watch | → `none` | none, and **no receipt is written**: the receipt stays |
  | List | → `in` | list add |
  | List | `in` → `gone` | list removal |
  | List | any other | none |
  | Rating | → `dislike`/`like`/`love` | rating |
  | Rating | → null or `seen` | rating removal |

  A change with no command settles silently with its current value, except `→ none`, which writes nothing.
  `decide` acknowledges an `unwatched` the remote does not hold as `already_unseen`.
- **Regression.** A current value that orders **below** its receipt's is a regression (a restore, a lagging
  write-back), not a change: it is not pending, and is decided again when the log changes. For a list or rating:
  its stamp is older than the receipt's value stamp. For an episode or film watch: `(p, order value, stamp)` orders
  below the receipt's under v2's progress order (viewing, then value, then stamp). A reset-hidden `unwatched` is never a regression against a `w` receipt of the same `p`, and a value of a
  different class that orders above is always pending.

### Receipts

- A **receipt** records, per account and target, the value a command was **built from**, that value's stamp, and
  its **settle order** `[epoch, n, device id]`: the lease epoch it was settled under, the holder's persisted count of
  settles within that epoch, and the settling device. Entries merge per key by settle order (then JCS, §4), never by
  clock, so a settle made under a later lease always wins, whatever any device's clock says.
- A target is pending only on its class, `p` or field value; watched-at alone never makes it pending.
- **Timeless values.** A command built from a timeless value (an import) is always `baseline`. Against a receipt
  other than `n`, `out` or `null`, a timeless value that differs from it, is not a regression and is not `→ none`
  (a change to `none` keeps the receipt), is decided as `baseline` against the snapshot: acknowledged when the remote
  holds it, so the receipt follows an import from that tracker; otherwise sent additively (a watch or list add the
  remote lacks, or a rating where the remote has no entry). A `baseline` rating built from a timeless value, acknowledged by a
  remote value that maps (§6 Ratings; imports map with the same thresholds) to a different reaction, settles the
  receipt as that remote reaction (as the built-from value when the entry has no value), keeping the built-from value's stamp, so a later change a person
  makes is pending against what the tracker holds; a timeless rating is not pending against a receipt whose value
  stamp equals its own. Against a `u` receipt a timeless `watched` is decided by §6 Against a `u` receipt, never
  acknowledged by presence alone. Against `n`, `out` or `null`, or with no
  receipt, it is caught up additively (`baseline`) like any value before `since`. **Known limit**: a watch the person
  deleted on one tracker's site, then imported from another source, is re-added there once (v2's catch-up does the
  same).
- **Seeded accounts.** den-edge numbers a commit's staged rows `base + 1 … base + N` in staging order (a key staged
  twice takes its last position; N is the number of distinct keys staged, counted by the client before staging the `set:deliver` row that carries it). A switch
  and a re-switch write `seededThrough` = `base + N` into each seeded account's `set:deliver` row with a fresh stamp;
  a compaction stages rows in ascending order of their current seq (a folded row takes the greater of its parts'
  seqs, for both the order and the count) and writes `seededThrough` = `base` + the number
  of staged rows whose seq was at or below the old `seededThrough`, so no target changes side. Write-back (§10) never
  writes it. It merges by the later stamp; a value above the reader's head is ignored. Beside it, the first switch
  writes `seedBound` (`{"int": <ms>}`) with a fresh stamp: for a handed-off account the latest `disconnectedAt.t` of
  its handoffs, otherwise the greater of the performing device's clock at its read of `base` and the greatest play
  watchedAt through `base` not more than a day ahead of it (§10 Ready builds and `stop`). A re-switch (§10) keeps the
  `seedBound` of the `set:deliver` versions it merges and writes one only for an account that has none and that the
  restored log shows connected in v2 form (its credential in `set:keys`, or a handoff) or, for Trakt, that the
  performing device held connected with a local connect stamp earlier than the restored log's greatest stamp, by the
  same formula as the first switch; two values merge to the **lesser**, so a re-switch never widens the windows onto
  viewings finished by v3 clients, which send no stop. Every device reads the §9 switch windows from it. For such an account, a target
  with no receipt whose row has seq above `seededThrough` and whose value stamp is real and not later than `since` is
  pending, not `baseline` (kept or converted work, §10). A list or rating target the switch owes as catch-up carries
  the explicit entry `["b", <value>, <value stamp>, <settle order>]` ("owed as baseline"): decided as `baseline`
  while the current value equals `<value>`, and replaced by its settle; any other current value is decided against
  `<value>` as its receipt. A deleted title's film-watch and rating targets are seeded by the same rules as any other
  (from the earliest unsettled event's `before` when unsettled) and stay undecided while the title is `deleted`. An
  account
  without `seededThrough` (connected after the switch) keeps additive catch-up. For §6 Rewatch and Earlier viewings,
  a pending no-receipt target above `seededThrough` counts as stamped later than `since`, and all its plays count.
- **Unverified receipts.** When any device reads, for one account, receipts at the same settle epoch ≥ 2 from two
  different devices, it adds that epoch to the `set:deliver` setting `unverified` (a list of epochs, merged as a
  union), and every entry at a listed epoch is **unverified**. Seed epochs 0 and 1 (§9, §10) are never unverified.
  An unverified entry is decided against its receipt by every rule above, except that a current value **equal** to
  it is decided against the snapshot instead of being taken as settled: a `w` with `p` > 0 by the rewatch rule's play
  check (Trakt: `H` holds `⌊S / 1000⌋ · 1000`; Simkl: `L ≥ ⌊W / 1000⌋ · 1000`), any other value by `decide`;
  acknowledged if present, sent if not. The holder re-settles every unverified entry under its own epoch in the pass
  that finds it, with its receipt value unchanged when the target decides to no command (`→ none` included). An
  entry whose decision is held, or that is skipped because the title is `deleted`, stays unverified and its epoch
  stays listed until the entry is decided; an epoch is removed from `unverified` once no entry at it remains (an
  epoch re-added by a stale merge with no entries is dropped on the next pass). A regression against an unverified
  entry stays unverified, as a held one does. **Known limit**: for an unverified `w` in `p` > 0, only its own viewing
  is re-checked (with each viewing in its `sending` list, if any), so a Trakt play of an earlier viewing lost in the
  restore stays missing. Re-checking every earlier Den play would re-push plays a person deleted on Trakt's site,
  which is the worse outcome.
- **Only the lease holder writes receipts** — the one exception is write-back after a generation change (§10), which
  only merges receipts that were already settled — and only by a batch whose `base` is the receipt row's seq it read
  before deciding the command, checking the lease immediately before the write. On a conflict or a lapsed lease the
  settle is discarded and the target decided again later. Kept refused writes (§10) never include a receipt or a
  `lease`.
- **Every settled value has an entry**, defaults included (`n`, `out`, `null`, with value stamp `[0, 0, ""]` when
  seeded); absence means no receipt.
- A target is **pending** when its current value differs from its receipt (class, viewing, or field value) and is
  not a regression. Values, not stamps: v2's merge lets an older-stamped version win on viewing.
- **No receipt**: a target with no receipt whose value's stamp is later than the account's `since` is pending. One
  from before `since` is caught up additively only — watched, a list add, a rating — as v2's catch-up; any other
  value settles silently. Connecting an account for the first time never removes anything there, and a change after
  `since` is always delivered. Catch-up never re-verifies settled targets, except once at a ready build's first reconcile and once at the switch (§9): an edit made on the tracker's own site otherwise stands.
- **Rows**, `schema: 3`:
  - Episodes: `snt:<provider>:<account id>:<watch row name>`, one per watch row with a receipt:
    `{"kind": "snt", "schema": 3, "provider", "account", "target": "<watch row name>", "entries": {"2": ["w", 1,
    1789000000000, <value stamp>, [<epoch>, <n>, "<device id>"]]}}` — class (`w`/`u`/`n`), progress viewing `p`,
    watched-at, value stamp, settle order, and an optional sixth element `sending` (§6 Deciding).
  - Titles: `snt:<provider>:<account id>:t<shard>`, `<shard>` = first 3 hex of SHA-256 of the `rec` row's name (4096
    shards): `{"kind": "snt", "schema": 3, "provider", "account", "shard", "entries": {"rec:movie:550#watch": ["w",
    0, 1789000000000, <value stamp>, [<epoch>, <n>, "<device id>"]], "rec:movie:550#list": ["out", <value stamp>,
    [<epoch>, <n>, "<device id>"]], "rec:movie:550#rating": ["love", <value stamp>, [<epoch>, <n>, "<device id>"]],
    …}}`. Film watch receipts carry watched-at (and `sending`) like an episode's.
  - Identity is `provider`, `account` and `target` or `shard`; a reader rebuilds the name and checks the HMAC. Row
    unknown fields and invalid keys follow the `wat` rule (§3, 1 KiB).
  - Names use the account id, not a key-derived hash, so a key reset or linking copies receipts intact (they are
    rewritten under the new key's row names like every row).
- **Size**: a worst-case episode entry is ~520 bytes with a full `sending` (8 `[p, W]`, one `[-1, I]`, one
  `[p, null, T]`), so a full block's receipt row stays under ~17 KiB; ~0.13 KiB charged per episode per account
  typically. A title entry is ~180 bytes worst case with its lasting `sending` elements (`[-1, I]`, `[p, null, T]`);
  `[p, W]` elements exist only between an intent and its settle, so a shard fits ~44 films at rest and ~28 with
  every film mid-send; at den-edge's 50 000-row limit a shard averages ~11 titles, so the at-rest bound is reached
  with probability about 10⁻¹⁰. Only a higher den-edge value cap recovers a shard refused as too large. A shard write refused as too large stops delivery for that
  account (above) rather than losing a receipt.

### Deciding and settling

- `pending_targets` builds each command from the table above — never from v2 `commands`. Each carries `at` (the
  value stamp's `t`, for ordering) and `watched_at` (§5, sent to the tracker). It fills `decide`'s command as:
  `current` = true; `baseline` = the target has no receipt and its value's stamp is not later than `since` (except as §6 Seeded
  accounts says), or its value's stamp is timeless; a rating command built from a timeless value sends `c` as
  `rated_at` when `c` > 1 and omits it otherwise; list and watch commands built from one send no import time beyond
  `watched_at` (§5); `episode`
  for an episode target; `added` = true for a list add, false for a removal; `rating` = 10 / 7 / 2 for love / like /
  dislike, null for a removal. The `Remote` side is filled from the tracker snapshot as v2 does, and
  `episodes_complete` MUST be true only when the snapshot lists every watched episode of the show (both shipped
  trackers' full syncs do), or episode un-watches hold.
- **Intent before a Trakt rewatch.** Before sending Trakt a watched command decided by the rewatch rule, the holder
  writes into the target's receipt entry a sixth element `sending`: the list of `[p, W]` it will send (one per play,
  below), by the same compare-and-set as a settle and with a **fresh settle order** `[own epoch, next n, own id]`;
  the entry otherwise keeps its old value and still counts as it. A later holder reuses a listed `W` only for the same
  `p`, and the rewatch rule checks that `W` only then; entries for any other `p` are ignored. The settle that clears it carries a later settle order, and its
  `base` is the seq its own intent write produced. So a watched-at lowered by a merge between send and resend cannot
  add a second play, and a later viewing is never acknowledged by an earlier one's intent. A first watch needs no
  intent: `decide` acknowledges any remote watch. `sending` holds three kinds of element: `[p, W]` (an intent,
  `p` ≥ 0, `W` non-null), `[-1, I]` (an imported play Den sent, §6 Earlier viewings) and `[p, null, T]` (a viewing
  the switch seeded at ≥ 0.8, §9). An intent write replaces only the `[p, W]` elements; every `[-1, …]` element is
  kept by every later intent write and every settle of that target, a `w` or `u` settle included, and every
  `[p, null, T]` element by every intent write and every settle (a settled viewing is not pending, so it only ever
  takes part in §9's matching). `S` for a
  viewing is the `W` of its `[p, W]` element.
- **Ratings** are acknowledged when the remote's rating maps to the same reaction (≤ 4 dislike, 5–7 like,
  ≥ 8 love), so a person's tracker rating is never overwritten by its Den equivalent. A known remote time is always
  passed to `decide` as is. A rating receipt entry carries an optional fourth element, the remote rating (1–10) that
  acknowledged it: `[<reaction>, <value stamp>, <settle order>, <remote rating>?]`, written when the entry settles
  by an acknowledgement whose remote value differs from the rating Den sends for its reaction (§6 Timeless values
  included). When the remote rating's time is unknown and its value **equals** the entry's fourth element or, when
  there is none, the rating Den sends for the receipt's reaction (10, 7 or 2) — the tracker still holds exactly what
  Den last settled — `decide` gets its `at` as 1, so a pending change goes through; any other remote rating whose
  time is unknown is held as `remote_order_unknown_or_newer` (a same-bucket site edit, 6 against 7, is detected).
  The fourth element is not part of the value: it never makes a target pending and plays no part in a regression. A
  settle by Send writes none; a re-settle by acknowledgement (an unverified one included) rewrites it from that
  pass's remote value; an acknowledgement by a valueless remote entry writes none, and a valueless remote entry
  whose time is unknown is passed with `at` 1. Trakt's known time is passed as is (Trakt stores the `rated_at` Den
  sends); Simkl's is always passed as unknown, because its all-items feed does not reliably carry a per-item rating
  time. A seed-epoch re-read records the remote value even when it equals the rating Den sends. When the receipt's value stamp is timeless (Den sent it with an import time or no
  `rated_at`), a remote rating whose value equals the entry's fourth element or, with none, the rating Den sends
  for its reaction, is passed with `at` 1 whatever its time. A rating entry settled at seed epoch 0 or 1 with no
  fourth element, whose target is not pending, is re-read at the first pass of each lease holder: a remote rating
  that maps to the entry's reaction is recorded as its fourth element, by a re-settle under the holder's epoch with
  the value unchanged; the first pass is the first one whose snapshot of that account is complete. A pending target
  is never re-read; its remote rating is compared with the entry's fourth element or, with none, the rating Den
  sends for the entry's reaction (the known limit below applies). An owed-as-baseline `["b", …]` entry is not re-read;
  its settle records the value.
  **Known limit**: a person's Den rating change against a tracker rating edited on its site with no time is held
  until the person resolves it; Den never overwrites a site edit it cannot order.
- den-core `decide` sends, acknowledges, supersedes or holds each command against the tracker's snapshot with every
  v2 hold reason — remote order unknown or newer (v2's comparison), incomplete coverage, account mismatch,
  independent state (a removal that would drop a list entry or rating) — plus `removals` held (above).
- **Remote plays.** Simkl reports only the **latest** play per episode or film (`watched_at`); `decide` gets it as
  `L`, at second precision, or null when unknown. Trakt lists every play (`GET /sync/history/{type}/{id}`), which
  `decide` gets as the set `H`.
- **Floor.** A receipt's floor is its watched-at for a `w` receipt and its value stamp's `t` for a `u` receipt, at
  second precision. An `n` receipt, and no receipt, has a null floor. A null floor is below every time; a null `W` is never acknowledged by a play time. A covering
  reset is **later than a floor** when `⌊R.t / 1000⌋ · 1000` is greater than it (a null floor is below every reset);
  every comparison of a reset with a floor in §6 and §9 uses this. A default
  `n` entry is `["n", 0, null, [0, 0, ""], <settle order>]`. A tracker adapter passes a missing watch time (Simkl's
  epoch sentinel) as `L` = null, never 0.
- **Rewatch.** `watched` in a `p` greater than a `w` receipt's, with `cleared` viewing below the receipt's `p` and no
  covering reset later than the receipt's floor, is a rewatch. So is a `watched` value, with no receipt and stamped later
  than `since`, or against an `n` receipt whatever its stamp, whose register has an unhidden `imported`, or a visible imported play whose watchedAt is earlier than
  `⌊W / 1000⌋ · 1000` of the current viewing's Den play, **and whose current viewing has a
  Den play** (a non-negative key): its floor is the greatest visible imported play's watchedAt (null if none), and it
  sends only Den plays. A `watched` value derived from `imported` alone, and any value stamped at or before `since`
  with no receipt, is decided by `decide` (with `baseline` when it has no receipt), so any remote watch acknowledges
  it — an imported watch is never sent back as a new play, and connecting an account pushes no older rewatches. Den
  always sends `⌊W / 1000⌋ · 1000` as a play's `watched_at`. On **Trakt**, with `H` read across every page of the
  history endpoint and every play in it taken at second precision (`⌊h / 1000⌋ · 1000`), a rewatch sends **one play
  per visible Den play** whose viewing is in (receipt `p`, current `p`] — against an `n` or `u` receipt, every visible
  Den play in [receipt `p`, current `p`], and with no receipt decided in the two steps of §6 Earlier viewings (the viewings before the split caught
  up alone by `decide` with `baseline` and settled as a `w` receipt, the rest decided in a later pass as a rewatch
  against it, or, when none is before the split, the intent written into a new default `n` entry), in all cases above the `cleared`
  viewing — with the current viewing's watched-at as `W` when it has no play, each acknowledged iff `H` holds a
  play at exactly `⌊S / 1000⌋ · 1000`, `S` being its `sending` entry if one exists for that `p`, else its `W`. On **Simkl** it is one play, acknowledged iff `L` is known, `L ≥ ⌊W / 1000⌋ · 1000` and
  `L` is later than the floor, all at second precision.
  Otherwise it is sent, even when the remote holds other watches. A resend after a crash or a lapsed lease therefore
  finds the play it already made and adds none; a tracker play from elsewhere that predates this rewatch does not
  swallow it. **Known limit (Simkl only)**: a play from outside Den later than `W` acknowledges an unsent Den
  rewatch, and several Den viewings between two passes reach Simkl as one play, so Simkl's play count can be short —
  unavoidable while Simkl reports only the latest play.
- **Un-watch then re-mark.** `watched` in a `p` greater than a `w` receipt's, with `cleared` viewing at or above the
  receipt's `p` **or a covering reset later than the receipt's floor**, is two steps. First the un-watch alone, `at`
  = the `cleared` (or reset) stamp's `t`: sent and settled, it writes the receipt `["u", <p>, null, <its stamp>,
  <order>]` — `p` being the un-watch's progress viewing, or the receipt's `p` for a reset — whatever the current
  value is. The re-mark is decided only in a later pass. If the un-watch step is held as
  `remote_order_unknown_or_newer` or `would_remove_independent_state`, it is skipped and the re-mark is decided in
  the same pass by the rewatch rule against the `w` receipt. Held for any other reason, both steps hold and are
  decided again later.
- **Against a `u` receipt**, `watched` is acknowledged by the same rule as a rewatch, and sent otherwise — so a
  snapshot that still shows the old watch does not acknowledge a re-mark.
- **Settling.** Sent, or acknowledged (already present) → the receipt is the value the command was built from,
  whether or not the target changed meanwhile (a later change is then pending against it). Superseded → nothing. A
  coordinate the tracker cannot hold (`not_found`, or one that does not map back) → the built-from value, final. Held
  → nothing; decided again later. Exception: a `baseline` rating built from a timeless value, acknowledged by a
  differently mapped remote rating, settles as the remote reaction (§6 Timeless values).
- **Earlier viewings on Trakt.** Against an `n` receipt, or with no receipt and stamped later than `since`, a
  `watched` value in a `p` above the receipt's (with no receipt: at or above 0 when §6 Rewatch covers it, else above
  0), whether or not §6 Rewatch covers it (the two steps
  below apply to both), is decided on Trakt as a rewatch: one play per visible Den play of a viewing in [receipt `p`,
  current `p`] above the `cleared` viewing (with no receipt, only the viewings from the split on), the current viewing with its watched-at as `W` when it has no play, each first written into `sending`
  (§6 Intent) and acknowledged iff `H` holds `⌊S / 1000⌋ · 1000` exactly, with §9's window for a target seeded at the
  switch; `decide`'s any-watch acknowledgement never applies to it. With no receipt, the target is decided in two
  steps around the **split**, the least viewing whose visible Den play is later than `since.t` (viewing order, not play
  time, since play times need not grow with viewings): first the viewings below the split that have a visible Den
  play (a viewing with none — hidden by `cleared` or a reset, or known only from an import — takes no part) are caught
  up alone by `decide` with `baseline` and watched-at = the play of the greatest of them, and settle as `["w", <that
  viewing>, <its play>, <value stamp>, <order>]`; the remaining viewings are decided in a later pass as a rewatch
  against that receipt. When no viewing below the split has a visible Den play, the intent is written into a new default entry `["n", 0, null, [0, 0, ""], <fresh order>,
  <sending>]`, which selects the same plays. On **Simkl**, the same targets are decided as one play of the current
  viewing, acknowledged iff `L` is known, `L ≥ ⌊W / 1000⌋ · 1000` and `L` is later than the receipt's floor (null for
  `n` or no receipt), with §9's Simkl window for a target seeded at the switch; with no receipt, only when some visible
  Den play is later than `since.t` (this gate applies to §6 Rewatch's Simkl branch too; otherwise the catch-up below
  applies). `decide`'s any-watch acknowledgement never applies to it. On Trakt, for a target whose current value is
  `watched`, against an `n` receipt whose watched-at is null, or with no receipt (whatever its stamp), when the
  register has a visible imported play earlier than
  `⌊W / 1000⌋ · 1000` of its least-watchedAt visible Den play, that imported watch is first decided alone by `decide` with `baseline` and watched-at = the
  greatest such imported play, before any Den play is decided. Before a send it adds `[-1, ⌊I / 1000⌋ · 1000]`
  to `sending` by §6 Intent (`I` the imported play), keeping every other element — with no receipt, as a new entry
  `["n", −1, null, [0, 0, ""], <fresh order>, [[-1, ⌊I / 1000⌋ · 1000]]]`, which counts as no receipt (its null
  watched-at leaves the step not yet done) — and settling keeps that element, so it records that Den sent
  that play. Sent or acknowledged, it settles as `["n", <the
  receipt's p, or −1 when the target had no receipt>, <that imported play>, <the receipt's value stamp, or [0, 0, ""]>, <order>]`, keeping any
  `sending`. On an `n` entry the watched-at only records that this step is done; its floor stays null (§6 Floor). The
  Den plays follow in a later pass, decided against that entry; for a target that had no receipt they follow the
  no-receipt rules (the split from `since`, and the catch-up when no visible Den play is later than `since.t`), an `n`
  entry with `p` −1 counting as no receipt for every rule except its settle order and this step, which an `n` entry
  with a non-null watched-at always marks done (its `p` reads as 0 wherever a viewing is compared; −1 is allowed in
  `n` entries only, and a reader MUST accept it there), and the split's intent written into this entry. **Known
  limit**: an imported play reaches Trakt only through this step, and only when Trakt then holds no watch of that
  episode; any other imported play is not sent to Trakt. When no visible Den play is later than
  `since.t` (never for a target above `seededThrough`, whose plays all count as later), the target is caught up alone
  by `decide` with `baseline` and watched-at (§5), and settles as `w` at the current viewing, on both trackers.
- **Remote → Den**: v2's tracker pull, written as imports (§7) — episodes, films, ratings and watchlist — only after
  pulling the log to its head.

## 7. Writing (v3 library)

Every write is a set-to-value; replaying one changes nothing. `register_write` (§11) decides every case below.

- **Playback** updates `progress` in the current viewing as v2 updates an `ep` row. It starts viewing + 1 when the
  current viewing's **stored** progress is ≥ 0.95 or is hidden by a covering reset, or the register is `imported`
  (unhidden) and the current viewing has no progress, or that viewing has a play hidden by a reset or `cleared` —
  judged on the stored register, never on derived state (as the shipped TV does for a
  finished episode). A playback write never writes `value` 0 in a new viewing: the
  first write of a viewing carries a value above 0. When `value` first reaches 0.95 in a viewing, the client adds
  the play `viewing → stamp.t` if that viewing has none.
- **Mark watched** on an episode or film that is already watched (§5) writes nothing, so marking a season or series
  watched never touches an imported or finished episode. Otherwise: `progress` = value 1 with a fresh stamp, in the
  current viewing — or current viewing + 1 when
  the current viewing already has a play that is hidden by a reset or `cleared` — and the play for the viewing it
  wrote.
- **Un-watch**: `cleared` = `[current viewing, fresh stamp]`; then `progress` = value 0 in current viewing + 1 with
  that stamp.
- **Rewatch**: progress in current viewing + 1, as v2; earlier plays stay.
- **Season reset**: block 0's `seasonReset` = a fresh stamp. **Series reset**: `rec.episodesReset` (v2).
- **Import** (a tracker's watched list, an import file) of episode `(s, e)`, after pulling the log to its head:
  only if the episode has no unhidden `progress`, is not in progress, and has no covering reset at or later than
  the import's newest play (with no plays, no covering reset at all), the same comparison §5 uses to hide it, so an
  import never writes an `imported` a covering reset hides on arrival. A play known only to the day, as a file
  import's is, is written as the start of that day in UTC (whole seconds), so the same viewing imported from any
  device gets one key and never outranks a person's reset made later that day. A day-only play of date D is not
  written when the register holds a Den play (non-negative key) whose watchedAt equals local noon of D in the
  importing device's time zone, as v2's import computed it (§8), or, failing that, a Den play whose watchedAt is a
  whole multiple of 900 000 ms within [D − 2 h, D + 24 h]; exact matches are taken first for every date, then
  range matches with dates in ascending order, each date taking the earliest Den play in its range not yet taken,
  and each Den play excuses at most one day-only play. Imported plays
  never do, so consecutive-day viewings are all written in any order. **Known limit**: only for a v2 import made in
  a zone of UTC−10 to −12 or +12 to +14 and re-imported from another zone can consecutive-day viewings be
  matched to the wrong day. The reset comparison above uses
  the play as written (whole seconds). A play a tracker reports is not written when it matches one-to-one, by §9's
  one-play-per-viewing matching, a viewing whose ⌊W⌋ the tracker does not hold exactly and that §9's window for that
  account would take part in: a viewing with a Den play `W` ≤ `seedBound`, within the **viewing window**
  (⌊W⌋ − 86 400 000, min(⌊W⌋ + 90 000 000, `seedBound` + 86 400 000)]; a play-less viewing §9 matches, within its
  window there; or a viewing with a `[p, null, T]` element in that account's entry — whether or not it has a Den
  play yet, and whatever its `W` — within (T − 86 400 000, `seedBound` + 86 400 000]: it is a v2 scrobble of that
  viewing, recorded at the tracker's server time, so delivery and import agree on it (**known limit**: a tracker
  play from outside Den within about a day of such a Den play is taken as its v2 scrobble and not written; v2's
  Simkl stop fires only at ≥ 95 %, so it lands at or after `W`, and a ready build's kept stop lands within 25 hours
  of it). **Known limit**: a reset later
  than 00:00 UTC of the play's date hides it (for a person west of UTC that includes a late-evening reset on the
  previous local day), so such a viewing reads unwatched until marked. It writes its plays either way (plays are additive and never set watched state), and `imported: true` only under
  that condition (§3). A play whose watchedAt, at second precision, equals `⌊W / 1000⌋ · 1000` of a Den play in the
  same register is not written: it is Den's own delivery reported back. A writer skips a play write whose kept selection is unchanged. An import writes no series `status` and no
  `dismissed`; a series' watched state is derived from its episodes (**known limit**: today's web
  import also marks a finished series watched and dismisses a long-stale one from Continue Watching; a v3 import
  leaves both to the person). It writes no `progress`, so it never
  outranks or erases a person's progress, even when a concurrent write merges with it; and it never lands over a Den
  un-watch or reset, even with a newer remote watch time. A film import writes `rec.status` watched with
  `[0, <its latest play ms>, ""]` (timeless, so any real status wins) under the title-import rule below; it writes
  its plays either way (plays are additive). **Known limit**: unlike today's web import, which writes real day stamps,
  a v3 import neither marks an episode already in progress in Den nor changes a watchlisted film's status; the plays
  are kept, and a person's Mark watched delivers it. **Known limit**: den-core `decide` acknowledges a `baseline` list add by any
  title watch, so a watchlist add imported from one tracker does not reach another tracker's watchlist for a title
  that tracker has watched.
- **Title imports** (a tracker's ratings and watchlist) are timeless, so any real stamp beats them (§4); their
  counter `c` orders imports among themselves by the tracker's own time while `t` stays 0. A field is
  **import-owned** when its stamp is timeless; an absent field (or a title with no `rec` row) counts as
  `[0, 0, ""]`. **Every title import writes only where the field is import-owned and
  its current stamp is earlier than the import's stamp under §4**, so a sequential write and a merge agree whatever
  the order. A missing tracker time (Simkl's epoch sentinel) is `c` = 1, the lowest import time, and an import never
  rewrites a field already holding the same value from an import. **Known limit**: a rating or watchlist entry a
  shipped v2 TV imported carries a real stamp, so it is treated as a person's; a later removal or re-rating of it on
  the tracker does not reach Den (its receipt is seeded as current, so nothing is pushed back either).
  - a rating writes `reaction` with `[0, <rated_at ms>, ""]`;
  - a watchlist add writes `status` watchlist with `[0, <listed_at ms>, ""]`;
  - a watchlist removal — a title present in the puller's previous complete watchlist snapshot of that account
    (kept locally by the device that pulls, which is the account's lease holder) and absent from **every** connected
    account's current complete snapshot read in the same pass — writes `status` `watched` if the title is a film
    whose `"0"` register holds a negative-key (imported) play with watchedAt later than its `cleared` stamp's `t`
    (null `cleared`: any), judged on the stored register whatever `status` is, else `none`, with `[0, max(<that title's previous listed_at ms>, <the field's c>) +
    1, ""]`, only where `status` is `watchlist`.
    An account whose snapshot cannot be read in the pass blocks removals for that pass; a cached complete list whose
    tracker activity stamp is unchanged counts as a current complete snapshot.

  Imports never write `deleted`, and skip a title that is `deleted`. Being timeless, none of them is pending against
  a receipt (§6).
- **Films**: status, resume and reaction stay in `rec` (v2). Finishing or marking a film watched also writes the
  play for `resume.viewing`, **in the same batch** as the `rec` change. Un-watching sets `cleared` to
  `[resume.viewing, stamp]` **before** v2 bumps it, in the same batch too. Playing a film whose status is `watched`
  while `resume.value` < 0.95 (an imported watch) starts `resume.viewing` + 1, as a finished resume does.

## 8. v2's reference reading

The v3 form (§9) folds v2 rows by what v2 web derives, which is den-core's reading. For episode `(s, e)`:

- **Claims**: the `ep:tv:T:s:e` row and the `after` of every v1 `tracker-event` whose `after` is that row
  (Appendix A), as stored in the log.
- **Merged progress** = den-core `merge` over **all** claims, real and timeless (v2 §5). **State** =
  `episode_mark(merged, held: null, authoritative: true)` with the title's `episodesReset` applied by `t`.
- **Fold**: a real merged progress becomes the register's `progress`, stamp unchanged; a timeless watched state
  (`t == 0` and `value ≥ 0.95`) becomes `imported: true`. v2 web demotes a future stamp when it reads a row and
  keeps the demoted copy locally; the log still holds the stamp as written, and the fold uses that, so no device's
  clock becomes part of the stored library. A stamp more than a day ahead is demoted when deriving (§4), as v2 web
  shows it.
- **Plays**: for every viewing with a real claim of `value ≥ 0.95`, the least such `t`. **Cleared**: the greatest
  `viewing − 1` over claims of `value == 0` **with viewing ≥ 1**, with that claim's stamp; null if none.
- **Titles**: each `rec` row merged with every title event's `after` (v2 merge), as a v2 TV repairs an interrupted
  projection.
- **Film plays**: for a film whose merged `rec` has status `watched` with `status.at.t > 0`, the play
  `resume.viewing → status.at.t` in a `wat:movie:<id>:0:0` row.
- **Film cleared**: for a film whose merged `rec` has status `none`, `resume.viewing ≥ 1` and `resume.at ==
  status.at` (the v2 un-watch shape both clients write), `cleared = [resume.viewing − 1, status.at]`.
- Known shipped behaviour the fold keeps as it is: a replay that starts at fraction 0 writes value 0 in viewing + 1,
  which v2 and v3 both read as an un-watch. Known shipped differences the fold does not follow: a v2 TV keeps a held
  real mark where the web flags a timeless watch, and ignores a timeless value-0 row the web clears; v2 web's detail
  page compares whole stamps where its library compares `t`.

## 9. The switch

A library becomes v3 in one step: den-edge replaces its log with the **v3 form** and raises its **wire minimum**,
atomically.

**Who and when.** Every device reports its format as the setting `<d>.format` = `{"int": 3}` in `set:devices`
(merged per setting, v2 §5). The switch is **offered** once every device whose `<d>.seen` setting is stamped
(`at.t`) within 180 days reports format 3, **every `tv`-kind device reports format 3 whatever its `seen`** (a TV can
hold an outbox that still delivers) unless a person removed it, and no device has entries but no `seen` (unknown
counts as not ready). It is **performed** only by a device holding the v2 delivery outbox for every connected
account, after a full reconcile and drain since its last install, so it holds nothing but held commands. A drain
counts for the switch or a handoff only once every catch-up (baseline) command for that account has been sent to it
or acknowledged by it. A catch-up is the additive command the latest full reconcile derives from a row's current
state; one that reconcile no longer derives (superseded, or its row changed) is neither owed nor replayed. A
catch-up that reconcile derives but that was completed without that account's acknowledgement (orphan-retired, or
completed while the account refused its credential) counts as unsent: a ready build does not keep its id as
delivered and delivers the re-derived command to the account now connected (a catch-up is additive and `baseline`,
so any presence acknowledges it). A ready build cannot tell which of a pre-ready build's completed catch-up ids were
acknowledged, so it keeps none of them: on its first reconcile it drops every catch-up id it inherited from a
pre-ready build and decides each catch-up that reconcile regenerates against the account's snapshot, sending or
acknowledging it, before a drain counts. **Known limit**: a pre-ready build keeps only event and catch-up
ids, never which account took them, so at its first reconcile and at the switch every command a pre-ready build
delivered is decided again against the snapshot: a watch, watchlist entry or rating delivered and then removed on the
tracker's own site while Den still holds it is added again (as a v2 reinstall does). A rating changed on the
tracker's site since is held as `remote_order_unknown_or_newer`, not overwritten. A catch-up held as `snapshot_unavailable` or `account_changed` is retried until decided, and a catch-up rating
is acknowledged by any remote rating entry, valued or not, so no catch-up holds for good and seeding never turns one
into a non-baseline command. While a catch-up cannot be delivered (a credential stays refused, or the tracker keeps
refusing that command; a `not_found` from any tracker counts as sent), the offering client shows that, with the title, as why
the switch waits. If connected accounts are delivered by different devices, each other device
disconnects its accounts only once their handoffs are stored, after draining their outboxes: each **handoff** is
written in the same batch as its disconnect (for Simkl, the `set:keys` null; for Trakt, whose tokens are local, the
local disconnect follows the stored batch). If that batch is refused as too large (den-edge answers 400 for a value
over its cap), the device writes `ratings` as `"all"`, then also `unsettled` as `"all"`, and retries; if it is still
refused, the account stays connected, no handoff is written, and the offering client shows why the switch waits.
Both fallbacks only widen re-decision: with `unsettled` `"all"`, the known limit on pre-ready deliveries applies to
every delivery of that account; with `ratings` `"all"`, see below. Sizes are measured on the UTF-8 JSON before
escaping. A handoff is the setting
`<d>.handoff:<provider>` in `set:devices` = `{"string": <JSON {account, disconnectedAt, generation, head, headAt,
readyFrom, unsettled}>}`, with `ratings` written in the same batch to its own row
`set:handoff:<provider>:<account id>` (setting `ratings`, `"all"` only when that row would exceed den-edge's cap)
as `{<rec row name>: <remote rating>}` for every rating target of
that account whose latest settlement on this device was an acknowledgement recording a remote value different from
the rating Den sends for its reaction (keys that match no row count as absent; **known limit**: with `"all"`, a
person's Den rating change made before the first complete pass against a tracker value Den had acknowledged is
held as a site edit would be). Once that account is connected in `set:trackers` after the switch, the device that
connected it writes every `<d>.handoff:<provider>` naming that account, and its `set:handoff` row, `null`. A
handing-off device that has not observed a commit (a generation change with minimum 3) within 24 hours of its
handoff, and reads no open rewrite, **withdraws** it: in one batch it writes its handoff and `set:handoff` row
`null` and restores its connection in v2 form (Simkl: `set:keys` with its original stamp), then resumes its outbox
from `head`; a withdrawal refused as `rewrite_in_progress` is dropped if the next read shows minimum 3. Here `generation` and `head` are the library generation and log seq its final drain read
through, `headAt` its own clock (ms) at that read, `readyFrom` the lowest log seq its ready build read (its
persisted head + 1 when it read none), and `unsettled` the ids of events in [`readyFrom`, `head`] whose commands it
did not deliver (held, superseded, orphan-retired, or unsent; an event counts as delivered only if it is in the
device's own per-account acknowledgements — an id-only receipt or completed flag inherited from a pre-ready build
counts as not delivered) — or `"all"` if that list would exceed 2 KiB. The switch counts an event as settled for
that account only when `readyFrom` ≤ its seq ≤ `head` and it is not in `unsettled` (every event below `readyFrom`
is unsettled; the known limit on pre-ready deliveries covers the re-decision; an event whose
`commands(event, event.after)` is `[]` is never listed); with `"all"`, or when `generation` differs
from the generation the switch reads under, every event for that account is unsettled. An episode or film-watch
target of that account **qualifies** when its current value is `watched`, it has no unsettled event (a finish by
playing writes none), and (`"all"` only makes every event unsettled; `head` stays valid in the same generation):
- an **episode**: its row seq is above `head`; or, when `generation` differs, its `progress` stamp `t` is later
  than `headAt − 86 400 000`;
- a **film**: its row seq is above `head` and its `status` or `resume` stamp is later than the `at` of every
  settled film-watch event of it; or, when `generation` differs, that stamp's `t` is later than `headAt −
  86 400 000`. A rating- or list-only edit qualifies a film only when no film-watch event of it is settled (the known limit
  below), and a file import after `head` does. **Known
  limit**: a film watch removed on the tracker's site whose `rec` row changed after `head` is added there again once.
- a **list** or **rating** target: its value is `in` or a reaction other than `seen`, it has no unsettled event, its
  field stamp is later than the `at` of every settled event of that field, and its `rec` row seq is above `head` (or,
  when `generation` differs, its field stamp `t` is later than `headAt − 86 400 000`). Such a target is seeded with
  the owed-as-baseline entry `["b", …]` (§6 Seeded accounts), so any presence acknowledges it; one whose field stamp is later than the switch's stamp is seeded with
  the default (`out`, `null`) instead, and is delivered once it is no longer more than a day ahead.

A qualifying target is seeded unsettled: as `["w", p₀, <that play's watchedAt>, [0, 0, ""], [1, n, <performer>]]`
when some viewing **below** the current one has a visible Den play with watchedAt ≤ `disconnectedAt.t` (`p₀` the
greatest such viewing), so later viewings go through the rewatch rule; otherwise, when the current viewing (a
film's `resume.viewing`) is above 0: if the register's `cleared` viewing is current − 1 (an un-watch, or a replay
started at 0, since the target has no unsettled event), as `["u", <current>, null, <the cleared stamp>, [1, n, <performer>]]`, so
the watch is decided against a `u` receipt; else as `["w", <current − 1>, <⌊R.t / 1000⌋ · 1000 of the latest
covering reset not more than a day ahead of the performing device's clock, or null>, [0, 0, ""], [1, n,
<performer>]]`, since v2 keeps no play for a viewing finished by playing (a bare null
floor would make any old reset trigger un-watch-then-re-mark); otherwise with the default `n`, so `decide`
acknowledges the watch if present and sends it if not. Handoffs naming the **same account**: an event is settled if
any of them settles it; a target qualifies only if it qualifies under every one; `since` is the earliest
`disconnectedAt`; only the one with the latest `disconnectedAt` reconnects, and the seed's `p₀` bound and the Trakt scrobble window
use that latest `disconnectedAt`. **Known limit**: a viewing finished
offline before `disconnectedAt` that syncs after `head` is taken as delivered, so Trakt may hold one play fewer (v2
would not have delivered it either). The current viewing is never seeded as delivered: a finish
on another device, or a failed best-effort scrobble, may never have reached the tracker. For every
target of an account whose `set:deliver` row holds `seedBound` (§6 Seeded accounts), whatever its receipt's settle
order and however that receipt was rewritten since — which covers every target seeded unsettled at the switch, by a
handoff's qualification, from an event's `before`, from a reconcile read, or as `u` by the replay-at-0 exception —
the Trakt rewatch check of a pending viewing whose play has watchedAt `W` ≤ `seedBound` also acknowledges it by a
play in `H` later than both the receipt's floor and `⌊W / 1000⌋ · 1000 − 86 400 000`, at or before
min(`⌊W / 1000⌋ · 1000 + 90 000 000`, the **window end** `seedBound + 86 400 000`) (H is server time,
`seedBound` a device clock; the same viewing window as §7), and not matched exactly
to another viewing, nor to an imported play this target's imported-first step **sent** (a `[-1, …]` element of its
`sending`, §6 Earlier viewings); an imported play Den did not send — one a tracker reported back, a v2 scrobble
pulled after the switch included — takes part in the window like any other play. The receipt's own viewing takes part in the matching when `H` holds no play at exactly its
`⌊W⌋`, and every viewing at or above the receipt's, below the current one, that has no play takes part with the
window (receipt floor, window end], so no viewing's own scrobble stop is ever credited to a later viewing; each play acknowledges at most one viewing, earliest viewing to earliest play, since a v2 scrobble records its stop time (sent when the player closes at 80 % or
more, which can precede `W`'s write), not `W`. **Known limit**: a Trakt play from outside Den inside that window
acknowledges the viewing; a viewing whose final stop failed after an earlier session's stop more than a day before
`W` reached Trakt may be sent again.
For such a target, a pending viewing whose play has watchedAt `W` ≤ `seedBound` is also acknowledged on Simkl when `L` is later
than both the receipt's floor (the stored receipt's own floor, §6 Floor — null for an `n` entry — never §6 Rewatch's
imported-play floor) and `⌊W / 1000⌋ · 1000 − 86 400 000` and at or before min(`⌊W / 1000⌋ · 1000 +
90 000 000`, `seedBound + 86 400 000`), since a v2
Simkl scrobble carries Simkl's server time, not `W`; a viewing with `W` above `seedBound` is checked by §6 Rewatch
alone. **Known limit** (Simkl only, which reports one latest play): for a viewing at or before `seedBound`, an
earlier viewing's scrobble within a day acknowledges a later one whose own scrobble failed.
The switch records every viewing it seeds while in progress at ≥ 0.8 as a `sending` entry `[p, null, T]` in that
account's Trakt entry only — and only when the entry holds no `T` element for that `p` and `T − 86 400 000` is
below `seedBound + 86 400 000`, so a re-switch never adds a second — `T` being `⌊t / 1000⌋ · 1000` of that viewing's seeded progress stamp. For that viewing,
whatever its `W` and whether or not `W` ≤ `seedBound`, the Trakt check also acknowledges it by a play later than both
the receipt's floor and `T − 86 400 000` and at or before the window end, not matched exactly to another viewing nor
to a `[-1, …]` element, under the same one-play-per-viewing matching, since v2's Trakt stop at 80 % is sent when the
player closes, at about `T`; the `null` is never sent or matched exactly. The intent write before any send adds
`[p, ⌊W / 1000⌋ · 1000]` of the finished viewing beside it, and that viewing keeps the `T` window as well.
**Known limit**: device clocks more than a day off the tracker's put a v2 stop outside every window, and it is sent
again. **Known limit**: when a viewing below
the current one was finished by playing after `disconnectedAt` and a replay then started before the switch, the
`w` at p − 1 seed marks it delivered, so an abandoned replay leaves it off that account (as v2). **Known limit**: a film finished by playing that reaches the log after
`head` with a stamp more than a day older than `headAt` is not delivered to that account, and, when `generation`
differs, an episode likewise. These qualification and `p₀` rules apply only to a handed-off
account; when the performing device holds the account itself, its own reconcile head decides (below). Its
`set:deliver` row gets `since` = the earliest `disconnectedAt` of its handoffs; every other account's gets the switch's stamp. When the performing device itself has the account connected at
the switch, an event is settled iff it is in the device's own acknowledgements, or it is at or below the handoff's
`head`, not in its `unsettled`, and the handoff's `generation` equals the switch's. Two handoffs naming different
accounts of one provider: the one with the later `disconnectedAt` is connected again; the other stays
disconnected. **After the switch the handing-off device connects the account again itself**, writing the
connection into `set:trackers` by compare-and-set, after reading the log to its head, from its kept tokens with its
original connect stamp — the one case a device writes a connection a person did not — but only if `set:trackers`
holds no non-null connection for that provider, no connection of that account stamped later than
`disconnectedAt`, and no other handoff for that provider has a later `disconnectedAt`. Every connection write, a
person's included, is a compare-and-set on `set:trackers`; a device that reads two non-null connections for one
provider uses the later-stamped one and writes the other `null` by compare-and-set, whether or not it holds any
lease; otherwise, or if it has no tokens (Simkl's was
nulled in v2's `set:keys`), the account stays disconnected, other clients show it as awaiting a person's reconnect,
and delivery for it waits until then. Every change made in between is delivered. Any other
client only shows that the switch is offered.

**Sequence.**
1. Open the rewrite (below) and take `base`. From here until commit or abort the performing device **sends no
   tracker command and no scrobble `stop`**, so every v2 stop precedes `seedBound` on the performer's clock.
2. Read the log through `base`, and derive the v3 form from **every row through `base`**.
3. Stage it, commit with that `base`. On `409`, abort, and start over from 1.

**The v3 form** is, from §8's reading:
- a `wat` row per block with a claim (§8 fold);
- every `rec` row merged with its title events;
- every row it does not rewrite — `set` rows other than `set:tracker-event:*`, and rows of any kind it does not
  know — **staged with `k` and `v` unchanged**, except tracker tokens moved from `set:keys` to `set:trackers`. Each
  connection is written with the stamp of the setting it came from (`set:keys`), or the device's local connect
  stamp for Trakt, never a fresh one; a re-switch (§10) takes credentials only from `set:trackers` versions it holds
  and ignores `set:keys`;
- receipts (§6), seeded per account from the performing device's settlement state. A `tracker-event` in the log
  through `base` is **settled** for an account only if its id is among this device's per-account acknowledgements
  for that account, or den-core `commands(event, event.after)` returns `[]` (the change never needed a command). An
  event whose command was superseded by a later change is **unsettled**, and so is one for which `commands` returns
  an error (for example `invalid_reaction` for `seen`). An outbox skip, an
  id-only receipt or a completed flag from a pre-ready build never counts as settlement. A target whose row changed
  after the last full reconcile's read head is seeded from the row as that reconcile read it (unsettled). Exception: when such a target is not
  unsettled (no unsettled event and no unsent command for it in the outbox) and its current register's `cleared`
  viewing is at or above the `p` of that reconcile-read value (a replay started at 0, which v2 writes with no event),
  it is seeded as `["u", <cleared viewing + 1>, null, <the cleared stamp>, [1, n, <performer>]]`, so no un-watch is
  delivered for it and every viewing above the `cleared` one is checked by §6 Against a `u` receipt (with §9's
  window). v2 removed a rating for `seen` only for a TV's own action, never for another device's (`invalid_reaction`
  in `commands`); such changes since v2 are delivered as removals, acknowledged where already absent, and held while
  the remote rating is not the entry's acknowledged value (§6 Ratings) and its time is unknown or later
  (§6 Ratings) (**known limit**: Den then shows `seen`
  against that tracker rating until the person resolves it). v3-ready
  builds record acknowledgement per account — for a rating, with the remote rating value that acknowledged it, at
  that build's first reconcile or since — and record orphan retirement separately. The switch seeds, for the
  reaction it seeds, the remote value recorded with the latest settlement of that rating target for that account
  (none when that settlement was a Send, or settled a different reaction) as the rating entry's fourth element
  (§6 Ratings) whenever it differs from the rating Den sends for the seeded reaction; a handed-off account's
  entries get the handoff's `ratings` value only when it maps (§6 Ratings) to the seeded reaction and differs from
  the rating Den sends for it (with `"all"`, none, and §6 Ratings' seed-epoch
  re-read applies); a rating target with no recorded value is seeded without one, so the first reconcile of a ready
  build re-reads every rating it catches up against the snapshot to record it (an orphan-retired push was
  never delivered, so it is unsettled). Where settlement is unknown, the event is unsettled — `decide` against the
  snapshot makes that safe. A target is **unsettled** if any of its events is, or if any unsent command for it is in
  the outbox, journalled or not. An unsettled target is seeded with the value derived from its earliest unsettled
  event's `before` alone — folded through §8, then read through §5 and §6 — with the title's resets whose `t` is
  less than that event's `at`; an absent `before` seeds the default (`n`, `out`, `null`). It is never seeded as "no receipt". Every other target is seeded with its current value, except that for an account the performing device
  holds itself, a `watched` value in p > 0 — current, or as the last full reconcile read it when its viewing is the
  current viewing — whose current viewing has no settled event and whose current viewing's
  play is later than the account's connection stamp (the earliest `set:keys` `simkl` setting stamp the device has
  seen for that account id; for Trakt, the device's local connect stamp, which a ready build holding a Trakt
  connection with none records as its own clock at its first reconcile) is seeded by the
  qualifying-target shapes below (bound: the performing device's clock at its read of `base`), so a rewatch whose
  best-effort scrobble failed, or that was finished where nothing delivers, is decided by §6 Rewatch with the
  windows, and a scrobble that did land acknowledges it; and the performing device sends every kept `stop` whose
  batch has succeeded before it opens the rewrite. **Known limit**: for an account the performing device holds, a
  viewing below the current one whose best-effort scrobble failed is seeded as delivered (as v2). A value of class `n` (a
  film's `none`) in viewing p > 0 — current, derived from a `before`, or as the last full reconcile read it — is
  instead seeded by the qualifying-target
  shapes of the handoff paragraph: `w` at p₀ when a lower viewing has a visible Den play (bound: `disconnectedAt.t`
  for a handed-off account, else the performing device's clock at its read of `base`); else `u` at p when `cleared`
  is `[p − 1, …]`; else `w` at p − 1 with the covering-reset floor; `n` only when p = 0. So a replay in progress at
  the switch is delivered as a rewatch when it finishes. Unsettled
  removals are subject to `removals` (more than 20 pending → held). Receipts the device already holds from an
  earlier switch are merged in. Seeded receipts carry settle order `[1, n, <performing device id>]`. Each `set:deliver` row gets `since` =
  the switch's stamp (if it has none; for a handed-off account, the earliest `disconnectedAt` of its handoffs) and
  `lease` = `["", <1 + the greatest settle epoch in the merged receipts>]`.
  Seeding also writes each account's credentials (§6).

It holds no `ep` or `set:tracker-event:*` row. §12 pins that it derives §8's state for every coordinate and title.
If the v3 form would exceed den-edge's 50 000-row limit or 32 MiB charged (`2(k + v) + fragment + 192` per row),
`v3_form` fails: the switch is not performed and
the offering client shows why.

**den-edge** stores beside each library a **wire minimum** (2 by default, inside the library's own store, so backed
up and restored with the rows) and a **library generation**: a random id that den-edge replaces on every commit,
and whenever it loads a library whose `(generation, head)` differs from the pair recorded for it in a store-level
index that backups leave out — which catches a whole store restored, a lost store, and one library restored alone. `x-den-generation` is `<store generation>.<library generation>`; a library that does not
exist has generation `0`, returned on its 404. It adds:

1. `POST /lib/{id}/rewrite` → `{"rewrite": <r>, "base": <head>}`: opens a staging area, kept on disk (it survives a
   restart). A second open while one exists gets `409 rewrite_in_progress`. From then on the library is **fenced**:
   every write without this rewrite id — batches, `PUT member`, `DELETE` — gets `409 {"error":
   "rewrite_in_progress"}` with `Retry-After`, until commit, abort, or 5 minutes without a staging request.
2. `POST /lib/{id}/rewrite/{r}/rows {writes: [{k, v}]}`: stages rows under the usual count limit and the 2 MiB body
   limit (the client chunks by bytes), counted against a separate allowance equal to den-edge's stored-library cap
  (32 MiB, not the 8 MiB in-memory cap of a legacy log), so a full
   library can still switch.
3. `POST /lib/{id}/rewrite/{r}/commit {base, wireMin}`: in one transaction, only if the head is still `base`,
   replaces the rows with the staged ones, keeps the library's token and member hashes, raises the wire minimum to
   `wireMin` (never lowers it; never above the request's `x-den-wire`) and replaces the library generation.
   Sequence numbers continue above the old head, so any stale `base` conflicts. Otherwise `409 {"head"}` and nothing
   changes.
4. `DELETE /lib/{id}/rewrite/{r}`: aborts and lifts the fence.

The same rewrite, with an unchanged `wireMin`, compacts a v3 library later; its v3 form is the library's rows with
any stray `ep` or `tracker-event` row folded through §8.

## 10. Clients around the switch

- **Headers.** Every request from a v3-capable client to `/lib/{id}/…` carries `x-den-wire: 3` and
  `x-den-generation` (also before the switch; `0` when creating a library). Every `/lib` response carries
  `x-den-wire-min`, `x-den-generation` and `Cache-Control: no-store`. den-edge's CORS allows and exposes them.
- **Generations.** den-edge answers any write — batch, member, DELETE, rewrite — from a client that sends
  `x-den-wire` whose `x-den-generation` is missing or differs from the library's with `409 {"error":
  "generation_changed"}`. Generations compare for equality. A batch's rows and its `x-den-generation` come from the
  same read; a batch built under one generation is never sent under another.
- **Refusal.** With a wire minimum of 3, den-edge answers every `/lib/{id}` request without `x-den-wire ≥ 3` —
  batches, member writes and `DELETE /lib/{id}` included — with `426 {"error": "upgrade_required", "min": 3}`. The
  single exemption: `GET /lib/{id}/changes?since=0&limit=1` is answered normally (the lowest row, `head`,
  `generation`), because a v2 TV's routes home check opens that row to keep its LAN routes.
- **Refused writes are kept.** A v3-capable client keeps every write refused with `409 rewrite_in_progress`,
  `409 generation_changed` or `426` durably — playback progress, settings and dismissals as well as actions — and
  sends it after `Retry-After`, through write-back when the generation changed.
- **New libraries.** A library's first batch carries `x-den-wire-min`; den-edge sets the minimum to the greatest of
  that and the minimum of the library named in `x-den-library-member`. A first batch whose `x-den-wire` is below
  that minimum gets `426`. A key reset whose `DELETE` of the old library gets `generation_changed` re-copies first.
- **What shipped v2 builds do on 426**, stated so nobody expects more: a v2 TV logs it and stops syncing, keeping its
  local state and outbox, and may keep delivering from that outbox. A v2 web page keeps showing its kept copy and
  keeps unsent **actions** (watch marks, list, rating); playback progress and settings it saves after the switch are
  lost. Neither says "update"; a v3 client lists every device below format 3 as needing one. A cached v2 web shell is
  replaced on the next visit, when its service worker fetches the current build. **Known exception to Guarantee 3**:
  playback progress and settings saved by such a stale v2 shell during that one visit are lost.
- **A minimum never falls back.** A v3 client remembers the highest `x-den-wire-min` it has seen per library id. If
  a response shows less (a store restored from an older backup), it switches again on what the restored log holds
  before writing anything else. After the first switch the v2 outbox is no longer the authority, so **any** v3
  client may perform this one, seeding receipts from those it holds merged with the restored log's, and merging the
  `set:deliver` rows it holds (`since` to the earlier), with settle order epoch `0` for what it seeds; a client that
  holds no receipts for an account seeds that account's targets as unsettled, with default values (`n`, `out`,
  `null`) — never "no receipt". A v3 client that follows a key reset or link away from a library it has seen at
  minimum 3 treats a lower minimum on the new library the same way, as a restore.
- **The v2 outbox stops.** On the switch's generation change, a v3 client stops its v2 outbox without sending
  anything more from it, and converts its entries through §8, where they become pending targets decided by §6.
- **Known exception to Guarantee 7**: a v2 TV that scrobbled a play after the switch, then updates and writes that
  viewing back, has it delivered again with Den's watched-at, which is not the scrobble's time, so Trakt can show it
  twice. Accepted because the shipped v2 journal does not record scrobble-stop times, so nothing can tell that play
  apart from a genuine rewatch.
- **Ready builds and `stop`.** A ready build keeps a scrobble `stop` at ≥ 80 % durably with the batch carrying that
  viewing's progress ≥ 0.8, and sends it once that batch succeeds under the generation it was built for, as one
  request with a total timeout of 60 s that is never retried, and only if a read of the library completed less than
  60 s before the send shows that same generation and a wire minimum below 3, both measured on the clock §6 Holding
  names (one that counts sleep; in a browser the greater of the elapsed `performance.now` and `Date.now`, a backwards
  `Date.now` counting as expired); the 1-hour and 24-hour limits use the same clock, and a stop kept across a restart
  of that clock is dropped (v3 or the window decides that viewing later); a stop is kept with the boot identity it
  was measured under (on Apple platforms `kern.boottime`, on Linux `/proc/sys/kernel/random/boot_id`, in a browser
  the page load, so nothing survives a reload), and one whose boot identity differs is that restart (a stepped wall
  clock can shift `kern.boottime`; that only drops a stop). Since a device takes a lease only after
  10 minutes of observation following a generation change (§6 Taking), every such stop lands before any v3 delivery.
  A stop whose request fails, or that the device has not sent within 1 hour of the batch's success, or whose batch
  has not succeeded within 24 hours, is dropped (and `pause` sent if the player is still open). `rewrite_in_progress` and transient failures of the batch keep it waiting (so an
  aborted switch still delivers it); it is dropped on `426` and on any `generation_changed` (a restore to a backup
  taken before the switch reads the same minimum as one before any switch, so the two cannot be told apart; the
  dropped stop is a v2-equivalent failed scrobble, and the viewing is delivered by catch-up or v3 later). den-edge
  also records in its (generation, head) index the highest wire minimum ever committed for each library id and
  answers `x-den-wire-min` as the greater of it and the stored minimum, and a v3 client that reads minimum 3 on a log
  holding `ep` or `tracker-event` rows switches again as after a lower minimum (§10). So
  any ready build's stop lands within an hour of a batch at or below `base`, inside the window end. The first switch's `seedBound` is the greater of the
  performing device's clock at its read of `base` and the greatest play watchedAt in the log through `base` that is
  not more than a day ahead of that clock, so a batch from a clock running ahead is still inside the window.
- **Known limit (§7 imports):** a playback write from a device that has not yet seen an import merges as progress
  plus `imported`, so the episode reads in progress, not watched, until it is finished. Clients still converge.
- **After a switch or a restore** (a changed generation) on a library whose wire minimum is, or was seen at, 3, every
  client forgets its head and bases, reads from 0, and writes back what it holds that the new log lacks — **in v3
  form only** (a ready build on a library it has only seen below 3 writes back in v2 form, v2 §2), including **every receipt it holds**,
  intents (`sending`) included (merged per key by settle order, §6), never a `lease`. It converts any `ep` rows, v1 events and kept unsent work it
  holds (including a v2 build's kept journal, once that device updates) through §8 into registers first. Its
  converted changes are pending targets (§6).
- **No v2 rows in a v3 library.** A v3 client uploads no `ep` or `tracker-event` row on any path — write-back,
  imports, linking a local library, recovery. A v3 reader that finds one folds it through §8 into registers and
  writes those back before any compaction drops it.

## 11. den-core

The rules live in den-sync so both clients share them:

- `name`, `merge` and `newest` accept `wat` and `snt` rows (§3, §6, §13); `later` and `merge` break ties by JCS.
- `episode_state`, `film_state`: row + resets + clock → watched, resume, current viewing, visible plays, first play,
  watched-at (§5).
- `register_write`: action + current register + covering resets + clock → the register to write (§7). `import_write`: register + import item
  → the register, or nothing (§7).
- `pending_targets`: state + receipts + `since` → commands (§6); `decide` gains the `rewatch` input with the
  remote's play times, the un-watch-then-re-mark order and the `removals` latch, and acknowledges a `baseline` rating by any remote
  rating entry, valued or not (today it needs a value; a ready build needs this den-core change), and acknowledges a
  rating whose remote value maps to the same reaction (§6), and takes a rating's remote time as 1 when it is unknown
  and equals the entry's acknowledged value (§6 Ratings); `settle`: outcome + built-from value
  → the receipt, or nothing; `lease`: row + observation + clocks → take, renew, send, wait, stop or release.
- `v2_reading`: v2 rows → §8's state; `v3_form`: every row through `base` + settlement state → the switch's rows
  (§9); `write_back`: held state + new log → the v3 rows to write after a generation change (§10).
- `switch_ready`: devices row + outbox summary → offered, performable, or neither (§9).

## 12. Vectors the implementation MUST pin

- Names and keys: `(1, 31)` → block 0, `(1, 32)` → block 1, season 0, a film `wat:movie:550:0:0`; keys `"01"`,
  `"-1"`, `"32"` in block 0 and `"100000"` ignored when deriving and kept through a merge; `d` other than `""`,
  `"local"` or 16 hex refused by writers; receipt row names from account ids and SHA-256 shards.
- Stamps: `[0, 0, "a1b2c3d4e5f60718"]` and `[0, 0, "local"]` are timeless; a future winner derives as timeless but
  merges as stored; a future play is not visible; equal stamps break by JCS bytes; a future reset is not a floor.
- Merge laws for `wat` and `snt` over random triples, including 30 plays, the same imported play from two sources
  (seconds and ms), duplicate viewings with different times, `cleared` changing in one part, and receipts settled
  on two devices with skewed clocks (settle-stamp order).
- Deriving: resets hiding progress, resume, imported state and plays; an import after a reset visible; a stamp
  issued after a reset shows past it; current viewing under a hidden progress; first play by time with imported
  plays; film watched-at before its play lands.
- Writing: mark watched after a reset writes viewing + 1 with a play; replay starts viewing + 1; un-watch clears the
  viewing before the bump with a stamp.
- Imports: no claim → written; real progress, in progress or a newer reset → nothing; never a `progress`; a film
  import `[0, <latest play ms>, ""]` loses to any real status; film import and watchlist add in both orders and
  merged agree; a removal stamped above the field's `c`; one account keeping a title another dropped; an unreadable
  account blocking removals; Simkl's missing time as `c` = 1 and no rewrite of an equal value.
- Rev-12/13 rules: an intent ignored for a different `p`; a settle based on the intent's seq; Trakt sending one play
  per Den viewing; an imported episode or film rewatched in Den delivered; an unverified list add re-settled and its
  epoch removed; a handoff with a mismatched `generation`, with `"all"`, and with a playback-finished watch after
  `head`; a reconnect refused when the provider already has another account.
- Rev-15 rules: mark watched on an imported or watched episode writes nothing; a scrobbled current rewatch acknowledged
  on Trakt by a play in [W, disconnectedAt]; a first watch finished on another device after `head` delivered; a held
  catch-up list add at a handoff staying pending; a late offline episode finish above `head` delivered; a film rated after `head` not re-pushed; a
  tracker watchlist removal of a watched film keeping it watched; two non-null connections resolved by any reader.
- Rev-14 rules: a Simkl import then catch-up sends nothing; one watch imported from both trackers at different
  seconds sends nothing; a Trakt play with non-zero milliseconds is acknowledged; a handoff seeding `w` at `p₀` so a
  post-`head` rewatch is sent; a rating-only edit not qualifying a film; a held unverified entry staying unverified;
  two handoffs reconnecting one provider.
- Rev-19/20 rules: a baseline rating acknowledged by a valueless remote entry; a ready build's first reconcile
  re-deciding an inherited catch-up id; an old play before the floor or before `W` − 1 day not acknowledging a
  scrobbled current viewing; one window play acknowledging only one viewing; a post-`head` list or rating change
  delivered to a handed-off account as `baseline`; an inherited id-only event receipt counted undelivered in a
  handoff.
- Rev-21 onward: the imported-first step against a null-watched-at `n` and with no receipt, settling `n` at `p` −1,
  not running again, and the Den plays following as no-receipt rules; `seedBound` read by a non-performing device
  after a receipt rewrite; a re-switch not moving `seedBound` later; the replay-at-0 `u` seed; the Simkl no-receipt
  gate and its catch-up; an imported play Den sent never acknowledging a separate Den viewing in the window, and a
  v2 scrobble pulled back as an imported play still acknowledging its viewing; a stop within the clock skew before
  `base` acknowledged by the window end; a Simkl rating with no rated time that still maps to the receipt letting a
  Den change through, and a same-bucket site edit (6 against 7) held; a `[-1, …]` intent element surviving a later
  intent write and a `w` settle; an imported-first intent with no receipt written as `n` at −1; a stop kept through
  an aborted switch and sent after; a stop not sent within an hour dropped; an acknowledgement of 8 against `love`
  recorded, then a Den `like` sent; a viewing seeded at 0.85 and finished a week after the switch acknowledged by its
  pre-switch stop; a switch seeding a Simkl-derived rating's recorded value (8 against `love`) so a later Den `like`
  is sent; a kept stop not sent once a read shows the new generation or after 60 s; a pre-connect rewatch not
  re-decided at the switch; a day-only import written at UTC midnight and hidden by a later same-day reset; a
  handed-off Simkl rating of 8 re-read and recorded before a Den `like` is decided; a Trakt rating sent without
  `rated_at` not holding a later Den change; a fresh browser waiting 10 minutes before taking the seeded lease; a
  rewatch in progress at the last full reconcile, finished with a failed scrobble before the switch, delivered after
  it, and the same with the reconcile reading the finished viewing and a later write in it; a v2 Netflix row
  re-imported after the switch writing no play; a pulled-back v2 Simkl scrobble writing no play; a pending
  seed-epoch Trakt rating with a timeless receipt and a newer same-bucket site edit held, not sent; consecutive-day
  imports written in either order both keeping their plays; a Trakt stop at 80–95 % before `W` not written, and
  still acknowledging its viewing, before and after that viewing settles; a handoff too large for `set:devices`
  retried with `"all"` before its account is disconnected; a newest-first CSV with the same episode on D and D + 1 writing both plays; a
  handed-off Simkl 8 against `love`, changed to `like` before the first pass, sent; a browser reloaded mid-observation
  waiting a full 10 minutes again; a stop kept across a reboot dropped.
- Delivery: pending by value (an older-stamped winner on viewing); a reset delivering an un-watch for real and for
  imported episodes, settling without oscillating; a series watchlist add; commands built from values with `at` and
  `watched_at`; each `decide` outcome's receipt (sent then changed → built-from value); no receipt → additive unless
  after `since`; reconnect keeps `since`, two `since` merge to the earlier; every row of the command table; a replay
  from 0 delivers nothing; `→ none` keeps the receipt and a later rewatch is still sent; a regression after restore
  is not pending; rewatch sent once when repeated, including with a lesser merged watched-at; un-watch then re-mark
  in two passes with a lagging snapshot; `not_found` final; removals latch, approval and re-arming; lease take (epoch
  + 1), renewal, conflict, sleep past 2 minutes on each clock, browser clock stepping back, expiry by observation,
  release; a lapsed holder's settle discarded; settle order beating a far-future clock; default receipts surviving a
  write-back; token refresh by CAS with an unrelated conflict.
- Switch: `v3_form` derives §8's state for every coordinate and title (including timeless-over-real in viewing 0, a
  viewing-0 value-0 claim leaving imported plays visible, a future-stamped claim kept as stored, and title events
  repairing a `rec`); unknown rows staged unchanged; seeding: a web-written first event pending, a held baseline
  command pending, an orphan-retired push pending, unknown settlement pending, a before-value under a reset, 21
  unsettled removals held; a TV unseen for 200 days blocks the offer; a library restored alone gets a new
  generation; any v3 client re-switches after a restore; fence refuses batch, member and
  delete; commit refused on a moved head; generation replaced on commit and restore; token and members kept; `426`
  on batch, member, delete and a low-wire first batch, and the home-check exemption; `generation_changed` on a
  missing or stale generation; a new library created with generation `0`; `wireMin` never lowered or raised past
  `x-den-wire`; a restored lower minimum switched again with receipts written back; `write_back` converts a kept v1
  journal, emits no `ep`, event or `lease`.

## 13. Versions and den-edge

- `wat` and `snt` rows are `schema: 3`. `rec` and `set` rows stay `schema: 2`. v3 adds no field to a `rec` row: v2
  carries a newer version's unknown fields wholesale (v2 §5), which would let an old write roll a new field back.
  `<d>.format`, `set:trackers` and `set:deliver:*` are settings values, which v2 clients merge per setting and keep.
- v2 clients never see `wat` or `snt` rows: before the switch none exist, after it they are refused.
- den-edge adds, and interprets no row: the per-library wire minimum and generation, `x-den-wire`,
  `x-den-wire-min` and `x-den-generation` with CORS and `no-store`, the 426 and its one exemption,
  `generation_changed`, generation `0` on a 404, and the fenced rewrite (§9, §10).

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
