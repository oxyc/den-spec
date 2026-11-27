# Follows and updates, v1

How a profile follows a series, a film or a person, and how every client derives the same **updates** from what it
follows: "a new episode aired", "this series returns on 12 March", "a film by this director came out".
[`../vectors/follows-v1.json`](../vectors/follows-v1.json) pins the rows, the event identity and the derivation;
each client's tests load it.

This is stage 1 of den#477: follows and an in-app list of updates, reconciled whenever a client comes to the
foreground. There are no notifications, and no shelf follows (den#541 made those unsupported).

Words: *MUST* is a rule a client breaks at the cost of another client or profile; *should* is advice.

## 1. Rows

Both rows are library-v2 §3 settings rows (`kind: "set"`, stamped values, per-setting merge) in the **active
profile's** library-v4 log. They are open groups (library-v2 §3): a client manages every entry, including ones it
cannot interpret. Follows are their own row: following is not the Watchlist, a reaction or a seen mark, and none of
those writes or reads it.

Both rows are profile-scoped (profiles-v1 §2). A profile-aware household MUST NOT hold either one, so
`profile_household_write` refuses both. Visitor has no library and so cannot follow in v1.

### `set:follows`

One setting per thing followed. Its key is `<kind>:<id>`:

| kind | id |
|---|---|
| `tv` | a TMDB series id |
| `movie` | a TMDB film id |
| `person` | a TMDB person id |

`id` is canonical decimal, `1 ≤ id ≤ 2^53 − 1`. The value is `{"int": <since>}` while followed, where *since* is the
Unix ms of the follow, and `null` once unfollowed. A follow writes a new *since* each time, so following again
starts a new baseline (§3). A reader ignores, and keeps, a key or a value it does not understand.

```json
{"kind": "set", "schema": 2, "name": "follows", "values": {
  "tv:1399": {"value": {"int": 1789000000000}, "at": [1789000000000, 0, "a1b2c3d4e5f60718"]},
  "person:525": {"value": {"int": 1789100000000}, "at": [1789100000000, 0, "a1b2c3d4e5f60718"]},
  "movie:550": {"value": null, "at": [1789200000000, 0, "0f1e2d3c4b5a6978"]}
}}
```

### `set:updates`

What the profile did with an update: one setting per event id (§2), `{"string": "read"}` or
`{"string": "dismissed"}`. An event with no setting is unread; a dismissed one is not shown. `null` (unread) is
valid, though no v1 client writes it. Two devices marking the same event resolve per setting, by stamp, as every
setting does.

**Retention.** Events last at most 90 days (§3), so state about older ones is dead. A client writing the row first
drops every setting whose stamp's `t` is more than 180 days before now. A reader ignores such settings. A version
still holding one brings it back on merge, and the next write drops it again. If more than 2,000 settings remain,
the writer keeps the 2,000 with the newest stamps.

## 2. Event identity

An event's id names *what happened*, never *when*. A changed date therefore moves an event rather than adding a
second one. It is also the same on every device, which is why marking it read on one device marks it read on all.

| type | id | means |
|---|---|---|
| `episode` | `episode:tv:<series>:<season>:<episode>` | that episode has aired |
| `returns` | `returns:tv:<series>:<season>` | that season's first episode is announced for a later day (season 1: the series premieres) |
| `release` | `release:<movie\|tv>:<id>` | that title came out: a film's release, a series' first air date |

Ids are ASCII, at most 96 characters, with every number in canonical decimal.

Every event carries, beside its id and type:

- `title` (`{type, id}`), and `season` and `episode` where the id has them;
- `date`, a calendar day `YYYY-MM-DD`, and `precision`. In v1 the precision is always `day`, because a fact without
  a whole day yields no event at all (§3). A year-only announcement never becomes a release date;
- `source`, the facts' provider: `tvmaze` or `tmdb` (atlas's `airing.source`), and `tmdb` for releases;
- `observedAt`, the Unix ms at which this device received the fact. It is provenance only: it is in neither the id
  nor the order;
- `via`, the follow keys (§1) that bring it, sorted by key.

## 3. Deriving events

A client derives the profile's events from its active follows and public facts each time it reconciles. Nothing
about an event is synced but its §1 state, and den-edge never sees what is followed. Derivation is a pure function
of these inputs, so devices holding the same follows and facts list the same events.

- `today` is the viewer's calendar day. A follow's **baseline day** is the UTC calendar day of its *since*. An event
  belongs to a follow only when the event's date is **after** that baseline day. That is the whole baseline: what
  had already happened by the day of the follow is history, and none of it is listed.
- Days compare as calendar dates. "Within *n* days" counts whole days from `today`, today included.

From a followed series `tv:<id>` and its atlas `/airing` answer (`previous`, `next`, `upcoming`, `seasonPremiere`):

1. **episode.** Take the newest dated episode with `season > 0` that is out today or within the last **7** days.
   This is the Apple apps' New-episode rule, `NewEpisodePolicy.freshDays`. It is a single event per series, the
   newest, so a season released all at once is one event. A date in the future is not out, and a stale answer that
   names no recent episode lists none.
2. **returns.** If `seasonPremiere` names a season `≥ 1` whose date is **after** `today` and within **90** days, list
   `returns:tv:<id>:<season>`. Once that day comes, the premiere is an `episode` event and the `returns` one ends.

From a followed film `movie:<id>` and its TMDB release date:

3. **release.** If the date is a whole day, out today or within the last **30** days, list `release:movie:<id>`.

From a followed person `person:<id>` and their filmography (TMDB credits, cast and crew):

4. **release.** For each credited title whose own date is a whole day (a film's release date, a series' first air
   date), out today or within the last **30** days, list `release:<type>:<title id>`, unless that title is itself a
   followed series. A followed series' episodes already cover its premiere.

Then:

- One id is one event. When several follows bring it, the one event lists every follow whose baseline it passes in
  `via`, and a follow whose baseline it does not pass is left out. If none passes, there is no event. Following a
  director and their film therefore lists the film's release once. Its `date`, `source` and `observedAt` come from
  the `via` key that sorts first.
- Unfollowing removes a follow's key from every `via`, and an event left with none is gone. Following again sets a
  new baseline.
- Order: newest `date` first, then `id` by bytes.
- A fact the client could not fetch yields nothing for it. A failed fetch never turns into an event or removes the
  others. Each client should keep the facts it last fetched for as long as its existing caches would (atlas's
  `max-age`, the TMDB cache), and derive from them.
- An episode or a release having come out says nothing about whether it can be played. A client MUST NOT present an
  event as available to watch on the strength of the event alone.

**Catch-up.** Derivation holds no state, so a device that was offline, or one opened for the first time, lists what
the windows above still hold, with the profile's synced read and dismissed marks already applied. Nothing is
replayed and nothing is lost inside a window. Events older than their window are gone, which bounds the list.

## 4. Profiles and policy

A client derives only from the active profile's `set:follows` and applies only its `set:updates`. On a profile
switch it drops the listed events before the new profile's are shown. Before showing an event, a client applies the
active profile's content policy (profiles-v1 §5) to the event's title exactly as it does to any title it lists. An
event whose title the policy would not list is not shown, and is not counted as unread. Its link opens the title,
and for an `episode` event that title's episode, under the same policy as any other link.

## 5. Reconciling

A client reconciles when it comes to the foreground and when the updates list is opened. It does not reconcile on a
timer. It asks for facts through the caches it already has: one batched `/airing` request for the followed series,
one TMDB detail for each followed film, and one filmography for each followed person. It should derive only while a
profile with follows is active.

## 6. Versions

A new event type or field is additive: an older client ignores unknown setting values it cannot interpret and
keeps them (§1), and lists only the types it knows. A change to an id's spelling is a new version of this document,
because the marks in `set:updates` are keyed by it.
