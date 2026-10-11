# Assistant writes, v1

How an assistant (Claude, ChatGPT) connected through den-mcp changes a library — adds to or takes off the watchlist,
marks seen or unseen, rates — while no Den is open (oxyc/den#217). den-mcp **seals** a request to the library; it can
never **open** anything. Devices (the TV, Den Web) drain the requests, check them, and apply them through the same
den-core ops a person's tap uses, so tracker delivery and the removals latch (library v4 §9) apply unchanged.

A connection may also **read** (§15): devices publish a projection of the watchlist, Continue Watching and Seen
history, sealed under a key only the household and den-mcp hold. Reads are additive: every v1 grant, session, token,
claim and wrap issued before them keeps working unchanged.

[`../vectors/assistant-v1.json`](../vectors/assistant-v1.json) pins every byte below. The rules are implemented
once, in den-core: the crate `den-assistant` (keys, sealing, claims, wraps, the accept rules), which den-edge and
den-mcp depend on by git tag, and den-sync's `assistant_*` ops (§11), which the clients call.

Words: *MUST* is a rule a party breaks at the cost of the household's library or secrets; *should* is advice.

## 1. Overview

| Step | Who | What |
|---|---|---|
| Consent | Den Web, a **member** | On den-edge's consent page the member ticks "Allow changes to my library". Den Web makes sure the library has a drop-box key (§5), makes a grant key, writes the grant row (§5), then approves with the grant key (§9). If the approval does not succeed, Den Web MUST revoke the grant it wrote. |
| Token | den-edge | At code exchange it wraps the grant key under the new refresh secret (§8). Every access token for the session carries the grant key sealed to den-mcp (§7). |
| Write | den-mcp | Opens the claim, fetches the library's drop-box public key, builds, signs and seals a request (§4), appends it to the library's queue (§9). |
| Apply | the TV, Den Web | Drain the queue, open and check each request against the rows and the device's own records (§6), apply what is accepted through the tap path as of the request's time, record it as applied, acknowledge. |
| Keep | Den Web | Renews each grant while its connection stands (§5); Settings on Den Web and the TV lists every grant and revokes any of them. |

## 2. Primitives

| | |
|---|---|
| Sealing | HPKE (RFC 9180), **base mode**: KEM **X-Wing** (`0x647a`), KDF **HKDF-SHA256** (`0x0001`), AEAD **AES-256-GCM** (`0x0002`). `SealBase` / `OpenBase`, single shot. |
| X-Wing | draft-connolly-cfrg-xwing-kem (ML-KEM-768 + X25519), as HPKE carries it (draft-ietf-hpke-pq): private key 32 bytes (X-Wing's seed), public key 1,216 bytes, encapsulated key `enc` 1,120 bytes. An encapsulation takes X-Wing's 64-byte `eseed`. |
| Signatures | Ed25519 (RFC 8032). Private key: the 32-byte seed. Verification is **strict** (ed25519-dalek's `verify_strict`: canonical `S` and `R`, no small-order key or `R`). |
| Other | HKDF-SHA256 (RFC 5869), AES-256-GCM (12-byte nonce, 16-byte tag), SHA-256. |
| Text | base64url is RFC 4648 §5 **unpadded**, read strictly (no padding, no other alphabet, no bits set past the last byte). Hex is lowercase. Strings in a construction are their UTF-8 bytes. |

Implementation: den-core's `den-assistant` composes `hpke` 0.14.1 (its `XWing` KEM, over RustCrypto's `x-wing`
0.1.1), `ed25519-dalek` 3.0.0 and `aes-gcm` 0.11.1, and implements no primitive itself. Its tests check the
primitives against their own published vectors: draft-connolly-cfrg-xwing-kem's test vectors, and the HPKE-PQ
reference vectors (hpkewg/hpke-pq at `6433c8f`) for X-Wing with HKDF-SHA256, whose only published AEAD is
ChaCha20-Poly1305; the AEAD changes only the suite id the key schedule hashes.

**Randomness.** Every key seed, request id, `eseed` and nonce MUST come from the platform's CSPRNG, fresh for each
use. den-edge and den-mcp call `den-assistant`'s `*_with_rng` functions with their CSPRNG; the forms that take the
bytes exist for the vectors. den-sync takes random bytes as input (it is pure), so one input always gives one output.
Reusing an `eseed` reuses the HPKE key; reusing a wrap nonce under one key breaks AES-GCM.

## 3. Keys

| Key | Kind | Made by | Held by | Id |
|---|---|---|---|---|
| **Drop-box key**, one or more per library | X-Wing | the first device that needs one (§5) | private: only the library (`set:assistant`); public: den-edge | `kid` = hex of the first 16 bytes of SHA-256(public key) |
| **Grant key**, one per assistant connection | Ed25519 | Den Web, at consent | public: the library (`set:assistant-grants`); private: den-edge, wrapped (§8), and den-mcp, per access token (§7) | grant id = hex of the first 16 bytes of SHA-256(public key) |
| **den-mcp's token key** | X-Wing | the operator, once | private: den-mcp (`MCP_WRITE_KEY`); public: den-edge (`MCP_WRITE_PUBLIC_KEY`) | — |

- `MCP_WRITE_KEY` is the base64url of the 32-byte private key; `MCP_WRITE_PUBLIC_KEY` the base64url of its 1,216-byte
  public key (1,622 characters). Without `MCP_WRITE_PUBLIC_KEY` den-edge offers no write scope.
- The **grant blob**, the form a grant key travels in (§7, §8): the grant id's 16 bytes, then the 32-byte seed — 48
  bytes. A reader MUST check that the id is its seed's (`SHA-256(public key)[0..16]`) and refuse the blob otherwise.

## 4. A request

### Message

The message is the UTF-8 **JCS** (RFC 8785) of one object, at most **1,024 bytes**:

```json
{"args":{"episode":2,"season":1,"title":{"id":1399,"type":"tv"},"value":true},"at":1789999940000,"grant":"d7047d7faa4c6f77e1919c132bb6bd9f","id":"94619420c64002287a3ad7ecad5dfa6b","library":"4c1b7a0e9d3f2c8b5a6e1d0f7c3b9a2e","op":"seen","v":1}
```

| Member | Value |
|---|---|
| `v` | `1` |
| `library` | the library id (library v2 §1: 32 hex characters) |
| `grant` | the grant id |
| `id` | 16 random bytes, hex: the request's identity, for replay (§6) |
| `at` | when den-mcp made it, Unix ms, an integer `0 ≤ at ≤ 2^53 − 1`. It is also the time the change is applied at (§6). |
| `op` | one of the ops below |
| `args` | the op's arguments |

A title is addressed exactly as library rows key it (library v2 §3, v4 §3): `{"type": "movie" | "tv", "id": <TMDB
id>}`, the id an integer `1 ≤ id ≤ 2^53 − 1`. A season is an integer `≥ 0` (0 is specials); an episode an integer
`0 ≤ e ≤ 99999` (library v4 §3).

| `op` | `args` | Applied as |
|---|---|---|
| `watchlist_add` | `{"title"}` | the person's "Add to Watchlist" on that title |
| `watchlist_remove` | `{"title"}` | the person's "Remove from Watchlist" on that title |
| `seen` | `{"title", "value": true \| false}`, and for a series optionally `"season"`, or `"season"` and `"episode"` | the person's Seen / Unseen on that film, series, season or episode |
| `rate` | `{"title", "value": "dislike" \| "like" \| "love" \| null}` | the person's rating (the `reaction`, library v2 §3); `null` clears it |
| `play` | `{"title"}`, and for a series optionally `"season"`, or `"season"` and `"episode"` | plays it on the TV (§16) |
| `play_trailer` | `{"title"}` | plays its trailer on the TV (§16) |

A message is **valid** only if it is byte for byte the JCS of its own parse (so: no whitespace, sorted members, no
duplicate member, every number a plain integer — no fraction, exponent or minus sign), has exactly the members
above, and its `args` has exactly the members its op allows (`season` and `episode` only on a `tv` title, `episode`
only with `season`). Every string in a valid message is ASCII, so its JCS is the sorted, compact JSON any serializer
writes for it.

### Signing and sealing

```
signature = Ed25519-Sign(grant private key, UTF-8("den/assistant/sig/v1") ‖ 0x00 ‖ message)
plaintext = signature (64) ‖ message
enc, ct   = SealBase(pkR = the drop-box public key, info = UTF-8("den/assistant/v1"),
                     aad = UTF-8(library id), pt = plaintext)                        (RFC 9180 §6.1)
sealed    = base64url(enc (1,120) ‖ ct)
```

Its length is `1,120 + 64 + len(message) + 16` bytes. The vectors' 243-byte message seals to 1,443 bytes, **1,924
characters**; a 1,024-byte message, the longest, to 2,224 bytes, **2,966 characters** — under den-edge's 4,096 (§9).

den-mcp MUST build requests with `den_assistant::seal_request_with_rng` (it checks the message is valid before it
signs) and a fresh `id` (`request_id_with_rng`) for each.

## 5. Library rows

Three settings rows (library v2 §3): sealed under the library key, uncompressed (library v4 §4), each value stamped.
A client writes them by compare-and-set like any settings row, and merges them with den-core's `merge`.

### `set:assistant` — the drop-box keys

One setting per key: `dropbox.<kid>` → `{"string": "<base64url of the 32-byte private key>"}`.

- A device that needs a drop-box key and reads, at the log's head, a row with none makes one
  (`assistant_keygen_dropbox`), writes it, and only after that write has landed PUTs its public key (§9).
- Merge: by the later stamp, per setting (every setting's rule). Two devices that make one at once leave **two** keys;
  both stay and both open requests (§6), so neither device's key is ever lost.
- **den-edge holds one public key**: the one `assistant_dropbox` names, the key whose setting has the latest stamp.
  A device that reads a row whose `assistant_dropbox` answer differs from the one it last PUT for that library PUTs it
  again. den-edge converges on the merged row's key; a request sealed to the other key in the meantime still opens.
- v1 never clears or rotates a key in place; a `null` setting is not tried. A library key reset starts over (§10).

### `set:assistant-grants` — the grants

One setting per grant, keyed by grant id: `<grant id>` → `{"string": "<JCS of the grant>"}`:

```json
{"cap":3,"client":"Claude","createdAt":1789136000000,"expiresAt":1791728000000,"ops":["rate","seen","watchlist_add","watchlist_remove"],"pk":"<base64url, 32 bytes>","revokedAt":null,"v":1}
```

| Member | Value |
|---|---|
| `v` | `1` |
| `pk` | the grant's Ed25519 public key; its grant id MUST be the setting's name |
| `client` | the assistant's name as the consent page showed it: 1–80 characters, no leading or trailing whitespace, no control, bidi or zero-width character |
| `ops` | the ops allowed, a non-empty list of distinct v1 ops, sorted |
| `cap` | requests a day, `1 ≤ cap ≤ 1000` |
| `createdAt` | consent time, Unix ms |
| `expiresAt` | from this time on the grant accepts nothing (§6), Unix ms: `createdAt` + 30 days, moved by renewal |
| `revokedAt` | null, or when the grant was revoked, Unix ms |

- A grant is written once, by Den Web at consent (`assistant_keygen_grant`), **before** it approves at den-edge, so
  a request can never reach a device before its grant. If the approval fails or is abandoned, Den Web MUST revoke it.
- **Renewal.** A grant lasts 30 days, den-edge's idle limit for a session. While `GET /oauth/connections` lists the
  grant's session, Den Web renews it (`assistant_renew`: `expiresAt` = `now` + 30 days, never earlier, never
  un-revoking) — at most once a day, whenever it is open. A grant whose connection is gone simply expires.
- **Revocation** (`assistant_revoke`) is written when a person disconnects an assistant (§9 *Revoking*), from
  Settings on either client, or by a key reset (§10). **Settings on Den Web and the TV list grants from this row**
  (`assistant_grants`), not from den-edge's connections, and can revoke any of them, whatever their state.
- A value that breaks any rule above — another member, `v` other than 1, a `pk` whose id is not the setting's — is
  **malformed**: it is no grant, and a request naming it is `unknown_grant`. A newer grant version (`v` > 1) also
  reads as no grant on a v1 device, so it fails closed.
- **Merge** (den-core `merge` of a `set:assistant-grants` row), each part a join, so the merge is commutative,
  associative and idempotent:
  - The higher **rank** wins: a newer version (`v` an integer > 1) over a v1 grant over a JSON object that is
    neither over anything else. Rank is read with `revokedAt` set aside.
  - Two v1 grants keep the byte-greater JCS of every member but `revokedAt` and `expiresAt` (a grant is written
    once, so they agree), the **later `expiresAt`**, and the later stamp. Two values of another rank: the later
    stamp, then the byte-greater JCS without `revokedAt`.
  - Then the winner's `revokedAt` is the join of **both** sides': any time beats `null`, and the **earlier** time
    beats a later one. A revocation is kept even when it sits in a version that lost — a malformed one, or a v1
    version a newer one replaced. A revocation is never undone.

### `set:assistant-applied` — the requests applied

One setting per applied request, keyed by request id: `<id>` → `{"string": "<JCS of {applied, at, grant}>"}`:
`grant` the grant id, `at` the request's own `at`, `applied` when the device that applied it did (its `now`).

- Merge: by the later stamp, per setting, so the row is the union of every device's entries.
- A reader takes `applied` as at least `at`. A malformed value still names an id that was applied (it refuses a
  replay), counts toward no grant's cap, and is pruned at once.
- **Pruning.** An entry may go once **both** its `at` and its `applied` are more than **14 days** before `now` —
  twice the window in which §6 accepts a request, so a clock skewed on either side never drops an entry that still
  guards one. A device drops the entries `assistant_prune` names, from the row (written by compare-and-set, with no
  tombstone) and from its own record (§6). An entry an older copy merges back is harmless and pruned again. Pruning
  should run at most once a day.

## 6. Applying

### The device's own records

den-edge serves the rows, and a live den-edge can serve an **older** version of one — a grants row from before a
revocation, an applied row from before a request — or withhold a write. So every device MUST keep, durably and only
ever growing (until pruned, §5):

- **its revoked set**: every grant id it has seen revoked — any `revokedAt` in a grants row it read
  (`assistant_grants` answers `"state": "revoked"`), and every grant it revoked itself;
- **its applied set**: every applied entry it recorded or read, as request id → the entry's tagged value.

It passes both to every `assistant_open` (`localRevoked`, `localApplied`), which unions them with the rows. A
revocation or an applied request a device has seen therefore holds for that device whatever den-edge serves later.

### Opening and checking

den-core's `assistant_open` decides, in this order, and the first rule a request fails is its answer:

| # | Reject | When |
|---|---|---|
| 1 | `does_not_open` | `sealed` is longer than 4,096 characters, is not base64url, or opens under none of the row's drop-box keys with `aad` = this library's id |
| 2 | `malformed` | the plaintext is 64 bytes or fewer, or the message is not a JSON object with a string `grant` |
| 3 | `unknown_grant` | the grants row holds no well-formed v1 grant under that id |
| 4 | `bad_signature` | the grant's `pk` is not a valid key, or the signature does not verify under it, strictly |
| 5 | `malformed` | the message is not valid (§4) |
| 6 | `wrong_library` | the message's `library` is not this library |
| 6a | `wrong_queue` | the op belongs in the other queue than the one it was drained from (§16) |
| 7 | `revoked` | the grant has a `revokedAt`, or is in the device's revoked set — whatever the request's `at` |
| 8 | `expired` | `now` ≥ the grant's `expiresAt` |
| 9 | `from_future` | `at` > `now` + 5 minutes |
| 10 | `stale` | `at` < `now` − 7 days |
| 11 | `replay` | the applied row or the device's applied set holds `id` |
| 12 | `op_not_allowed` | the grant's `ops` does not hold `op` |
| 13 | `over_daily_cap` | the applied entries (row and device, by id) already hold `cap` or more of this grant whose `applied` is after `now` − 24 hours — one applied "in the future" by a clock ahead counts |

Only signed content is judged after rule 4: rule 2 reads the `grant` member only to find the key that checks it. The
cap counts by when requests were **applied**, not by their `at`, which the signer chooses. `now` is the device's
clock. A request exactly 7 days old, or exactly 5 minutes ahead, is accepted.

### Performing

A device **MUST** perform an accepted request through exactly the path the person's own action takes on that client
— den-core's `apply_write` (library v4 §8) and the same tracker delivery (library v4 §9), the removals latch included.
No other path exists for it.

- **As of the request's time.** The write's stamp is the accept's `stamp`, `[at, 0, <this device's stamp device id>]`,
  not a freshly issued one. `apply_write`'s replay rule (library v4 §8 *Replays write nothing*) then writes nothing to
  a field or register that already holds a later stamp: a request never overrides a change made after it was made,
  whatever order it arrives in. A request whose effect is therefore nothing (or that changes nothing anyway) is still
  accepted and recorded.
- **Recorded in the same batch.** The accept's `setting` and `value` MUST be written into `set:assistant-applied`, with
  a fresh stamp, in the **same** `/lib` batch as the request's effect, and added to the device's applied set before
  the batch is sent. A conflict re-derives and resends both together (library v4 §8 *Protocol*).

### Draining

- A device drains only after reading the library log to its head, so it holds the current drop-box keys, grants and
  applied set.
- It sorts a drain's messages by request `at` — opening each to learn it; one that does not open sorts by `seq` —
  then by `seq`, and handles them in that order against the rows as they stand.
- **Acknowledging** (§9): every message is acknowledged once handled — applied and recorded, or rejected (logged with
  its reason, never its content) — **except `from_future`**, which a device MUST NOT acknowledge and MUST log loudly:
  a clock ahead or behind by more than 5 minutes is a fault the household should see, and the request becomes good
  once the clocks agree. A device acknowledges up to the `seq` before the lowest `from_future` message (or the last it
  handled); a message it handled past that point is delivered again and the applied set refuses it as `replay`. A
  device that fails before recording a request does not acknowledge it.
- Two devices draining at once may both apply one request before either sees the other's entry. Every op is an
  idempotent set at one stamp, so the library ends the same; the applied row keeps one entry per id; the cap may be
  passed by one request per extra device.
- Den Web and the TV should drain on open, and then with `wait` (§9) while open, as for the inbox (inbox v1 §3).

## 7. The token claim

An access token for a session with the write scope carries the claim **`dw`**:

```
enc, ct = SealBase(pkR = den-mcp's token public key, info = UTF-8("den/assistant/token/v1"),
                   aad = UTF-8(the token's sub), pt = grant blob (48))
dw      = base64url(enc ‖ ct)                                       (1,184 bytes, 1,579 characters)
```

- den-edge seals a fresh claim (`seal_claim_with_rng`) for every access token it issues.
- den-mcp opens `dw` with `MCP_WRITE_KEY` and `aad` = the `sub` of the token it already verified, and checks the
  blob (§3). The scope claim is space-separated: a write session's token has `"scope": "den:search
  den:library.write"`. A token without the write scope, or whose `dw` does not open or whose blob fails, gets no
  write tool — never a fallback.
- An AI client holds the token, and so `dw`, but cannot open it.

## 8. The wrap at rest

den-edge never stores a grant key in the clear:

```
K       = HKDF-SHA256(ikm = refresh secret, salt = UTF-8(session id), info = UTF-8("den/assistant/wrap/v1"), L = 32)
wrapped = base64url(nonce (12) ‖ AES-256-GCM(K, nonce, aad = UTF-8(session id), pt = grant blob))   (102 characters)
```

- The **refresh secret** is the 32 bytes the refresh token's secret spells: a den-edge refresh token is
  `<sid>.<secret>`, `secret` the base64url of 32 random bytes. den-edge keeps only its SHA-256, so at rest it cannot
  unwrap.
- **Approval → exchange.** The grant blob lives only in the in-memory pending and code entries (as den-edge's codes
  already do) until the code is exchanged; then den-edge wraps it (`wrap_with_rng`) under the first refresh secret and
  forgets it.
- **Refresh.** den-edge unwraps with the presented refresh secret, mints the access token with a fresh `dw`, makes the
  new refresh secret, and re-wraps under it with a fresh nonce. It keeps the wrap under the replaced secret beside the
  replaced secret's hash for as long as that hash is taken again (the refresh grace), so a client retrying a lost
  answer still gets a token with `dw`; it drops it with the hash.
- A wrap that does not unwrap (a bug, a tampered store) ends the session, as a reused refresh token does.
- den-edge wipes the unwrapped key after use and never logs a wrap, a blob or `dw`.

## 9. den-edge

### OAuth

- **Scopes**: `den:search` (as today) and **`den:library.write`**, which den-edge's metadata lists in
  `scopes_supported` when `MCP_WRITE_PUBLIC_KEY` is set.
- **Asking.** An authorization request may ask for `den:library.write`; one that names no scope may be offered it too.
  `GET /oauth/request/{id}` adds `"write": true` when the write scope can be granted to this request.
- **Consent.** The consent page offers "Allow changes to my library" only to a **member** (`x-den-library-member`),
  never to a guest, and only when `write` is true. Ticked, Den Web runs §1's consent step and approves with:

  ```
  POST /oauth/request/{id}/approve
  x-den-library-member: <id>:<member>
  {"write": {"grant": "<grant id>", "key": "<base64url of the 32-byte grant seed>"}}
  ```

  den-edge checks that `grant` is the key's id; a mismatch is `400 invalid_grant_key`, a guest's proof with `write`
  is `403 write_not_for_guests`, `write` when the request cannot have the scope is `400 invalid_scope`. Without
  `write` the approval grants `den:search` alone, as today. Any answer but success — an error, a timeout, a closed
  page — and Den Web revokes the grant it wrote (§5).
- **Tokens.** A write session's token answer has `"scope": "den:search den:library.write"`, its access tokens the
  same scope and `dw` (§7). The session record keeps the grant id and the wrap (§8).
- **Connections.** `GET /oauth/connections` names each write session's `grant`. Den Web renews those grants (§5).
- **Revoking.** When Den Web disconnects a write session (`DELETE /oauth/connections/{sid}`), it MUST also write that
  grant's `revokedAt` (`assistant_revoke`) into the library and add it to its own revoked set. den-edge's deletion
  stops new appends at once; the grant row, and every device's revoked set, is what devices enforce.

### Endpoints

Every answer is JSON; an error is `{"error": "<code>"}`. A `Bearer` route takes an access token den-edge issued,
still in date, whose session still stands (as the `/mcp` relay checks today, on every call), with the write scope:
otherwise `401 invalid_token` (with `WWW-Authenticate`) or `403 insufficient_scope`. A member route takes
`x-den-library-member: <id>:<member>` and answers `403 not_a_member` when it does not prove membership.

**`PUT /assistant/dropbox`** — member. Body `{"pk": "<base64url of the 1,216-byte public key>"}`, at most 4 KiB.
Stores it for the member's library, replacing any other. `204`. `400 invalid_public_key` when `pk` is not 1,216 bytes
of base64url. `429 rate_limited` (with `Retry-After`) past 10 a minute per library.

**`GET /assistant/dropbox`** — Bearer, called by den-mcp over den.network. `200 {"library": "<id>", "pk": "<base64url>",
"kid": "<kid>"}` for the session's library. `404 no_dropbox` when the library has none.

**`POST /assistant/append`** — Bearer. Body `{"sealed": "<base64url>"}`, at most 8 KiB. Appends it to the session's
library's queue: `200 {"seq": <n>, "queued": <messages waiting>}`.
- `400 invalid_sealed` when `sealed` is not unpadded base64url, is over **4,096 characters**, or decodes to fewer
  than 1,201 bytes (`enc`, a signature, one byte and a tag).
- `409 queue_full` when **200** messages are waiting: the oldest is never dropped for a new one.
- `429 rate_limited` past **30 a minute per session** or **300 a day per library**.

**`POST /assistant/drain[?wait=S]`** — member. `200 {"messages": [{"seq": <n>, "sealed": "…"}, …]}`: every waiting
message, oldest first, without removing any. With `wait` (seconds, at most 25), den-edge holds an empty drain until a
message arrives or `S` passes, exactly as inbox v1 §3 says of its drains: at most two held per address, an early empty
answer is possible, so a client MUST NOT drain again straight after one, and its timeout must outlast `S`.

**`POST /assistant/ack`** — member. Body `{"upTo": <seq>}`. Removes every message with `seq ≤ upTo`: `200 {"removed":
<n>}`. Idempotent; `400 invalid_seq` for anything but a non-negative integer.

### The queue

- One per library: entries `{seq, sealed, receivedAt}`, `seq` increasing and never reused for that library, kept for
  **7 days** (a request older than that is `stale` anyway) and at most **200**.
- den-edge does not store anything that opens an entry. Re-delivery is always safe: the applied set (§5) dedupes.

## 10. Library key reset

A key reset (library v2 §1, v4 §12) shuts out a device that held the old key. Grants and drop-box keys MUST NOT
survive it, since the shut-out device knew them:

- The device resetting **does not copy `set:assistant`**: the new library has no drop-box key until one is made
  (§5) and PUT under the new library id. Requests sealed to the old key never open there.
- It copies `set:assistant-grants` with **every grant revoked** (`assistant_revoke` at the reset's `now`), so
  Settings still shows which assistants were connected, and it adds them to its revoked set. `set:assistant-applied`
  is copied as is. `set:assistant-read` (§15) is copied with **every read record revoked**, the same way.
- den-edge already ends every session of a library whose key is reset (the member proof behind it no longer stands);
  write and read sessions end with the rest, and den-edge drops the old library's drop-box key, queue and
  projections with it. The owner reconnects each assistant, which makes new grants and read keys.

## 11. den-core

`den-assistant` (no dependency on den-sync) is what den-edge and den-mcp use: `kem_public`, `key_id`, `grant_public`,
`grant_id`, `GrantKey` (from a seed; its blob), `message`, `sign`, `parse_message`, `check`, `prune`,
`seal_request_with_rng`, `seal_claim_with_rng`, `wrap_with_rng`, `request_id_with_rng`, `open_claim`, `wrap_key`,
`unwrap`, `b64url` / `b64url_decode`. `seal_request`, `seal_claim` and `wrap` take their random bytes as arguments, for
the vectors. For reads (§15): `GrantKey::with_read_key` / `read_key`, `seal_read_claim_with_rng`, `open_read_claim`
(→ `ReadKey { grant(), key() }`), `projection_plaintext(view)`, `projection_aad(library, grant, set, index, count)`,
`seal_projection(read key, view, random) -> SealedProjection { set, parts }`, `open_projection(read key, library,
grant, set, parts) -> view`, the limits `MAX_PART`, `PART_BYTES`, `MAX_PARTS`, `MAX_PROJECTION_PLAINTEXT`; `unwrap` returns the read key
too when the wrap holds one.

The clients call den-sync's `evaluate` (the versioned JSON envelope every op shares). Every number is an integer;
`random` is 32 bytes of hex from the platform's CSPRNG; `device` the device's 16-hex stamp device id; rows are the
opened settings rows (`{"kind": "set", "schema": 2, "name", "values"}`), and an absent or `null` row is an empty one.

| Op | Request | `ok` |
|---|---|---|
| `assistant_keygen_dropbox` | `random` | `{kid, public, setting: "dropbox.<kid>", value: {"string": <private key>}}` |
| `assistant_dropbox` | `assistant` (row) | `{kid, public}` of the key den-edge should hold, or `null` |
| `assistant_keygen_grant` | `random`, `client`, `ops`, `cap`, `now` | `{grant, public, secret, setting: <grant id>, value: {"string": <JCS grant>}}`; `secret` (base64url seed) goes to den-edge's approval and nowhere else |
| `assistant_revoke` | `grant` (id), `value` (the setting's tagged value), `now` | `{value}` with `revokedAt` set, or kept when earlier; any JSON object can be revoked |
| `assistant_renew` | `grant`, `value`, `now` | `{value}` with `expiresAt` = max(its own, `now` + 30 days); a grant or a read record (§15) |
| `assistant_grants` | `grants` (row), `read` (row, §15), `play` (row, §16), `localRevoked`, `now` | `{"grants": [{grant, client?, ops?, cap?, createdAt?, expiresAt?, revokedAt?, state}], "reads": [{grant, client?, createdAt?, expiresAt?, revokedAt?, state}], "plays": [same as reads]}`, `state` one of `active`, `expired`, `revoked`, `newer`, `malformed`; a read or play record is `revoked` also when its grant is |
| `assistant_keygen_play` | `grant`, `client`, `now` | `{setting: <grant id>, value: {"string": <JCS play record>}}` (§16) |
| `assistant_grant_key` | `random` | `{grant, public, secret}`: a grant key with no grants row setting, for a read-only connection (§15) |
| `assistant_keygen_read` | `grant`, `random`, `client`, `now` | `{key, setting: <grant id>, value: {"string": <JCS read record>}}`; `key` (base64url) goes to den-edge's approval |
| `assistant_projection` | `library`, `documents`, `layouts`, `head`, `read` (row), `grants` (row), `localRevoked`, `random`, `now` | `{publish: [{grant, set, parts}], delete: [<grant id>], digest, counts: {watchlist, continue, seen}, skipped}` (§15) |
| `assistant_open` | `library`, `device`, `sealed`, `assistant`, `grants`, `applied` (rows), `localRevoked` (grant ids), `localApplied` (request id → tagged value), `now`; for §16 `play` (row), `queue` (`"library"`, the default, or `"tv"`) and, for `"tv"`, `awakeSince` (ms) | `{"accept": {grant, id, at, op, args, stamp, setting, value}}` — `stamp` the write's stamp (§6), `setting`/`value` the applied entry to record — or `{"reject": "<reason>"}` |
| `assistant_prune` | `applied` (row), `localApplied`, `now` | `{"remove": [<request id>, …]}`, sorted, from either |
| `merge` | two `set:assistant-grants`, `set:assistant-read` or `set:assistant-play` rows | the merged row (§5, §15, §16) |

Errors: `invalid_seed`, `invalid_client`, `invalid_ops`, `invalid_cap`, `invalid_time`, `invalid_grant` (a value
that is not a JSON object, or for `assistant_renew` no v1 grant under that id), `invalid_library`, `invalid_device`,
`invalid_row` (a row that is not the named settings row), `invalid_layout`, `too_large`, `invalid_queue`,
`invalid_awake` (the TV's queue with no `awakeSince`), `invalid_request`. A reject is
an answer, not an error.

## 12. Threat model

| Threat | What holds |
|---|---|
| **den-edge at rest** (its disk, backups, operator, a stolen copy) | It holds drop-box public keys, sealed queue entries, refresh-secret hashes, and grant keys wrapped under refresh secrets it does not hold. It cannot read the library, open the queue, or sign a request. **`MCP_WRITE_KEY` sits at rest too** — in the box's environment and its backups (den-mcp and den-edge share the box): with it, the `dw` claims in any captured access token open, but a token is short-lived and forging with its grant key still needs a live, unexpired bearer token or a live compromise. |
| **A fully compromised live den-edge** | It sees each grant key at approval and at every refresh, so it can forge requests **within that grant's ops and daily cap** until the grant is revoked or expires. It also relays `/mcp` in plaintext, so it sees what den-mcp is asked to write, and it can serve den-mcp a drop-box key of its own and read those requests: **the queue is confidential at rest, not against a live den-edge.** It cannot read the library (it never has the library key); cannot exceed ops or cap (devices enforce both from the sealed grants row); cannot bypass the removals latch (an accepted request takes the tap path); cannot override a later change (requests apply at their own `at`). It can serve an older grants or applied row, or withhold a revocation's write, so it can **delay a revocation for devices that have never seen it**, but cannot undo one for a device that has (§6 *The device's own records*). It can withhold, delay or re-deliver requests: availability, and re-delivery is deduped. Den Web is served by den-edge, so a den-edge serving a modified page reads what a browser holds — today, with or without this feature (recovery code §1). |
| **A compromised den-mcp** | With `MCP_WRITE_KEY` it opens the claims in the tokens it is sent: the same forging power as den-edge, for live sessions, within ops and cap. It never holds the library key or a drop-box private key. |
| **An AI client, or a prompt injection steering it** | It holds an access token. It cannot open `dw`. It can ask for any write its grant allows, up to the cap: what the person consented to. Every write is an idempotent set the household sees, a removal is held by the latch past its threshold, and revoking stops the rest. |
| **A replayed or reordered request** | The applied set refuses a repeated id for at least 14 days, and `stale` refuses anything older than 7. Order does not matter: every op is a set, applied at its own `at`. |
| **A request made for another library, or by another grant** | The AAD binds the library id into the seal, and the message carries both: it does not open, or fails `wrong_library`, `unknown_grant` or `bad_signature`. |
| **A forgotten connection** | A grant expires 30 days after consent or its last renewal, and Den Web renews only grants whose connection den-edge still lists. |
| **A key reset** | Drop-box keys are not carried over and every grant is revoked (§10), so a device shut out by the reset cannot use one it knew. |
| **A clock** | `at` is den-mcp's; a device refuses one more than 5 minutes ahead (and keeps it queued, §6) or 7 days behind its own clock. The cap counts by applied times, failing closed for times ahead. |
| **Quantum adversary recording traffic** | X-Wing is hybrid: a sealed request or claim stays confidential while either ML-KEM-768 or X25519 holds. Signatures (Ed25519) are not post-quantum; forging one needs a quantum computer at the time, not later. A projection is AES-256-GCM under a 32-byte key carried only by X-Wing seals and wraps, so it is as safe. |
| **Projections at rest** (§15) | den-edge stores each read grant's projection sealed under its read key, which it holds only wrapped under a refresh secret it does not keep: unreadable at rest. Captured `dr` claims open with `MCP_WRITE_KEY`, as `dw` claims do. **Known limit**: the sealed size, compressed, tells den-edge roughly how large the library's lists are and when they change. An assistant's writes do put chosen titles beside the rest, so a den-edge watching sizes while an assistant writes could guess whether a title is already listed — but a den-edge that live already reads the projection outright (below), so compression exposes nothing more than that. |
| **A live den-edge or den-mcp, and reads** | Either can read a projection while serving a request — den-edge unwraps the read key at every refresh, den-mcp opens `dr` on every call — so a live compromise reads **the watchlist, Continue Watching and Seen history** of connections that may read, as fresh as the last publish. Never the library itself, its settings, credentials (`set:keys`, tracker tokens), devices, or anything a projection does not carry (§15 lists all of it). A forged or stale projection is possible for a live den-edge (it holds the key); it misleads an assistant, never a device, which only writes projections. |
| **Playing on the TV** (§16) | A forged `play` needs what a forged write needs (a live grant key) and a live play record, and is applied only by a TV that is awake with Den open, within 120 s. den-edge cannot move a play into the library's queue or a library op into the TV's: the queue is the signed op's, so either is `wrong_queue`. den-edge learns when a TV is awake and drains the TV queue — which it already learns from drains — and whether an appended request was taken, never whether it was applied or refused. |
| **A revoked or expired read** | Devices stop publishing for it and ask den-edge to delete it at once (§15); den-edge also deletes it when the session ends. A revocation in either the read record or the grant with its id counts, so a client that knows nothing of reads still revokes them by revoking the grant. |

## 13. Limits

| | |
|---|---|
| Message | ≤ 1,024 bytes |
| Sealed request | ≤ 4,096 characters (≤ 2,966 in practice) |
| `dw` claim | 1,579 characters |
| Wrap | 102 characters |
| Queue | 200 messages, 7 days, per library |
| Appends | 30 a minute per session, 300 a day per library |
| Daily cap | 1–1,000 per grant, by applied time over 24 hours |
| Grant lifetime | 30 days from consent or renewal |
| Freshness | `at` within [now − 7 days, now + 5 minutes] |
| Applied entries | pruned once `at` and `applied` are both over 14 days old |
| `dr` claim | 1,579 characters |
| Wrap with a read key | 144 characters (80-byte blob) |
| Projection | every entry of every list; ≤ 64 MiB of plaintext, ≤ 64 parts of ≤ 262,144 characters (16 MiB) per grant; 5,000 / 500 / 50,000 entries measured at 2 parts, 392,412 characters |
| Projection publishes | on change, debounced 30 s; a heartbeat every 15 minutes; den-edge takes 60 an hour per grant |
| TV queue (§16) | 20 messages, 10 minutes, per library; 10 appends a minute per session |
| Play freshness | `at` within [max(now − 120 s, the TV's wake time), now + 5 minutes] |

## 14. Vectors

`../vectors/assistant-v1.json`, generated by den-core (`cargo test -p den-sync --test assistant
write_assistant_vectors -- --ignored`, with `DEN_SPEC_DIR` set), where every case is also asserted. Every random input
is derived from a label (the file's `notes` say how).

- `fixed`: every key from its seed; one request (fields, message, signed bytes, signature, `eseed`, sealed); one
  `dw` claim; one wrap with its refresh token, secret and key. den-edge and den-mcp check these through
  `den-assistant` (den-core `crates/den-assistant/tests/vectors.rs`).
- `cases`: `evaluate` requests with their exact answers: an accept for every op and shape (with its stamp), a
  request sealed to the library's second drop-box key, the 7-day and 5-minute boundaries, the cap counted by applied
  time (a clock ahead counting), each reject in §6 (several `malformed` shapes, a key that is no curve point, a seal
  under the token info, a signature by another key in a grant's name, a message for another library sealed for this
  one, a revoked grant whose request predates the revocation, an expired and a renewed grant), the device's own
  revoked and applied sets against an older row, malformed applied entries, the keygens and their refusals,
  revocation and renewal, Settings' list, the grants merge (a revocation in a malformed version, a newer version, the
  later expiry), and pruning with skewed clocks. `crates/den-assistant/tests/rules.rs` covers the message encoding
  (unsorted keys, duplicate members, fractions, exponents, `-0`), a non-canonical `S`, a small-order key and seals
  across infos. den-core's `policy-v1.json` carries a sample of the cases, unchanged, for the bindings.
- `fixed.read` (§15): the same grant with a read key — its `dr` claim, its 80-byte wrap, and a projection: its
  random bytes, view, compact plaintext, set, first part's additional data and parts. Every earlier `fixed` value is
  byte-for-byte what it was before reads. den-core's tests also cover a decompression bomb, and a large library
  (5,000 / 500 / 50,000) sealed in parts that refuse to open mixed, reordered or short.
- Read `cases`: `assistant_grant_key`, `assistant_keygen_read`, renewing a read record, Settings' read list (a
  revoked grant revoking its read record), the read records' merge, and one `assistant_projection` over a small
  library — a watchlisted film, a deleted one, a film watched with a play and one with none, a film in progress, a
  series resumed mid-episode, a series known only by an imported play with its layout — published for the live grant
  and deleted for three dead ones (revoked grant, expired record, the device's own revoked set).

## 15. Reads

A connection may read the watchlist, Continue Watching and Seen history. Devices publish a **projection** of them for
each connection that may read, sealed under that connection's **read key**; den-edge stores the latest; den-mcp opens
it. Nothing about reads changes a v1 grant, request, claim or wrap.

### Consent and keys

- **Scope** `den:library.read`, granted only to a **member** who ticks "Allow reading my watchlist and history" — a
  checkbox of its own, independent of "Allow changes to my library". A connection may read, write, or both.
  den-edge's metadata lists the scope when `MCP_WRITE_PUBLIC_KEY` is set; `GET /oauth/request/{id}` adds `"read":
  true` when it can be granted.
- Every connection that may read or write has a **grant key** (§3). A write connection writes its grant row as
  today; a **read-only** connection gets its key from `assistant_grant_key` and writes **no** grants row setting, so
  it can never write.
- The **read key** is 32 random bytes, made by Den Web at consent (`assistant_keygen_read`), and recorded in the
  library under the connection's grant id, in a row of its own:

  `set:assistant-read`: `<grant id>` → `{"string": "<JCS of {client, createdAt, expiresAt, key, revokedAt, v: 1}>"}`,
  `key` the base64url read key, `expiresAt` `createdAt` + 30 days, the other members as a grant's (§5).

  It merges exactly as a grant does (§5: rank, the later `expiresAt`, a revocation from either side sticking), is
  renewed with `assistant_renew` and revoked with `assistant_revoke` together with the grant, and shown by
  `assistant_grants` (`reads`).
- **Why a row of its own, not a grant v2.** Clients deployed before reads (Den Web 0.265.0 and the TV on den-sync v3.6
  and v3.7) read the grants row strictly: a value with a new member, or `v: 2`, is no grant to them, so a write
  connection made with one would have every request refused on those devices as `unknown_grant`. A new row costs them
  nothing: a client keeps every settings row it does not manage as it read it (library v2 §3), so they carry
  `set:assistant-read` untouched, and every grant value stays exactly v1. Such a client revoking a connection writes
  only the grant's `revokedAt`; a read record is dead whenever the grant with its id is revoked, so that revokes the
  read too.
- **Approval.** Den Web, after writing the read record (and the grant row for a write connection), approves with
  `{"read": {"grant": "<grant id>", "key": "<base64url grant seed>", "readKey": "<base64url read key>"}}`, beside or
  instead of v1's `"write"` object; when both are present they MUST name the same grant and key, else `400
  invalid_grant_key`. A guest's proof with `read` is `403 read_not_for_guests`; `read` when the request cannot have
  the scope is `400 invalid_scope`. If the approval does not succeed, Den Web revokes the read record (and the grant).
- **Wrap.** den-edge wraps the grant blob with the read key appended — 80 bytes, the 48-byte blob of §3 then the read
  key — exactly as §8 says; a wrap of a session with no read scope stays the 48-byte v1 blob. `unwrap` reads either,
  so every wrap made before reads keeps working.
- **The `dr` claim.** An access token whose session has the read scope carries, beside `scope` (which lists
  `den:library.read`), a claim **`dr`**:

  ```
  enc, ct = SealBase(pkR = den-mcp's token public key, info = UTF-8("den/assistant/read/v1"),
                     aad = UTF-8(the token's sub), pt = grant id (16) ‖ read key (32))
  dr      = base64url(enc ‖ ct)                                     (1,184 bytes, 1,579 characters)
  ```

  `dw` is unchanged: still the 48-byte blob, still present only for the write scope. A den-mcp from before reads
  ignores `dr`; its `dw` handling is untouched. A session with both scopes carries both claims (about 4.3 KB of token
  in all).

### The projection

A projection is the **whole** library's lists — no list is cut short, whatever its length. As den-mcp reads it (the
**view**, what `open_projection` returns):

```json
{"v": 1, "library": "<library id>", "grant": "<grant id>", "at": <publish time, ms>, "head": <library log head>,
 "watchlist": [{"title": {"type", "id"}, "addedAt": <ms | null>}],
 "continue": [{"title", "action": "resume" | "next", "season"?, "episode"?, "fraction", "at": <ms | null>}],
 "seen": [{"title", "season"?, "episode"?, "at": <ms | null>}]}
```

- Titles are ids only (`{"type": "movie" | "tv", "id": <TMDB id>}`); den-mcp names them through den-atlas.
- **`watchlist`**: every title document whose `status` is `watchlist`, not `deleted`; `addedAt`, else the status's
  time. Newest first.
- **`continue`**: den-core's Continue Watching, built as both clients build their row: per series, the episode mark
  touched last, the furthest finished episode and the furthest episode watched with no mark (an import), handed with
  the series' layout to `continue_entry`; a series whose answer is `none` is left out. `layouts` is the season layout
  the client knows, by TMDB id — `{"seasons": [{season, episodes}], "last_aired": {season, episode} | null}` — and a
  series with none can still resume, but not move on to its next episode. Series by their latest mark (one known only
  by an import last), then films `inProgress` and not dismissed since, latest first. `at` is the latest mark's time.
- **`seen`**: one entry per visible play of a film or an episode (`film_state_v4` / `episode_state_v4`'s `plays`), at
  its time; one entry with `at` null for something watched with no play to date it. Newest first, untimed last.
  Deleted titles are left out of every list.
- `head` is the library log's head the documents were read at (library v2 §2), so den-mcp can say "as of" both by
  time and by change.

### The compact form

On the wire each title is written once, in a table, and each entry is an array that indexes it. The plaintext is the
JCS of:

```json
{"v": 1, "library", "grant", "at", "head",
 "titles": [["movie" | "tv", <id>], …],
 "watchlist": [[<title index>, <addedAt | null>], …],
 "continue": [[<title index>, <action>, <season | null>, <episode | null>, <fraction>, <at | null>], …],
 "seen": [[<title index>, <season | null>, <episode | null>, <at | null>], …]}
```

`titles` is every title the lists name, once, sorted by type then id; a `null` season or episode is one the entry does
not have. Lists keep the view's order. `projection_plaintext` writes it; `open_projection` reads it back to the view.

### Sealing in parts

```
plaintext = the compact form's JCS                                  (at most 64 MiB)
deflated  = raw DEFLATE (RFC 1951) of plaintext, miniz_oxide level 9 (as library v4 §4)
chunks    = deflated cut into pieces of 196,580 bytes, the last shorter; count = their number (1 to 64)
set       = hex(HKDF-SHA256(ikm = random, info = UTF-8("den/assistant/projection/set/v1"))[0..16])
nonce_i   = HKDF-SHA256(ikm = random, info = UTF-8("den/assistant/projection/nonce/v1") ‖ 0x00 ‖ UTF-8(decimal i))[0..12]
aad_i     = UTF-8("den/assistant/projection/v1") ‖ 0x00 ‖ library id ‖ 0x00 ‖ grant id ‖ 0x00 ‖ set
            ‖ 0x00 ‖ decimal i ‖ 0x00 ‖ decimal count
part_i    = base64url(nonce_i ‖ AES-256-GCM(read key, nonce_i, aad_i, chunk_i))      (≤ 262,144 characters)
```

- `random` is 32 bytes fresh for every publish of every grant: `assistant_projection` derives each grant's from the
  call's 32 random bytes with HKDF-SHA256 (info `den/assistant/projection/grant/v1` ‖ 0x00 ‖ grant id).
- Every part names its publish (`set`), its place and the number of parts, so den-mcp opens a set only whole, in
  order, and from one publish: a part of another publish, a part moved, a part missing or an extra one does not open.
- `open_projection` opens every part, joins the chunks, inflates them with a hard limit of **64 MiB** (a set that
  inflates further is refused, however small it is sealed — a decompression bomb), and reads the compact form,
  which must be `v` 1 and name this library and grant.
- Nothing is cut: a library too large for 64 parts (about 12.6 MB compressed) or 64 MiB of plaintext is refused
  (`too_large`) rather than published short. A real library is far below either: 5,000 watchlist entries, 500 in
  Continue Watching and 50,000 Seen plays seal into 2 parts, 392,412 characters (den-core's
  `a_large_library_projects_whole`).

### Publishing

- The TV publishes whenever it runs; Den Web while it is open. Each builds with `assistant_projection` from the
  documents it holds read to the log's head, the layouts it knows, its revoked set (§6) and fresh random bytes.
- **When**: when `digest` (the SHA-256 of the lists, not of `at` or `head`) differs from the one it last published
  for that grant, debounced **30 s**; and as a heartbeat every **15 minutes** while running, so `at` stays fresh.
  Two devices publishing replace each other; both publish the same lists once both have read the same log.
- **What**: `PUT` every entry of `publish` (its `set` and `parts`, as they are); `DELETE` every grant in `delete` —
  read records revoked (in the record, in
  the grant with their id, or in the device's revoked set), expired, or malformed — once, and again whenever one
  reappears.
- A read record expires 30 days after consent or its last renewal; Den Web renews it with its grant (§5) while
  `/oauth/connections` lists the session.

### den-edge

Every answer is JSON; errors `{"error": "<code>"}` as §9.

**`PUT /assistant/projection/{grant}`** — member. Body `{"set": "<32 hex>", "parts": ["<base64url>", …]}`, at most
17 MiB. Replaces the grant's projection with the whole set, **atomically**: a reader gets the old set or the new one,
never a mix. `204`.
- `400 invalid_set` when `set` is not 32 lowercase hex.
- `400 invalid_sealed` when there are no parts or more than **64**, a part is not unpadded base64url, is over
  **262,144 characters** or decodes to 28 bytes or fewer, or the parts come to more than **16 MiB** of characters.
- `404 unknown_grant` when no session with the read scope of the member's library names that grant (den-edge never
  stores a projection nobody can read; a device drops it from its next publish until the connection is made).
- `429 rate_limited` past **60 an hour per grant**.

**`DELETE /assistant/projection/{grant}`** — member, for a grant of the member's library. `204`, also when there was
none.

**`GET /assistant/projection`** — Bearer, with the `den:library.read` scope, the session standing (§9). `200
{"library": "<id>", "grant": "<grant id>", "set": "<32 hex>", "parts": ["…", …], "storedAt": <ms>}` — one stored set,
whole — for the token's session; `404 no_projection` when no device has published one yet; `401`/`403` as §9.

den-edge deletes a grant's projection when its last read session ends (revoked, expired, idle) and when its library's
key is reset (§10). It stores nothing that opens one at rest.

### den-mcp

On a call with the read scope it opens `dr` (`open_read_claim`, checking the `grant` matches the session's), `GET`s
the projection, opens it with `open_projection(read key, library, grant, set, parts)` (the library, grant, set and
parts from the answer), and answers `den_watchlist`, `den_continue_watching` and `den_history(since?, limit?)` from
the view, naming titles through den-atlas and saying how fresh it is from `at`. The view is the whole library;
`limit` is den-mcp's own paging, never a gap in the data. A `404 no_projection` means no device has published since
the connection was made: the tool says to open Den on a device.

## 16. Play on the TV

An assistant may ask the TV to play a title, an episode, or a trailer. Only a TV applies these, and only while Den is
open on it. Like reads, this is additive: v1 grants, messages, claims and wraps are unchanged.

### Ops

`play` and `play_trailer` (§4's table) are request ops like the others: same message, signature and seal. A `play` of
a series with no `episode` plays it where it stands — the TV picks the episode exactly as its Continue Watching row
does, with den-core's `continue_entry` (a series it has no layout for resumes its last mark, or starts at S1E1).

### Who may play

- **Not the grants row.** A grant's `ops` stays the four library ops (`GRANT_OPS`). Clients deployed before play read
  `ops` strictly, so a grant listing `play` would be no grant to them, and every write by that connection would be
  refused there as `unknown_grant`. `assistant_keygen_grant` refuses a TV op (`invalid_ops`).
- **A play record**, in a row of its own, under the connection's grant id:

  `set:assistant-play`: `<grant id>` → `{"string": "<JCS of {client, createdAt, expiresAt, revokedAt, v: 1}>"}`.

  It merges as a grant does (§5), is renewed (`assistant_renew`) and revoked (`assistant_revoke`) with its grant, and
  listed by `assistant_grants` (`plays`). A connection may play while its grant stands (a play needs the grant's key
  and signature) **and** its play record is live: not revoked, not expired, its grant not revoked, not in the device's
  revoked set. Otherwise a TV op is `op_not_allowed`. Old clients carry the row untouched (library v2 §3), and revoking
  the grant, as they do, ends the play too.
- **Consent.** Den Web words the write checkbox "Allow changes to my library and playing on my TV" and, ticked, writes
  the play record (`assistant_keygen_play`) with the grant row. Grants made before play have no play record: their
  TV ops are `op_not_allowed` until the owner reconnects the assistant.
- A play counts toward its grant's daily cap like any request.

### Queues

A library has two queues at den-edge: the **library** queue (§9, every device drains it) and the **TV** queue (only a
TV drains it). A TV op belongs in the TV queue and every other op in the library queue — `den_assistant::Queue::of`
— so a TV-only request never sits in Den Web's way, and Den Web's contiguous acknowledgements never wait on a TV.

**The queue is bound by the signature.** It is a function of the signed `op`, so nothing new is signed and the
message stays v1 (a strict v1 parser on an old client refuses a TV op as `malformed`, which is the right outcome
there). A request drained from the other queue — den-edge moving a play into the library queue, or a write into the
TV's — is refused `wrong_queue` (§6, rule 6a) and acknowledged; it is never applied from the wrong queue.

### Applying on the TV

- The TV drains both queues. It drains the TV queue only while Den is open on it (in the foreground), with `wait`
  while open, and opens each request with `assistant_open` and `"queue": "tv"`, `"play"` (the play row) and
  **`"awakeSince"`**: when the TV last woke or came to the foreground, ms. Den Web drains only the library queue.
- **Freshness.** A TV op is `stale` when its `at` is more than **120 s** before `now`, or before `awakeSince`: a play is
  never applied after the TV slept, however recent. `from_future` is as §6 (5 minutes). Library ops keep their 7 days.
- **Applying.** An accepted `play` plays through the same path as the person's own Play (the release choice, resume
  position and player), and `play_trailer` as the title page's trailer. It writes nothing to the library but the
  applied entry (`setting`/`value`, recorded as §6 says, so a replay is refused); playback then writes progress as any
  playback does.
- **Acknowledging** is as §6: every request handled — applied, or refused (`stale`, `wrong_queue`, …) — is acked,
  except `from_future`. The TV queue's acks are its own (`?queue=tv`).

### den-edge

The library queue's endpoints (§9) take a queue: `queue` in the append body, `?queue=` on drain and ack; absent is
`library`, so every request made before this section is unchanged.

- **`POST /assistant/append`** — `{"sealed": "…", "queue": "tv"}` appends to the TV queue: `200 {"seq", "queued"}`, seqs
  of its own. Caps: **20** waiting (`409 queue_full`), kept **10 minutes** (a play older than 120 s is stale anyway),
  **10 appends a minute per session** (`429`). `400 invalid_queue` for another value. den-mcp appends `play` and
  `play_trailer` to the TV queue and everything else to the library queue; den-edge does not read the op.
- **`POST /assistant/drain?queue=tv[&wait=S]`** and **`POST /assistant/ack?queue=tv {"upTo"}`** — member, as §9. Only a TV
  drains the TV queue; den-edge records the time of every TV-queue drain for the library.
- **`GET /assistant/tv`** — Bearer, write scope. `200 {"online": <bool>, "lastSeenAt": <ms | null>}`: `online` when the TV
  queue was drained within the last **40 s** (a TV draining with `wait=25` drains at least that often), `lastSeenAt`
  the last such drain, `null` if never.
- **`GET /assistant/request/{seq}?queue=library|tv`** — Bearer, write scope, for the session's library. `200 {"state":
  "queued" | "taken"}`: `queued` while the message waits, `taken` once a device acknowledged it. `404 unknown_request`
  for a seq this library's queue never issued or has expired. `taken` says nothing of whether the request was applied
  or refused: devices tell den-edge only what they acknowledge.

### den-mcp

`play` and `play_trailer` tools build and seal requests as §4 (`seal_request_with_rng`) and append them with `"queue":
"tv"`. Before appending they may check `GET /assistant/tv` and say the TV is off or asleep rather than queue a play
that will go stale; after appending, `GET /assistant/request/{seq}?queue=tv` tells whether a TV took it.

### den-core

`den-assistant`: `OPS` (six ops), `GRANT_OPS` (the four a grant lists), `PLAY_OPS`, `PLAY_STALE_MS`, `Queue { Library,
Tv }` with `Queue::of(op)`, `Reject::WrongQueue`, `check_queue(…, queue, awake_since)` (`check` is `check_queue` on the
library queue), and `Grant.play`. `den-sync`: `assistant_keygen_play`, `assistant_open`'s `play` / `queue` /
`awakeSince`, `assistant_grants`' `plays`, `assistant_renew` and `merge` for the play row (§11).
