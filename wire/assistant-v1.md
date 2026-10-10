# Assistant writes, v1

How an assistant (Claude, ChatGPT) connected through den-mcp changes a library — adds to or takes off the watchlist,
marks seen or unseen, rates — while no Den is open (oxyc/den#217). den-mcp **seals** a request to the library; it can
never **open** anything. Devices (the TV, Den Web) drain the requests, check them, and apply them through the same
den-core ops a person's tap uses, so tracker delivery and the removals latch (library v4 §9) apply unchanged.

[`../vectors/assistant-v1.json`](../vectors/assistant-v1.json) pins every byte below. The rules are implemented
once, in den-core: the crate `den-assistant` (keys, sealing, claims, wraps, the accept rules), which den-edge and
den-mcp depend on by git tag, and den-sync's `assistant_*` ops (§10), which the clients call.

Words: *MUST* is a rule a party breaks at the cost of the household's library or secrets; *should* is advice.

## 1. Overview

| Step | Who | What |
|---|---|---|
| Consent | Den Web, a **member** | On den-edge's consent page the member ticks "Allow changes to my library". Den Web makes sure the library has a drop-box key (§5), makes a grant key, writes the grant row (§5), then approves with the grant key (§9). |
| Token | den-edge | At code exchange it wraps the grant key under the new refresh secret (§8). Every access token for the session carries the grant key sealed to den-mcp (§7). |
| Write | den-mcp | Opens the claim, fetches the library's drop-box public key, builds, signs and seals a request (§4), appends it to the library's queue (§9). |
| Apply | the TV, Den Web | Drain the queue, open and check each request (§6), apply what is accepted through the tap path, record it as applied, acknowledge. |

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
use. den-core takes them as inputs (it is pure, like den-sync), so one input always gives one output and the vectors
can pin it. Reusing an `eseed` reuses the HPKE key; reusing a wrap nonce under one key breaks AES-GCM.

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
| `at` | when den-mcp made it, Unix ms, an integer `0 ≤ at ≤ 2^53 − 1` |
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

A message is **valid** only if it is byte for byte the JCS of its own parse (so: no whitespace, sorted members, no
duplicate member, integers written as integers), has exactly the members above, and its `args` has exactly the
members its op allows (`season` and `episode` only on a `tv` title, `episode` only with `season`). Every string in a
valid message is ASCII, so its JCS is the sorted, compact JSON any serializer writes for it.

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

den-mcp MUST build requests with den-core's `den_assistant::seal_request` (it checks the message is valid before it
signs) and a fresh `id` and `eseed` for each.

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
- v1 never clears or rotates a key; a `null` setting is not tried.

### `set:assistant-grants` — the grants

One setting per grant, keyed by grant id: `<grant id>` → `{"string": "<JCS of the grant>"}`:

```json
{"cap":3,"client":"Claude","createdAt":1787408000000,"ops":["rate","seen","watchlist_add","watchlist_remove"],"pk":"<base64url, 32 bytes>","revokedAt":null,"v":1}
```

| Member | Value |
|---|---|
| `v` | `1` |
| `pk` | the grant's Ed25519 public key; its grant id MUST be the setting's name |
| `client` | the assistant's name as the consent page showed it: 1–80 characters, no leading or trailing whitespace, no control, bidi or zero-width character |
| `ops` | the ops allowed, a non-empty list of distinct v1 ops, sorted |
| `cap` | requests a day, `1 ≤ cap ≤ 1000` |
| `createdAt` | consent time, Unix ms |
| `revokedAt` | null, or when the connection was revoked, Unix ms |

- A grant is written once, by Den Web at consent (`assistant_keygen_grant`), **before** it approves at den-edge, so
  a request can never reach a device before its grant. It changes only by revocation (`assistant_revoke`), which
  Den Web writes whenever it disconnects that assistant (§9 *Revoking*).
- A value that breaks any rule above — another member, `v` other than 1, a `pk` whose id is not the setting's — is
  **malformed**: it is no grant, and a request naming it is `unknown_grant`. A newer grant version therefore fails
  closed on a v1 device.
- **Merge** (den-core `merge` of a `set:assistant-grants` row): a well-formed version beats a malformed one; two
  malformed ones go by the later stamp. Two well-formed ones keep the byte-greater JCS of every member but
  `revokedAt` (the versions agree on them, since a grant is written once), the **earlier non-null `revokedAt`** (a
  revocation is never undone, and an earlier one stands), and the later stamp. Each part is a join, so the merge is
  commutative, associative and idempotent.

### `set:assistant-applied` — the requests applied

One setting per applied request, keyed by request id: `<id>` → `{"string": "<JCS of {applied, at, grant}>"}`:
`grant` the grant id, `at` the request's own `at`, `applied` when the device that applied it did (its `now`).

- Merge: by the later stamp, per setting, so the row is the union of every device's entries.
- A malformed value still names an id that was applied (it refuses a replay), counts toward no grant's cap, and is
  pruned at once.
- **Pruning.** An entry whose request's `at` is more than **8 days** before `now` can no longer guard anything: §6
  refuses such a request as `stale`. A device may drop the entries `assistant_prune` names and write the row by
  compare-and-set — no tombstone. An entry another device's older copy merges back is harmless and pruned again.
  Pruning should run at most once a day.

## 6. Applying

### Opening and checking

den-core's `assistant_open` decides, in this order, and the first rule a request fails is its answer:

| # | Reject | When |
|---|---|---|
| 1 | `does_not_open` | `sealed` is longer than 4,096 characters, is not base64url, or opens under none of the row's drop-box keys with `aad` = this library's id |
| 2 | `malformed` | the plaintext is 64 bytes or fewer, or the message is not a JSON object with a string `grant` |
| 3 | `unknown_grant` | the grants row holds no well-formed grant under that id |
| 4 | `bad_signature` | the signature does not verify, strictly, under the grant's `pk` |
| 5 | `malformed` | the message is not valid (§4) |
| 6 | `wrong_library` | the message's `library` is not this library |
| 7 | `revoked` | the grant has a `revokedAt` — whatever the request's `at`: a revocation stops everything still queued |
| 8 | `from_future` | `at` > `now` + 5 minutes |
| 9 | `stale` | `at` < `now` − 7 days |
| 10 | `replay` | the applied row already holds `id` |
| 11 | `op_not_allowed` | the grant's `ops` does not hold `op` |
| 12 | `over_daily_cap` | the applied row already holds `cap` or more entries of this grant whose `applied` is after `now` − 24 hours |

Only signed content is judged after rule 4: rule 2 reads the `grant` member only to find the key that checks it. The
cap counts by when requests were **applied**, not by their `at`, which the signer chooses.

`now` is the device's clock. A request exactly 7 days old, or exactly 5 minutes ahead, is accepted.

### Performing

A device **MUST** perform an accepted request through exactly the path the person's own action takes on that client
— the same den-core `apply_write` (library v4 §8), stamped by the device as a fresh write, and the same tracker
delivery (library v4 §9), the removals latch included. No other path exists for it: an accepted request is treated as
the person's tap and nothing more. A request whose action would change nothing (adding a title already on the
watchlist) is still accepted and recorded.

In the same write as its effect, or straight after it, the device records it: the accept's `setting` and `value`
written into `set:assistant-applied` with a fresh stamp.

### Draining

- A device drains only after reading the library log to its head, so it holds the current drop-box keys, grants and
  applied set.
- It handles a drain's messages in `seq` order: open and check each against the rows as they stand, apply and record
  the accepted ones, then **acknowledge** (§9) up to the last `seq` it handled. A rejected request is acknowledged
  too, after logging its reason (never its content). A device that fails before recording a request does not
  acknowledge it; it is delivered again and the applied set decides.
- Two devices draining at once may both apply one request before either sees the other's entry. Every op is an
  idempotent set, so the library ends the same; the applied row keeps one entry per id; the cap may be passed by one
  request per extra device. A device should still drain only while it is in the foreground (the TV) or open (Den
  Web).
- Den Web and the TV should drain on open, and then with `wait` (§9) while open, as for the inbox (inbox v1 §3).

## 7. The token claim

An access token for a session with the write scope carries the claim **`dw`**:

```
enc, ct = SealBase(pkR = den-mcp's token public key, info = UTF-8("den/assistant/token/v1"),
                   aad = UTF-8(the token's sub), pt = grant blob (48))
dw      = base64url(enc ‖ ct)                                       (1,184 bytes, 1,579 characters)
```

- den-edge seals a fresh claim, with a fresh `eseed`, for every access token it issues.
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
  already do) until the code is exchanged; then den-edge wraps it under the first refresh secret and forgets it.
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
  `write` the approval grants `den:search` alone, as today.
- **Tokens.** A write session's token answer has `"scope": "den:search den:library.write"`, its access tokens the
  same scope and `dw` (§7). The session record keeps the grant id and the wrap (§8).
- **Connections.** `GET /oauth/connections` names each write session's `grant`.
- **Revoking.** When Den Web disconnects a write session (`DELETE /oauth/connections/{sid}`), it MUST also write that
  grant's `revokedAt` (`assistant_revoke`) into the library. den-edge's deletion stops new appends at once; the
  grant row is what every device enforces, and what a compromised den-edge cannot undo.

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
- den-edge cannot open an entry and does not try. It learns when an assistant writes and how much, not what.
- Re-delivery is always safe: the applied set (§5) dedupes, so den-edge may hand a message out any number of times.

## 10. den-core

`den-assistant` (no dependency on den-sync) is what den-edge and den-mcp use: `kem_public`, `key_id`, `grant_public`,
`grant_id`, `GrantKey` (from a seed; its blob), `message`, `sign`, `seal_request`, `parse_message`, `check`,
`prune`, `seal_claim` / `open_claim`, `wrap_key` / `wrap` / `unwrap`, `b64url` / `b64url_decode`. Each random input
is an argument.

The clients call den-sync's `evaluate` (the versioned JSON envelope every op shares). Every number is an integer;
`random` is 32 bytes of hex from the platform's CSPRNG; rows are the opened settings rows (`{"kind": "set", "schema":
2, "name", "values"}`), and an absent or `null` row is an empty one.

| Op | Request | `ok` |
|---|---|---|
| `assistant_keygen_dropbox` | `random` | `{kid, public, setting: "dropbox.<kid>", value: {"string": <private key>}}` |
| `assistant_dropbox` | `assistant` (row) | `{kid, public}` of the key den-edge should hold, or `null` |
| `assistant_keygen_grant` | `random`, `client`, `ops`, `cap`, `now` | `{grant, public, secret, setting: <grant id>, value: {"string": <JCS grant>}}`; `secret` (base64url seed) goes to den-edge's approval and nowhere else |
| `assistant_revoke` | `grant` (id), `value` (the setting's tagged value), `now` | `{value}` with `revokedAt` set, or kept when earlier |
| `assistant_open` | `library`, `sealed`, `assistant`, `grants`, `applied` (rows), `now` | `{"accept": {grant, id, at, op, args, setting, value}}` — `setting`/`value` are the applied entry to record — or `{"reject": "<reason>"}` |
| `assistant_prune` | `applied` (row), `now` | `{"remove": [<request id>, …]}`, sorted |
| `merge` | two `set:assistant-grants` rows | the merged row (§5) |

Errors: `invalid_seed`, `invalid_client`, `invalid_ops`, `invalid_cap`, `invalid_time`, `invalid_grant` (a value
that is no grant under that id), `invalid_library`, `invalid_row` (a row that is not the named settings row),
`invalid_request`. A reject is an answer, not an error.

## 11. Threat model

| Threat | What holds |
|---|---|
| **den-edge at rest** (its disk, backups, operator, a stolen copy) | It holds drop-box public keys, sealed queue entries it cannot open, refresh-secret hashes, and grant keys wrapped under refresh secrets it does not hold. Nothing usable: it cannot read the library or the queue, and cannot sign a request. |
| **A fully compromised live den-edge** | It sees each grant key at the session's next refresh, so it can forge requests **within that grant's ops and daily cap** until the grant is revoked. It cannot read the library (it never has the library key) or the queue (only the library holds a drop-box private key); cannot exceed ops or cap (devices enforce both from the sealed grants row); cannot undo a revocation (the row is sealed under the library key and `revokedAt` is sticky); cannot bypass the removals latch (an accepted request takes the tap path). It can withhold, delay or re-deliver requests: availability only, and re-delivery is deduped. It can hand den-mcp another library's public key, but a request signed by this grant fails there as `unknown_grant`. Den Web is served by den-edge, so a den-edge serving a modified page reads what a browser holds — today, with or without this feature (recovery code §1). |
| **A compromised den-mcp** | `MCP_WRITE_KEY` opens the claims in the tokens it is sent: the same forging power as den-edge, for live sessions, within ops and cap. It never holds the library key or a drop-box private key. |
| **An AI client, or a prompt injection steering it** | It holds an access token. It cannot open `dw`. It can ask for any write its grant allows, up to the cap: what the person consented to. Every write is an idempotent set the household sees in the library, a removal is held by the latch past its threshold, and revoking stops the rest. |
| **A replayed or reordered request** | The applied set refuses a repeated id for 8 days, and `stale` refuses anything older than 7. Order does not matter: every op is a set. |
| **A request made for another library, or by another grant** | The AAD binds the library id into the seal, and the message carries both: it does not open, or fails `wrong_library`, `unknown_grant` or `bad_signature`. |
| **A clock** | `at` is den-mcp's; a device refuses one more than 5 minutes ahead or 7 days behind its own clock. A device with a wrong clock may refuse good requests; the cap counts by the device's own `applied` times. |
| **Quantum adversary recording traffic** | X-Wing is hybrid: a sealed request or claim stays confidential while either ML-KEM-768 or X25519 holds. Signatures (Ed25519) are not post-quantum; forging one needs a quantum computer at the time, not later. |

## 12. Limits

| | |
|---|---|
| Message | ≤ 1,024 bytes |
| Sealed request | ≤ 4,096 characters (≤ 2,966 in practice) |
| `dw` claim | 1,579 characters |
| Wrap | 102 characters |
| Queue | 200 messages, 7 days, per library |
| Appends | 30 a minute per session, 300 a day per library |
| Daily cap | 1–1,000 per grant, by applied time over 24 hours |
| Freshness | `at` within [now − 7 days, now + 5 minutes] |
| Applied entries | pruned 8 days after their request's `at` |

## 13. Vectors

`../vectors/assistant-v1.json`, generated by den-core (`cargo test -p den-sync --test assistant
write_assistant_vectors -- --ignored`, with `DEN_SPEC_DIR` set), where every case is also asserted. Every random input
is derived from a label (the file's `notes` say how).

- `fixed`: every key from its seed; one request (fields, message, signed bytes, signature, `eseed`, sealed); one
  `dw` claim; one wrap with its refresh token, secret and key. den-edge and den-mcp check these through
  `den-assistant` (den-core `crates/den-assistant/tests/vectors.rs`).
- `cases`: `evaluate` requests with their exact answers: an accept for every op and shape, a request sealed to the
  library's second drop-box key, the 7-day and 5-minute boundaries, the cap counted by applied time, each reject in
  §6 (several `malformed` shapes, a signature by another key in a grant's name, a message for another library sealed
  for this one, a revoked grant whose request predates the revocation), the keygens and their refusals, revocation,
  the grants merge, and pruning. den-core's `policy-v1.json` carries a sample of them, unchanged, for the bindings.
