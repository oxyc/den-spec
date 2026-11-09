# Profile-routed playback completion receipts, v2

How a persistent Owner or Kid profile authorizes, receives and applies terminal evidence from managed Hearth
playback without revealing profile identity or viewing history to den-edge. This revision replaces the household
lane in v1 with a profile-owned opaque route. It is a contract-first protocol: no implementation or deployment is
implied by this document.

Words: *MUST* is a rule whose violation can disclose viewing history, cross a profile boundary or corrupt progress;
*should* is advice. Released v1 remains unchanged.

## 1. Parties, authority and invariants

A live persistent profile (Owner, Kid, or a future persistent role) lazily creates:

- `authority`: 32 random bytes, encoded as canonical unpadded base64url (43 characters);
- `route`: the non-identifying 16-byte value derived in §2, encoded as 32 lowercase hexadecimal characters; and
- `binding`: 16 random bytes, encoded as 32 lowercase hexadecimal characters and stable for this profile's life.

The authority and binding are profile-owned durable state; only the authority is secret. They travel only inside
that profile's encrypted durable state and every successor made by an authenticated same-profile activity-library
replacement. They MUST NOT be
derived from or copied between the household key, profile id, role, name, policy, activity-library key, another
profile, or a device id. A replacement is not committed until the successor durably contains them. A new profile,
including a recreated profile after deletion, creates different values.

Visitor owns no authority, route, binding, key, queue or replay state. Visitor playback remains available, but its
completion is visit-local and creates no durable receipt. A client MUST NOT borrow Owner/current-profile authority
for Visitor, a missing profile, a recovery-blocked directory, or a profile whose replacement is unproved.

Edge stores the household that owns a route and only opaque route, binding, activity-library, generation, key epoch,
queue and expiry metadata. It never stores `authority`, profile id, role, name, policy or plaintext player evidence.
The activity-library id is routing metadata, not profile identity, and MUST NOT appear in diagnostics. Authority and
proof bytes are sensitive request credentials: parse them only after member authentication, never log, meter, trace,
cache or return them, and erase transient copies as soon as practical.

One authority has exactly one route and binding. Route state has a positive safe-integer `generation`; registration
creates generation 1. A rebind or receipt-key rotation advances it by exactly one. Edge retains every superseded
generation needed by an unexpired callback or queued entry, but it accepts new playback only at the current one.

## 2. Derivation and proof of possession

All concatenation below is bytes. `JCS` is RFC 8785 canonical JSON and strict base64url rejects padding,
non-canonical spellings and the wrong decoded length.

```text
routeBytes = first-16(SHA-256(UTF-8("den/playback-receipt-route/v2") || 0x00 || authority))
route      = lowercase-hex(routeBytes)

proof = base64url(HMAC-SHA-256(
          key = authority,
          message = UTF-8("den/playback-receipt-proof/v2") || 0x00 || UTF-8(JCS(operation))))
```

`operation` is one of the exact objects in §§3–6. Unknown or missing members, a non-JCS-equivalent decoded value,
an unsafe integer, a bad identifier, or a proof which does not compare in constant time is invalid. `nonce` is 16
fresh random bytes as 32 lowercase hex. `issuedAt` is Unix ms and MUST be within five minutes of Edge's clock.

Every proof-bearing request is authenticated first as an ordinary member of `operation.household`. Edge then
decodes `authority`, derives `route`, requires it to equal `operation.route`, and verifies `proof`. The request body
is exactly:

```json
{"authority":"AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8","operation":{},"proof":"base64url-hmac"}
```

The empty object above is replaced by the applicable operation. Nothing else is accepted. TLS is required. A proof
does not grant ordinary library access and an ordinary member claim does not grant a route without its authority.
The complete JSON body is at most 4 KiB. Edge answers malformed shape, encoding or clock with `400
{"error":"invalid_receipt_operation"}`, absent or wrong household membership with `403
{"error":"not_a_member"}`, and a route/proof mismatch with `403 {"error":"invalid_receipt_proof"}`. Those
decisions happen in that order, disclose no route existence and have no side effect.

For each route-administration generation (`register`, `rebind`, `rotate`, `revoke`), Edge retains a SHA-256 digest
of the accepted full operation through the route's seven-day retirement horizon. An exact retry is idempotent and
returns the original answer. A different administration operation at an already-used generation is `409
{"error":"receipt_generation_conflict"}`. Thus a captured stale proof cannot rebind, rotate or revoke newer state.
Activation is separately idempotent by household and exact operation digest; it intentionally names the already
registered generation. `playback` is idempotent under its authenticated upstream request id; `drain` is read-only
and `ack` is cumulative and idempotent.

The deterministic derivation and proof are pinned by [`vectors/playback-receipts-v2.json`](../vectors/playback-receipts-v2.json).

## 3. Route administration

All endpoints in this revision are capability-gated by Edge's advertised `playbackReceipts: 2`. If absent or below
2, a client performs no v2 request and treats durable completion recovery as unavailable. A v2 endpoint on an Edge
without support returns exactly `426 {"error":"playback_receipts_v2_unsupported"}`.

`PUT /playback/receipts/v2/route` accepts `register`, `rebind`, or `rotate`. `DELETE
/playback/receipts/v2/route` accepts `revoke`. Each request uses §2's wrapper.

### 3.1 Register

```json
{"activity":"10000000000000000000000000000001","binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","generation":1,"household":"00000000000000000000000000000001","issuedAt":1790000000000,"kid":"2a37dd6f422e1a735b8a9f7f8b377e12","nonce":"01010101010101010101010101010101","op":"register","public":"base64url-x-wing-public-key","route":"d4b68422b2b00134a42a7a585c60a5ea","v":2}
```

`household`, `activity`, `binding`, `route`, `kid` and `nonce` are 32 lowercase hex. `public` is the canonical
base64url encoding of exactly one assistant-v1 X-Wing public key and `kid` MUST be its assistant-v1 key id. Before
accepting, Edge proves that the same requester can access both `household` and `activity`: the normal request member
claim names the household and the sensitive `x-den-activity-member` header carries an ordinary member claim for the
exact activity id. The header is never forwarded or logged. The activity library MUST differ from the household.
Registration fails closed before storing anything if either membership is absent. The response is exactly:

```json
{"binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","expiresAt":1790604800000,"generation":1,"route":"d4b68422b2b00134a42a7a585c60a5ea","v":2}
```

`expiresAt` is seven days after the last successful authenticated route use. Registration is lazy: selecting or
opening a profile alone does not create a key, publish a route, start a drain, or load receipt crypto.

### 3.2 Same-profile activity rebind

```json
{"activity":"10000000000000000000000000000002","binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","generation":2,"household":"00000000000000000000000000000001","issuedAt":1790000001000,"nonce":"02020202020202020202020202020202","op":"rebind","previousActivity":"10000000000000000000000000000001","route":"d4b68422b2b00134a42a7a585c60a5ea","v":2}
```

The authority and stable binding prove it is the same profile; membership proves the new activity library is usable.
Edge requires the current activity and generation to equal `previousActivity` and `generation - 1`, then atomically
changes the destination. Old callbacks and queue entries remain on this route and apply to the successor activity;
their sealed generation remains verifiable under §7. The old activity's loss of membership after the commit does
not invalidate them. A different profile cannot rebind the route because it lacks the authority, and changing
`binding` is invalid rather than a new profile.

The response has the same shape as registration with the new generation. A client MUST fence every await against
the still-current household, profile, binding and replacement attempt, and MUST read back the successor's authority
before allowing the old activity library to retire.

### 3.3 Receipt-key rotation

```json
{"binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","generation":3,"household":"00000000000000000000000000000001","issuedAt":1790000002000,"kid":"3a37dd6f422e1a735b8a9f7f8b377e12","nonce":"03030303030303030303030303030303","op":"rotate","public":"base64url-x-wing-public-key","route":"d4b68422b2b00134a42a7a585c60a5ea","v":2}
```

The new `kid` and `public` obey registration's rules and differ from the current key. New playbacks use the new
generation and key. The profile keeps old private keys until no unexpired callback or queue entry can name them,
at most seven days after their last issue. Edge keeps corresponding public metadata for that same bound. Rotation
does not change route, binding, activity or already-sealed work.

### 3.4 Revoke and deletion

```json
{"binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","generation":4,"household":"00000000000000000000000000000001","issuedAt":1790000003000,"nonce":"04040404040404040404040404040404","op":"revoke","route":"d4b68422b2b00134a42a7a585c60a5ea","v":2}
```

On profile deletion, or an explicit receipt-authority reset, the client revokes before retiring the activity
library. Edge atomically marks the route retired, refuses new playback and callbacks, deletes its queued ciphertexts,
wakes its waiters, and retains only a non-resurrectable route tombstone through the seven-day horizon. A late
callback is `410 {"error":"receipt_route_retired"}`. Drain and ACK give the same error. Exact revoke retry succeeds;
no operation can make a retired route live. If deletion cannot prove revocation, profile deletion remains visibly
pending/retryable and MUST NOT invent success or expose its activity to another route.

Revoke returns exactly `{"retired":true,"route":"<route>","v":2}`. Rebind and rotation return registration's
response shape at the accepted generation and refreshed expiry.

Idle expiry is not deletion. At `expiresAt`, Edge performs the same retirement and purge. Any authenticated route
use except an invalid request, failed callback or empty long-poll refreshes expiry to `now + 7 days`; callback and
entry expiry never extends past seven days from playback issue or `endedAt`, respectively. Re-registration after
expiry uses a fresh authority, route and binding.

## 4. Playback opt-in and Hearth negotiation

A client requests a receipt only for an exact live persistent-profile authority. The ordinary authenticated
`POST /den/<config>/playback` body gains one optional `receipt` member containing §2's wrapper with this operation:

```json
{"binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","generation":3,"household":"00000000000000000000000000000001","issuedAt":1790000100000,"nonce":"05050505050505050505050505050505","op":"playback","request":"65c74c15a686187bb2bbf5e0073f166f8b22dcb79950a9c7af719cfe1c10ddfc","route":"d4b68422b2b00134a42a7a585c60a5ea","v":2}
```

`request` is lowercase hex SHA-256 of the existing authenticated upstream idempotency-key bytes. Edge requires the
same request id, current route/binding/generation and household, snapshots the current activity/key metadata, and
creates a fresh 32-byte callback bearer and 16-byte receipt `id`. Replacement, rotation, selection or policy changes
after this point cannot retarget that snapshot.

Edge attaches v2 context only if its trusted Hearth capability advertisement contains `playbackReceipts: 2`.
It removes the client `receipt` wrapper (especially `authority` and `proof`) before forwarding and replaces it with
trusted private-hop context; Hearth never receives the authority.
Hearth accepts it only on Edge's authenticated private hop and preserves the exact household, route, binding,
generation, kid, public key, id and callback through the playback lifecycle. The context never comes from an
untrusted client header. Hearth replies with an end capability as in v1 and seals only checked terminal evidence.

If route authority is absent/invalid/stale, Edge lacks v2 support, the route expired, or Hearth does not advertise
v2, playback still proceeds but no callback/end receipt context is created. The playback answer includes exactly
`"receipt":{"v":2,"status":"unavailable"}` when the caller requested v2 and recovery could not be attached;
success uses `status: "attached"`. A caller which omitted v2 gets no inferred receipt. There is no downgrade to v1,
Owner, the selected profile or another route. UI describes unavailable background recovery without claiming that
progress was saved; retry starts a new playback operation rather than attaching authority to an existing one.

## 5. Callback queue, drain and ACK authority

Hearth calls `POST /playback/receipts/v2/callback` with the callback bearer and exactly `{"sealed":"..."}`.
The bearer is bound to one household, route, binding, generation, kid, receipt id and playback request and expires
seven days after issue. Its first accepted ciphertext fixes a SHA-256 digest and route-local `seq`. An identical
retry returns exactly `{"queued":N,"seq":S,"v":2}` with the original `seq`, even after ACK, and never recreates
work; a different retry is `409
{"error":"callback_conflict"}`. An unknown/expired callback is `401 {"error":"invalid_callback"}`. A retired route
uses §3.4's stable `410`.

Each route has an independent monotone sequence and queue. A route holds at most 25 ciphertexts, and all routes in
one household hold at most 100. Full means full: no existing entry is evicted. Route full is `409
{"error":"receipt_route_full"}`; household full is `409 {"error":"receipt_household_full"}`. Callback records are
bounded to 512 per household and use the same seven-day expiry. Implementations also retain v1's per-address and
per-capability attempt limits. Bounds are checked before allocation and ciphertext is at most 2,539 base64url
characters / 1,904 decoded bytes.

`POST /playback/receipts/v2/drain[?wait=S]` uses §2's wrapper with:

```json
{"binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","generation":3,"household":"00000000000000000000000000000001","issuedAt":1790000200000,"nonce":"06060606060606060606060606060606","op":"drain","route":"d4b68422b2b00134a42a7a585c60a5ea","v":2}
```

It returns every waiting entry for only this route, without removing it:

```json
{"binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","messages":[{"generation":2,"seq":1,"sealed":"base64url-hpke"},{"generation":3,"seq":2,"sealed":"base64url-hpke"}],"route":"d4b68422b2b00134a42a7a585c60a5ea","v":2}
```

A stale requested generation may drain because the authority and binding still own the route; the answer names each
entry's sealed generation. A client drains only while the same profile runtime and successor activity are current.
A device that lacks the profile, authority, target activity or required private key neither drains nor acknowledges.

`POST /playback/receipts/v2/ack` uses the wrapper with this operation:

```json
{"binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","generation":3,"household":"00000000000000000000000000000001","issuedAt":1790000201000,"nonce":"07070707070707070707070707070707","op":"ack","route":"d4b68422b2b00134a42a7a585c60a5ea","upTo":2,"v":2}
```

It removes only that route's entries with `seq <= upTo`, after preserving callback idempotency bindings, and returns
`{"queued":0,"upTo":2,"v":2}`. Repeating an ACK at or below the route's high-water mark succeeds unchanged. An ACK
above the greatest sequence ever issued is `400 {"error":"invalid_receipt_seq"}` and has no effect.

The client processes in sequence order and ACKs only the greatest contiguous terminally handled prefix. Applied,
already-applied, expired, canonically malformed, wrong-route/binding/generation/key/household and authenticated
ciphertext/HPKE-open failures are terminal rejection and may advance that prefix after privacy-minimized local diagnostics.
A missing private key, unavailable successor activity, failed atomic write/readback or cancelled/replaced runtime is
retryable and stops the prefix before that entry. Thus one damaged route cannot block another profile, while a
temporarily unavailable key cannot be silently discarded. Queue expiry eventually bounds one route's blockage.

Drain is foreground/event-driven after profile sync or a wake; it is not a polling loop. At most one drain per route
runs in a process. Cancellation is checked before open, apply, readback and ACK.

## 6. Routed-only activation and old clients

Edge keeps one receipt mode per household: `legacy` or `routed`. Legacy is the default and preserves v1 unchanged,
including for a household without profiles. Registering routes alone does not switch mode.

After rollout policy has established that the household is profile-aware and its supported clients can present v2,
an authenticated member explicitly calls `POST /playback/receipts/v2/activate` with §2's wrapper and:

```json
{"binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","generation":1,"household":"00000000000000000000000000000001","issuedAt":1790000300000,"nonce":"08080808080808080808080808080808","op":"activate","route":"d4b68422b2b00134a42a7a585c60a5ea","v":2}
```

The named live registered route proves the capability is usable. Activation atomically and irreversibly changes the
household to `routed`, prevents new v1 callback creation, and moves every existing v1 callback and queued ciphertext
to an unreadable quarantine which is deleted at its original expiry (never more than seven days). Quarantine cannot
be drained, rebound, converted, opened or acknowledged. In-flight v1 callbacks receive exactly `410
{"error":"playback_receipts_v1_retired"}` and do not enqueue.
An exact activation retry returns `200 {"mode":"routed","v":2}`. A different route attempting to activate an
already-routed household receives that same non-identifying answer and cannot observe the first route.

On a routed household, v1 drain and ACK return exactly `426
{"error":"playback_receipts_v2_required","minimum":2}`. An older client may still play and save progress while it is
open, but managed playback requested without a valid v2 route creates no durable receipt. This is the stable
unsupported outcome; Edge never gives an older client another profile's v2 queue and never silently falls back.

Legacy households continue v1 until a future profile-aware migration explicitly performs activation. A v2 client
must not activate merely because Edge advertises support. Reduced Tizen 3 has an explicit product exclusion: it
does not load v2 crypto, register, activate, drain or ACK. It keeps the existing localized, accessible,
remote-focusable handoff to a capable linked device and never represents recovery as complete. Current Tizen,
LG/webOS, browser, iPhone, iPad and tvOS have no protocol exclusion.

## 7. HPKE envelope and receipt

X-Wing, `SealBase`, strict encoding and `kid` are assistant-v1 §§2–3. For the callback's snapped generation:

```text
aad = JCS({"binding":binding,"generation":generation,"household":household,
           "id":id,"kid":kid,"route":route,"v":2})
message = UTF-8(JCS(receipt))
enc, ct = SealBase(pkR = pk, info = UTF-8("den/playback-receipt/v2"),
                   aad = UTF-8(aad), pt = message)
sealed = base64url(enc || ct)
```

The plaintext is exactly:

```json
{"binding":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","completed":false,"durationSeconds":2400,"endedAt":1790000400000,"episode":3,"expiresAt":1790605200000,"generation":3,"household":"00000000000000000000000000000001","id":"94619420c64002287a3ad7ecad5dfa6b","kid":"3a37dd6f422e1a735b8a9f7f8b377e12","positionSeconds":2310,"route":"d4b68422b2b00134a42a7a585c60a5ea","season":2,"title":{"id":1399,"type":"tv"},"v":2}
```

The AAD and plaintext route members MUST be equal to one another and to the requested lane/envelope. Receipt fields
otherwise retain v1 §3's exact types and ranges: movie omits season/episode; episode has both; position is within a
positive duration; terminal evidence alone sets `completed`; `endedAt` is receipt of last player evidence; and
`expiresAt` is `endedAt + 7 days` (safe-integer saturation is invalid, not accepted). No terminal evidence means no
receipt. UTF-8 JCS plaintext is at most 768 bytes. Domain, AAD and exact `v` prevent v1/v2 substitution.

A client selects the private key named by the entry's generation and `kid`; it does not try unrelated profile keys.
It rejects a non-canonical/overlong plaintext, unknown member, AAD mismatch, unretained route generation, receipt id
already in the durable replay set/current batch, `endedAt` over five minutes in the future, age over seven days, or
expired entry. Rejection never probes another profile.

## 8. Apply, replay and newer progress

An accepted receipt becomes the same ordinary library-v4 progress operation v1 defines, but against the route's
current activity library after any proven same-profile rebind. Its stamp is `[endedAt, 0, device]`, seconds is
`positionSeconds`, and value is 1 when completed or `positionSeconds / durationSeconds` otherwise. The route's
profile-scoped `set:playback-receipts-applied[id]` record is written at the application time.

Progress and replay record MUST become durable atomically and MUST be read back before ACK. Den #719 remains the
native Apple atomicity blocker; v2 does not weaken it. Library merge means a progress write after `endedAt` wins.
Duplicate callback, drain, another device, delayed generation and late ACK are harmless through callback binding,
the replay set and cumulative ACK. A receipt never writes household activity or another profile's replay set.

## 9. Threat model and privacy

Edge is honest-but-curious and may be compromised after storage. It learns household, opaque route/activity/key
bindings, per-route timing/count/size and replacement/rotation events. It does not learn profile id, role, name,
policy, title, episode, position or completion. Stable opaque routes permit within-household correlation for their
life; this is required routing leakage and they MUST NOT cross household boundaries or enter analytics.

Hearth necessarily sees title and terminal evidence while serving playback, and receives only Edge-authenticated
opaque route context. It has no authority secret or private receipt key. A stolen callback can submit at most one
fixed ciphertext to its already-bound route. A stolen authority plus household membership controls that one route
and is therefore protected like the profile activity key. Household membership alone cannot enumerate or drain
routes; wrong member/household/route/binding/key/generation proofs fail before state disclosure.

Malformed input, v1/v2 substitution, cross-household ciphertext, Owner/Kid substitution, replay, reordered delivery,
offline retry, replacement, deletion, rotation and expiry fail closed under §§2–8. Availability is not guaranteed:
queue exhaustion or Edge loss may lose a receipt, but cannot retarget it or overwrite newer progress.

## 10. Bounds, performance and qualification

Implementations MUST preserve the exact request/body/ciphertext bounds above, bounded parsing depth, constant-time
credential comparison, lazy crypto/module loading, one foreground drain, and no retained player, decoder or media
state. Route lanes avoid cross-profile head-of-line blocking but add metadata, proof hashing and more request bodies;
no performance gain is claimed.

Downstream qualification must record matched before/after startup and complete-graph Web/TV bundle sizes; native
artifact/slice sizes; registration/playback/drain/ACK request count and latency; Edge storage/lock contention and
100-entry household/25-entry route behavior; Core/Hearth hash, HPKE CPU and memory; client open/apply CPU, memory,
SQLite/I/O and energy; long-poll/wake behavior; and seven-day purge. A regression introduced by v2 must be resolved
or explicitly accepted before rollout. Source/light contract checks are not device, engine or performance evidence.

## 11. Required target outcomes and dependent work

- Shared codec/authority and deterministic HPKE vectors: [den-core #120](https://github.com/oxyc/den-core/issues/120).
- Routed storage/API and browser, LG/webOS and current Samsung/Tizen consumers:
  [den-edge #1165](https://github.com/oxyc/den-edge/issues/1165) and the canonical Web investigation
  [den-edge #1162](https://github.com/oxyc/den-edge/issues/1162).
- Trusted v2 context and sealing: [den-hearth #225](https://github.com/oxyc/den-hearth/issues/225).
- iPhone, iPad and tvOS routing/apply: [den #776](https://github.com/oxyc/den/issues/776), preserving the atomicity
  work in [den #719](https://github.com/oxyc/den/issues/719).
- Cross-platform completion and performance accounting: [den #481](https://github.com/oxyc/den/issues/481).

Until those land and qualify their production paths, v2 is specified but unimplemented on every target. Tizen 3 is
the only explicit product exclusion and keeps its handoff. Visitor is protocol-inapplicable by design, not a target
implementation omission.
