# Play results, v1

What a TV says back about a Play on TV request it took (oxyc/den#476): that it is preparing the source, that the
player started, or why it did not. Every requester — Den Web, a native sender, an assistant through den-mcp — shows
the same outcome from the same evidence, and nobody reports "Playing" from anything but the TV's player.

This is additive. Requests are unchanged: a household `play` (inbox-v1 §5) and an assistant `play` / `play_trailer`
(assistant-v1 §16) are sent, sealed, freshness-checked, replay-checked and acknowledged exactly as before. A receiver
that knows nothing of results sends none, and its requesters keep what they had (§6).

[`../vectors/play-results-v1.json`](../vectors/play-results-v1.json) pins every byte below.

Words: *MUST* is a rule a party breaks at the cost of a requester's truth or the household's privacy; *should* is
advice.

## 1. The result secret

Every request already carries 16 random bytes that only its sender and its receiver read, inside its seal:

| Request | Its id |
|---|---|
| Household `play` (inbox-v1 §1) | the envelope's `id` |
| Assistant `play` / `play_trailer` (assistant-v1 §4) | the message's `id` |

Both are 32 lowercase hex characters. Their 16 bytes are the request's **result secret**; den-edge never sees them (it
stores the request sealed), so nothing below needs a new key, a new message member or a new signed field.

```
salt    = UTF-8("den/play-result/v1")
address = hex(HKDF-SHA256(ikm = id (16 bytes), salt, info = UTF-8("address"), L = 16))      (32 hex characters)
key     = HKDF-SHA256(ikm = id (16 bytes), salt, info = UTF-8("key"), L = 32)
```

- The **address** names the request's results at den-edge. It says nothing of the request, its queue entry or its
  sender, and two requests never share one.
- The **key** seals them. A result opened under another request's key does not open, so a result can never reach,
  confirm or fail any request but its own — whichever TV, profile, lane or library it comes from or is shown in.
- A retry is a new request with a new id, so it has a new address: an old request's results never touch it.

## 2. A result

The plaintext is the UTF-8 JSON object

```json
{"at":1789000003000,"state":"preparing","v":1}
{"at":1789000011000,"state":"playing","v":1}
{"at":1789000009000,"reason":"no_source","state":"failed","v":1}
```

padded with spaces (0x20) after the closing brace to exactly **128 bytes**, so every result is the same size.

| Member | Value |
|---|---|
| `v` | `1` |
| `state` | `preparing`, `playing` or `failed` (§3) |
| `reason` | only with `failed`: one of §3's reasons |
| `at` | when the receiver observed it, its clock, Unix ms, an integer |

```
nonce  = 12 random bytes
aad    = UTF-8("den/play-result/v1") ‖ 0x00 ‖ UTF-8(address)
sealed = base64url(nonce (12) ‖ AES-256-GCM(key, nonce, aad, plaintext (128)) ‖ tag (16))   (156 bytes, 208 characters)
```

A reader MUST drop a result that does not open, whose plaintext is not a JSON object with `v` 1 and an integer `at`,
or whose `state` it does not know. JSON allows the trailing spaces, so a reader parses the plaintext as it is. It MUST
ignore members it does not know, and read a `failed` with a `reason` it does not know as `failed`.

## 3. States and the evidence for each

| `state` | Means | The evidence, and nothing less |
|---|---|---|
| `preparing` | The TV took the request and is finding a source for it | The receiver admitted it: opened, fresh, not a replay, its policy checks passed, and it is handing the request to its play path. |
| `playing` | The TV's player started this request | The player made for this request reports media playing (the media element's `playing` event, AVPlayer's or the engine's first rendered frame). |
| `failed` | It will not play, and why | One of the reasons below. |

| `reason` | When |
|---|---|
| `not_allowed` | A policy on the receiver refused it: a content limit, the active profile, a parental check. |
| `no_source` | No playable source was found for it. |
| `no_trailer` | A `play_trailer` for a title with no trailer. |
| `expired` | It reached the receiver after its play window (inbox-v1 §5's two minutes, assistant-v1 §16's 120 s or `awakeSince`). |
| `superseded` | A newer play replaced it on that TV before it started. |
| `failed` | Anything else: the player gave up, no page could start it, the source failed after it was chosen. |

These are **not** evidence of `playing`, and a receiver MUST NOT send `playing` for them: a queue append or
acknowledgement, `/inbox/pending` answering `false`, a wake controller's stage (wake-controller-v1 §6, `receiver-ready`
included), a source lookup succeeding, an HLS or media request, routing to the player (`Router.play`, a page's play
callback), or progress of the same title written by any device. Playback receipts (playback-receipts-v1) stay what
they are: terminal progress, never a request's result.

### Order

- A receiver sends `preparing` at most once, then a terminal state. It may send `playing` after `failed` when the
  same request recovers (another source chosen in its own player); it sends nothing after `playing`.
- A requester shows the **highest** state it holds by rank, `playing` (3) over `failed` (2) over `preparing` (1),
  whatever order they arrived in. So a lost, duplicated, replayed or reordered result never moves a request back:
  `playing` is final, and `failed` is final unless that request's own `playing` follows.
- Among results of one rank the latest `at` is the one shown (its `reason`, for `failed`).

### What only the requester knows

A receiver cannot speak before it has woken and opened the request, so the requester adds states of its own and MUST
present them as its own, never as the TV's:

| Requester state | When |
|---|---|
| `sent` | The append succeeded; nothing more is known. |
| `waking` | The target was not listening or online when sent (inbox-v1 §3 `/inbox/listening`, assistant-v1 §16 `online`) and is wakeable (wake-controller-v1 §7), or — for a household sender with no wake view — simply not listening. |
| `taken` | The queue says a receiver took it (`/inbox/pending` `false`, `GET /assistant/request` `taken`) and no result has come. |
| `unconfirmed` | Its wait ended with no terminal result. Not a failure: the TV may still play it. |

## 4. Receiver

A receiver that sends results advertises it (§6) and, for every request it takes:

- sends `preparing` when it admits the request, then `playing` or `failed` as §3 says, each sealed with a fresh nonce
  and posted to den-edge (§5) with its household member proof;
- sends `failed` `expired` for a household play that was stale when drained, and `failed` `superseded` for each fresh
  play a newer one in the same drain replaced;
- binds `playing` to the player it made for that request: a player already showing something else, or one made for a
  later request, is not this request's evidence;
- posts best effort: a failed post is not retried past the request's play window and never changes playback.

An assistant request that `assistant_open` **rejects** has no id the receiver can trust, so it gets no result; its
requester learns it was taken, and that no result followed (§7).

## 5. den-edge

den-edge stores results opaque, in memory, by address. It learns how many results an address got and when, never
what they say, for which request or from which queue.

Every answer is JSON; an error is `{"error": "<code>"}`.

**`POST /play/result`** — member (`x-den-library-member`, any library's). Body `{"address": "<32 hex>", "sealed":
"<base64url>"}`, at most 1 KiB. Stores it: `204`.
- `400 invalid_address` when `address` is not 32 lowercase hex.
- `400 invalid_sealed` when `sealed` is not unpadded base64url, is over **512 characters**, or decodes to 29 bytes or
  fewer (a nonce, a tag and one byte).
- `409 results_full` when the address already holds **4**.
- `429 rate_limited` (with `Retry-After`) past **120 a minute** per library.
- `403 not_a_member` without a member proof.

**`GET /play/result/{address}[?after=N&wait=S]`** — member, or Bearer with assistant-v1's write scope (§9). `200
{"results": ["<sealed>", …]}`: every result stored for the address, in the order they arrived; an empty list for an
address that has none (den-edge cannot tell an unknown address from one not answered yet). With `wait` (seconds, at
most 25) den-edge holds the answer until the address holds more than `after` results (default 0) or `S` passes, as
inbox-v1 §3 holds a drain: at most two held per client address, an early answer is possible, so a requester MUST NOT
ask again straight after one that brought nothing new.
- `400 invalid_address` as above.

Limits: an address is kept **10 minutes** from its first result; den-edge keeps at most **4,096** addresses in all,
dropping the oldest first. A restart loses them, which a requester reads as `unconfirmed`.

## 6. Capability and older clients

| Side | Advertises results by | Read as |
|---|---|---|
| A TV's household queue | `<d>.control` (library-v2 §3) `{"int": 2}` or more | household transport (inbox-v1 §5) still at `≥ 1`; results at `≥ 2` |
| A TV's assistant lane | `"results": 1` in its lane drain's body, beside `name` (assistant-v1 §16) | `GET /assistant/tv` lists `"results": true` for the lane; absent or older den-edge is `false` |

- A requester waits for results only from a receiver that advertises them. For one that does not, it shows its own
  states (`sent`, `waking`, `taken`) and MUST NOT call it confirmed. A sender may keep a heuristic it had before
  results for such a receiver; it MUST NOT apply it to one that advertises results.
- An old requester never asks for results; a new receiver's results simply expire unread.
- A receiver advertising results but sending none for a request within its wait is `unconfirmed`, not `failed`.

## 7. Requesters

- A requester derives the address and key from the id it put in the request, and reads `GET /play/result/{address}`
  with `wait`, one held request at a time per request it watches, for a bounded time: Den Web up to the play window
  (120 s), den-mcp a few seconds (§8). It stops at the first `playing`, and stops watching a request the person replaced
  (a newer send from the same surface), navigated away from, or whose profile or library changed.
- A requester shows §3's highest state, with words that name the TV, and for `failed` a next step:

  | Shown | Next step |
  |---|---|
  | `failed` `not_allowed` | none from here: the TV's content limit or profile refused it |
  | `failed` `no_source`, `no_trailer` | choose another title or open Den on the TV |
  | `failed` `expired`, `failed`, `unconfirmed` | try again — a **new** request, never the old one sent again |
  | `failed` `superseded` | none: the TV is playing the newer request |

- **An assistant request taken with no result.** For a lane that advertises results, `taken` with no result within
  a few seconds means the TV refused it while opening it — its connection may not play (no play record, assistant-v1
  §16), or it was too old — and the requester says that plainly, without claiming either.

## 8. den-mcp

`den_play_on_tv` and `den_play_trailer` append as before, then wait up to their bounded wait for a terminal result
on the request's address (`GET /play/result/{address}?wait=`), falling back to the request's queue state
(`GET /assistant/request/{seq}`) for what the TV has not said. Their answer's `state` is one of `playing`, `failed`
(with `reason`), `preparing`, `waking`, `taken` or `queued`, with words that say which, and `confirmed` true only for
`playing`. `den_play_status` reads the same for a request id an earlier answer gave, so a play still preparing can be
checked again without sending it twice.

den-mcp holds no library key and needs none: the request's id is the whole of what it needs to open its results.

## 9. Threat model

| Threat | What holds |
|---|---|
| **den-edge at rest** | Addresses and fixed-size sealed results, for 10 minutes, in memory. No id, key, title, reason or progress; an address is not linked to a queue entry except by timing. |
| **A live den-edge** | It may drop, delay, withhold or replay a request's results — availability, which a requester shows as `unconfirmed`; a replay or reorder cannot lower §3's highest state. It cannot forge a result or move one to another request (it has no id). It relays `/mcp` in the clear, so it sees the ids den-mcp returns, and with them could read those requests' results: no more than assistant-v1 §12 already concedes for `/mcp`. |
| **Another household member, or another TV** | Results are bound to the request's id. Any device that opened the request knows the id — the household — and only the TV that played it has the evidence. A TV den-edge delivered an assistant play to (assistant-v1 §12) reports that TV's own outcome. |
| **A stale or replayed request** | The receiver's freshness and replay checks are unchanged; a replayed request it refuses gets no new result, and its id's results are the first delivery's. |

## 10. Vectors

`../vectors/play-results-v1.json`, generated by `node tools/play-results-vectors.mjs`: an id, its address and key,
and one sealed result of each state with its nonce and padded plaintext. `tools/test_play_results.py` checks the
derivations and the plaintext rules, and that the generator reproduces the file.
