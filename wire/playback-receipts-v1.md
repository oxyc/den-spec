# Playback completion receipts, v1

How a managed Hearth playback that ends while no Den client can save progress eventually becomes the same
library-v4 progress write the player would have made. Hearth seals terminal player evidence to the household;
den-edge stores only opaque bytes; the next Web or Apple client opens, checks and applies them.

Words: *MUST* is a rule whose violation can disclose viewing history or corrupt progress; *should* is advice.

## 1. Parties and flow

1. A household member starts `POST /den/<config>/playback` through den-edge. Edge proves membership, reads that
   library's already-published assistant drop-box public key, creates one random callback capability, and attaches
   the library, key id, public key, receipt id and capability to this exact authenticated upstream operation.
2. Hearth keeps that context only with the resulting playback lifecycle. It exposes an end capability in the
   playback answer. The player sends its terminal `positionSeconds`, `durationSeconds` and `completed` there.
3. Hearth stamps `endedAt`, builds §3, seals it, then calls den-edge with the operation's callback capability.
   Callback retries carry the identical ciphertext. They are idempotent.
4. Edge appends the ciphertext to the library's receipt queue. `POST /playback/receipts/drain[?wait=S]` returns
   every waiting entry without removing it; `POST /playback/receipts/ack {"upTo":n}` removes handled entries.
   Both are ordinary member routes. A disconnect between drain and ack redelivers the entry.
5. A client opens each entry with every live drop-box private key from `set:assistant`, checks §4, and writes the
   returned ordinary library-v4 progress record and its replay record in one local batch. It acks only after that
   batch is durable. Sync and tracker delivery then proceed exactly as for progress saved by the player.

This reuses the assistant drop-box *key*, not an assistant OAuth grant or its queue. Playback gains no assistant
scope, and an assistant token gains no receipt access.

## 2. Cryptography

The primitives, strict base64url and key id are assistant-v1 §2–3. The selected drop-box public key is `pk` and
its id is `kid`.

```
message   = UTF-8(JCS(receipt))
enc, ct   = SealBase(pkR = pk, info = UTF-8("den/playback-receipt/v1"),
                     aad = UTF-8(library), pt = message)
sealed    = base64url(enc || ct)
```

There is no signature: the unguessable, single-operation callback capability is issued by Edge only after member
authentication and delivered only over Edge's authenticated private hop to Hearth. Edge accepts at most one
ciphertext for it; a retry must be byte-identical. HPKE separates receipts from assistant requests, which use
`den/assistant/v1` and contain a signed grant request.

Every encapsulation seed, receipt id and callback capability MUST be fresh CSPRNG output. Neither capability nor
plaintext is logged. The message is at most 768 bytes; `sealed` is at most 2,539 base64url characters.

## 3. Receipt

The plaintext is the UTF-8 JCS of exactly this object (the example is one TV episode):

```json
{"completed":false,"durationSeconds":2400,"endedAt":1790000000000,"episode":3,"expiresAt":1790604800000,"id":"94619420c64002287a3ad7ecad5dfa6b","kid":"2a37dd6f422e1a735b8a9f7f8b377e12","library":"4c1b7a0e9d3f2c8b5a6e1d0f7c3b9a2e","positionSeconds":2310,"season":2,"title":{"id":1399,"type":"tv"},"v":1}
```

| Member | Value |
|---|---|
| `v` | `1` |
| `library`, `kid`, `id` | 32 lowercase hex characters; `id` is stable for this playback operation |
| `title` | library-v4 title key: `type` is `movie` or `tv`, `id` a positive safe integer |
| `season`, `episode` | both absent for a movie; both present for an episode; season `0..9999`, episode `1..99999` |
| `positionSeconds` | integer `0..durationSeconds` |
| `durationSeconds` | positive safe integer |
| `completed` | terminal player evidence: natural end or the client's normal completion threshold |
| `endedAt` | Unix ms when Hearth received the last player evidence, not when an idle supervisor later reaped it |
| `expiresAt` | Unix ms from `endedAt` through `endedAt + 7 days` |

Requested or buffered final HLS segments are not completion evidence. With no terminal player evidence Hearth emits
no receipt. A server-side idle end may at most preserve earlier explicit partial evidence.

## 4. Client acceptance and application

A client tries all live `dropbox.<kid>` secrets, but accepts only when the key that opens the ciphertext has the
message's `kid`. It MUST reject a non-canonical or overlong message, unknown members, a wrong library or key,
invalid title/episode/time fields, `endedAt` more than five minutes in the future, an entry older than seven days,
an expired entry, or an id already in `set:playback-receipts-applied` or the current local batch.

An accepted entry produces:

- the ordinary library-v4 progress write for its title/episode, stamped `[endedAt, 0, device]`, with `seconds =
  positionSeconds` and `value = 1` when completed, otherwise `positionSeconds / durationSeconds`; and
- `set:playback-receipts-applied[id] = {"string":"<JCS of {applied,endedAt}>"}`, stamped at the application time.

Both MUST become durable atomically. Library merge then makes a reordered or delayed receipt harmless: a progress
write made after `endedAt` wins. The applied row makes callback, drain and multi-device replay idempotent.

## 5. Bounds, expiry and key epochs

- One library queue holds at most 100 ciphertexts. An entry and a callback capability expire after seven days.
- A callback capability is bound to one library, `kid`, receipt id and playback operation. Its first accepted
  ciphertext fixes its value; a different retry is refused.
- Edge limits callback attempts per capability and per address, and member drains by the existing member-route
  limits. Full means full: a new receipt never evicts an older waiting one.
- A key rotation publishes the new public key for new playbacks. Old private keys remain in `set:assistant`, so
  already-issued receipts remain openable through their seven-day lifetime.
- Queue reads, key creation and publication are lazy. A client with no configured drop-box or no queued receipts
  does no startup crypto and opens no background connection solely for this feature.

## 6. Threat model

Edge is honest-but-curious and may be compromised after storage: it learns the household, enqueue/drain timing,
count and ciphertext size, but not title, episode, position or completion. The fixed schema and small size cap limit
size leakage. Hearth necessarily sees the title and player evidence while serving the playback, but holds no
household private key. A stolen callback capability can enqueue at most one ciphertext for its already-bound
household and expires; it cannot choose another library or read the queue. A stolen member proof can already write
that library and is outside this protocol's authority expansion.

Malicious ciphertext, replay, cross-household substitution, stale delivery, reordered delivery, key-epoch races,
disconnects and process retries fail closed under §§2–5. Availability is not guaranteed: Edge may drop or delay
opaque entries, and queue exhaustion may lose a receipt, but neither case can corrupt newer progress.

## 7. Non-goals

This is not a generic durable job system, webhook product, assistant notification channel, playback event history,
or server-side library writer. It carries only bounded terminal playback evidence and leaves every library mutation
to an ordinary household client.
