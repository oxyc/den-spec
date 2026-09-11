# Library wire format, v2

How Den clients (the Apple TV app, Den Web) store a user's library in den-edge's record log
(`/lib/{id}/batch`, `/lib/{id}/changes`). den-edge stores and orders opaque rows; everything below happens in
the clients. [`../vectors/library-v2.json`](../vectors/library-v2.json) pins every derivation and
[`../vectors/merge-v2.json`](../vectors/merge-v2.json) the merge and clock rules; each client's tests load both.

Words: *MUST* is a rule a client breaks at the cost of other clients; *should* is advice.

## 1. Keys

The **library key** is 32 random bytes, generated once per library by the first TV. A client never sends it
to den-edge. Everything else is derived from it with HKDF-SHA256:

| Name | Salt | Info | Bytes | Use |
|---|---|---|---|---|
| `id` | `den/library/salt/v1` | `den/library/id/v1` | 16 | The `{id}` in `/lib/{id}/…`, lowercase hex |
| `encKey` | `den/library/v2` | `enc` | 32 | AES-256-GCM key for row values |
| `macKey` | `den/library/v2` | `mac` | 32 | HMAC-SHA256 key for row keys |
| `token` | `den/library/v2` | `token` | 32 | The `x-den-library-token` header, lowercase hex |

Salts and infos are the UTF-8 bytes of the strings shown.

**Handover (v1).** A linked device gets the library key from the TV's encrypted backup at `/sync`: the
snapshot carries it as `libraryKey`, base64. That backup is keyed off the link's inbox key, which den-edge
generates, so den-edge could read it. Issue #8's R5b replaces this with a key in the link QR's URL fragment.

## 2. Rows

Each record is one row `{k, v}`.

- **Row key** `k` = lowercase hex of HMAC-SHA256(`macKey`, *name*), where *name* is the record's UTF-8 name
  (§3). den-edge sees 64 hex characters and learns nothing about the title.
- **Row value** `v` = base64url, unpadded, of `nonce (12) ‖ ciphertext ‖ tag (16)`: AES-256-GCM with
  `encKey`, a fresh random nonce per write, and the 32 raw bytes of the HMAC as additional data. A row
  value moved to another key fails to open.
- The plaintext is a UTF-8 JSON object (§3). After opening, a reader MUST rebuild *name* from the payload's
  identity fields and drop the row if its HMAC isn't `k`.

A client writes with `POST /lib/{id}/batch {writes: [{k, base, v}]}`, where `base` is the `seq` it last read
for `k` (0 for a new row). A conflict answer carries the current row: the client opens it, merges (§5),
and writes again with the new `base`. It reads with `GET /lib/{id}/changes?since=N`, remembering the `head`.

## 3. Records

Times are Unix milliseconds (integers). A title is `{"type": "movie" | "tv", "id": <TMDB id>}`.

A **stamp** is `[t, c, d]`: Unix ms, a counter and a device id (§4). A **stamped value** is
`{"value": …, "at": <stamp>}`.

### Title — name `rec:<type>:<id>`

```json
{
  "kind": "rec", "schema": 2,
  "title": {"type": "movie", "id": 550},
  "status": {"value": "watchlist", "at": [1789000000000, 0, "a1b2c3d4e5f60718"]},
  "resume": {"value": 0.42, "at": […], "viewing": 0, "seconds": 2531.5},
  "reaction": {"value": null, "at": […]},
  "deleted": {"value": false, "at": […]},
  "dismissed": {"value": false, "at": […]},
  "episodesReset": null,
  "addedAt": 1788000000000,
  "watchedAt": null
}
```

- `status`: `none`, `watchlist`, `inProgress` or `watched`.
- `resume`: the resume fraction 0…1, which viewing it belongs to, and the absolute position in seconds
  (optional). Starting a finished title again, or un-watching it, starts a new viewing.
- `reaction`: `null`, `seen`, `dislike`, `like` or `love`.
- `deleted`: the tombstone. Rows are never removed.
- `dismissed`: taken out of Continue Watching. It holds until activity newer than its stamp (a `resume` or an
  episode's progress) brings the title back.
- `episodesReset`: a stamp, or null. For a series, every episode whose progress is older counts as
  unwatched ("Seen" turned off for the whole series).
- `addedAt`, `watchedAt`: the earliest add, and the first time it was watched (null when not watched).

### Episode — name `ep:<type>:<id>:<season>:<episode>`

```json
{
  "kind": "ep", "schema": 2,
  "title": {"type": "tv", "id": 1399}, "season": 1, "episode": 2,
  "progress": {"value": 1, "at": […], "viewing": 0, "seconds": 3000}
}
```

An episode is watched when `progress.value` ≥ 0.95 and its stamp is newer than the series' `episodesReset`.
Un-watching one episode writes value 0 in a new viewing. A watched bit learned without a time (a tracker
import) is written with the zero stamp `[0, 0, ""]`, so any real edit beats it.

### Settings — name `set:<name>`

```json
{
  "kind": "set", "schema": 2, "name": "prefs",
  "values": {
    "den.hideWatched": {"value": {"bool": true}, "at": […]},
    "den.minReleaseYear": {"value": null, "at": […]}
  }
}
```

Each setting is its own stamped value, so two devices changing different settings don't overwrite each other;
`null` is a setting that was cleared. Values are tagged: `{"bool": …}`, `{"int": …}`, `{"string": …}`,
`{"ints": […]}`, `{"strings": […]}`.

- `prefs`: the TV's synced preferences, by their `UserDefaults` keys (hidden genres and languages, Hide
  Watched, the year floor, subtitle and audio choices, …).
- `keys`: the user's own API keys — `tmdb`, `omdb`, `doesthedogdie` — sealed like every row, so they reach a
  linked device without den-edge seeing them.

A client writes only the settings it manages and keeps the others as it read them. A client that doesn't know
the `set` kind skips the row.

### What rows don't carry

No TMDB display data (title text, posters, ratings): each client fetches those from TMDB itself, so TMDB
content isn't stored on a server outside its caching terms. Nor per-device state: the chosen stream, a
series' season layout, the original language.

## 4. Stamps

Stamps order edits across devices whose clocks disagree: a hybrid logical clock. A stamp compares by `t`,
then `c`, then `d` (by bytes). Each device keeps the last stamp it issued or saw, `last`.

- **Issuing** at wall-clock time `now`: `t = max(now, last.t)`; `c = last.c + 1` if `t == last.t`, else 0;
  `d` = this device's id. Set `last` to it.
- **Seeing** a stamp `s` in a row it read: `last = max(last, s)`. So an edit made after reading a row is
  stamped later than that row, even when this device's clock is behind.

The device id is 16 random lowercase hex characters, made once per installation.

A stamp more than a day ahead of the reader's clock — a device with a wrong clock, or a key holder writing
year 2100 — would otherwise win every merge for good. A client reads it as the zero stamp: the value wins
nothing, the clock never sees it, and the next write of that row replaces it.

## 5. Merge

A merge of two versions of one row MUST give the same result in either order and in any grouping, and
merging a row with itself changes nothing. The vectors' `merge` cases check this.

- **Stamped values** (`status`, `reaction`, `deleted`, `dismissed`): the later stamp wins.
- **Resume and episode progress**: the higher `viewing` wins outright; within one viewing the higher value,
  then the later stamp, then the higher `seconds` (missing counts as −1).
- `episodesReset`: the later stamp; null is older than any stamp.
- **Settings**: per setting, the later stamp; a setting only one version has is kept.
- `addedAt`: the minimum. `watchedAt`: the minimum of the non-null values. `schema`: the maximum.
- Fields a client doesn't understand come from the version whose newest stamp is later, then the other.

## 6. Versions

- `schema` is 2. A client MUST NOT write a row whose `schema` is higher than its own; it keeps reading it.
- When writing a row back, a client MUST keep the fields it doesn't understand, so an older client can't
  erase what a newer one added.
- Actions are idempotent "set"s: add to the watchlist, set watched, set the reaction. Never a toggle, which
  flips back when replayed.
