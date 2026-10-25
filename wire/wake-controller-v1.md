# Wake controller, v1

How a household-local controller learns that a TV's sealed play queue received a message, wakes the physical
Apple TV and asks it to launch Den. The controller is a trigger only: it never receives a play, opens a sealed
message or reports that playback succeeded.

This contract joins the household play queue ([inbox-v1](inbox-v1.md) §5) and a TV's assistant lane
([assistant-v1](assistant-v1.md) §16) without changing either message. A TV still drains, authenticates, freshness-
checks, deduplicates and applies the request. [`../vectors/wake-controller-v1.json`](../vectors/wake-controller-v1.json)
pins the identifiers below.

Words: *MUST* is a rule a party breaks at the cost of another party's privacy, authority or reliable playback;
*should* is advice.

## 1. Boundaries

The controller belongs to the household node. It keeps an authenticated **outbound** connection to den-edge and
uses a LAN-only device protocol to wake and launch the TV. There is no browser-to-localhost route, inbound router
port, media relay or controller decryption key.

Every den-edge route below is HTTPS in production. A controller MUST authenticate the server and MUST NOT send its
credential over plaintext HTTP except to an explicitly configured loopback test server.

For each physical target the controller stores its own target id and device credentials, and registers only:

| Field | Shape | Meaning |
|---|---|---|
| `id` | 16 random bytes, lowercase hex | Controller-local stable target id |
| `name` | pairing-v1 §3 clean label | Display and disambiguation label |
| `inbox` | 32 bytes, lowercase hex | Public wake id for the household play queue (§2) |
| `tv` | 16 bytes, lowercase hex | Existing assistant lane id (assistant-v1 §16) |
| `capabilities` | distinct strings from §4 | What the controller can request of the physical receiver |

The controller MUST NOT receive or store the household play key, its 48-hex inbox credential, the inbox encryption
key, a library key, assistant keys, sealed bodies, title data or media URLs. The wake id and lane id grant no read,
drain, append or acknowledgement authority.

## 2. Public queue identifiers

### Household inbox wake id

The canonical inbox credential is the 24-byte `inbox` derivation in pairing-v1 §6, encoded as exactly 48 lowercase
hex characters. Its public wake id is:

```
wakeInbox = lowercase_hex(
  SHA-256(UTF8("den/wake/inbox/v1:") || UTF8(canonicalInboxCredential))
)
```

The input is the 48 ASCII hex characters, not their decoded 24 bytes. A value containing uppercase, whitespace,
a prefix, or anything other than exactly 48 lowercase hex characters is not canonical and MUST be refused rather
than normalized. This gives every implementation one public id for a credential.

The sender continues to authenticate an append with the credential. den-edge derives `wakeInbox` after validating
that credential and uses only the derived id for controller matching. It never returns the credential to a
controller. SHA-256 here is an identifier transform, not a password hash: the input has 192 random bits.

### Assistant lane id

`tv` is assistant-v1 §16's existing lane id: 16 bytes, lowercase hex, derived from the same household play key with
HKDF-SHA256 salt `den/link/v1` and info `assistant-tv`. It is already public to den-edge and den-mcp. This contract
does not add another assistant identifier or change its derivation.

Both identifiers change when the TV makes a new household play key after a library-key reset. A target snapshot
with the new pair replaces the old pair atomically.

## 3. Controller enrollment and authentication

A member of the library enrolls the household controller:

```
POST /wake/controllers
x-den-library-member: <library id>:<member proof>
{"name":"Living-room Hearth"}

→ 201 {"controller":"<32 lowercase hex>",
       "credential":"<controller>.<43 unpadded base64url>"}
```

`controller` and the credential's 32-byte secret are fresh CSPRNG output. The credential is returned once. den-edge
stores a verifier, not the secret. The member transfers it to the local controller through the authenticated setup
flow and the controller keeps it in permission-restricted persistent state.

Controller routes take:

```
x-den-wake-controller: <credential>
```

The credential authenticates exactly one controller and one library. It is not a library member proof and cannot
use inbox, assistant or library routes. A missing, malformed, unknown, revoked or wrong-library credential is
`401 invalid_controller` and MUST NOT reveal which part failed.

```
DELETE /wake/controllers/{controller}
x-den-library-member: <library id>:<member proof>
→ 204
```

Deletion revokes the credential, removes its registrations and pending wake events, and is idempotent. A library-
key reset does the same for every controller in the old library. At most **4 controllers** may stand for one
library (`409 controllers_full`). Enrollment is at most **5 attempts an hour per library** (`429 rate_limited` with
`Retry-After`). Names follow the clean-label rule and invalid JSON or a bad name is `400 invalid_controller`.

## 4. Target snapshot and long poll

The controller registers and waits in one bounded request:

```
POST /wake/poll?wait=S
x-den-wake-controller: <credential>
content-type: application/json

{"v":1,
 "targets":[{"id":"<32 hex>","name":"Living Room","inbox":"<64 hex>","tv":"<32 hex>",
             "capabilities":["companion-wake","app-launch"]}],
 "acks":["<event id>"],
 "stages":[{"event":"<event id>","target":"<target id>","stage":"launch-requested",
            "status":"ok","at":1790000001234,"elapsedMs":842}]}

→ 200 {"v":1,"serverTime":1790000002000,
           "acceptedTargets":["<target id>"],
           "events":[{"event":"<32 hex>","target":"<target id>",
                      "sources":["inbox","assistant"],
                      "firstAcceptedAt":1790000000000,"lastAcceptedAt":1790000001000}]}
```

- `S` is integer seconds from **0 through 25**; absent is 0. With no pending event, den-edge may hold the request
  for that long. It may answer early, so a controller MUST back off after an early empty answer rather than spin.
- The decoded request body is at most **32 KiB**. It has exactly version 1, at most **16 targets**, **32 acks** and
  **32 stages**. Arrays contain no duplicate ids. Unknown object members are ignored; an unknown `v` is
  `400 unsupported_version`. Other shape failures are `400 invalid_poll`, whole: no snapshot, ack or stage applies.
- `targets` is a complete snapshot, not a patch. A valid poll atomically replaces this controller's prior snapshot.
  An empty snapshot unregisters its targets. Target ids and `(inbox, tv)` pairs are unique in the library. A
  collision with another controller is `409 target_conflict` and changes nothing. Names are labels, not identity;
  senders must disambiguate when more than one target matches a name.
- `capabilities` has at most 8 distinct strings of at most 32 ASCII characters. A v1 target needs both
  `companion-wake` and `app-launch`. Unknown capabilities are ignored for forward compatibility, but do not
  satisfy either requirement.
- A valid poll is the controller's keepalive. den-edge persists the snapshot, so a process restart does not erase
  registration, but `wakeable` is true only while a poll is held or for **60 seconds** after the last valid poll.
  The controller polls continuously and sends its full snapshot again after every reconnect or den-edge restart.
- den-edge permits one held poll per controller. A new one answers the old one empty. A controller makes at most
  **6 non-held polls a minute**; excess is `429 rate_limited` with `Retry-After`. Held polls and immediate answers
  caused by events do not consume that retry budget.

This is long polling, not a promise that an HTTP connection stays up. The controller reconnects with capped,
full-jitter exponential backoff, then re-sends the whole snapshot. Its HTTP timeout MUST outlast `S`.

## 5. Wake events, coalescing and acknowledgement

After accepting an append to a registered household inbox or assistant lane, den-edge marks the matching target
pending. The accepted sealed message and queue behavior are unchanged. An event contains only its random 16-byte
id, target id, source kinds and Edge acceptance times. It contains no body, title or sender.

Pending events are **level-triggered and bounded**:

- There is at most one pending event per target and therefore at most 16 in a response. The first append sets
  `firstAcceptedAt`. Another append before acknowledgement adds its source, updates `lastAcceptedAt` and replaces
  the event id. This replacement makes an acknowledgement racing with a new append harmless.
- Events are returned oldest `firstAcceptedAt` first. A reconnect receives every still-pending event again. The
  controller durably deduplicates event ids and runs at most one wake/launch attempt per target at a time.
- An `acks` entry clears a pending event only when both its event id and authenticated controller match exactly.
  An unknown or replaced id is a no-op. This acknowledges only delivery of the **wake notification**. It does not
  drain, acknowledge or claim success for the inbox or assistant request. The controller sends it only after the
  corresponding wake/launch attempt reaches a terminal outcome and that outcome is durably recorded.
- den-edge keeps the pending marker as long as the underlying request can still matter, but no longer than the
  queue entry. It keeps a delivery watermark over its internal append order; acknowledgement advances that
  watermark without touching the queue. After a restart or later snapshot it reconstructs a marker for an
  unconsumed entry only when that entry is newer than the watermark. It never drops a new queue entry merely to
  keep wake state bounded.
- A controller uses the acceptance times only to avoid pointless late work. The TV remains authoritative: inbox
  `sentAt`, assistant `at`, the 120-second play fence, queue acknowledgement and replay checks are unchanged. It
  MUST NOT begin a new wake attempt when `lastAcceptedAt` is already more than 120 seconds before its current time;
  it records `stale-event` and acknowledges that wake notification.

Legacy paired inboxes used for settings, addons or handover have no registered wake id and MUST NOT produce wake
events. Only a TV's household play inbox and its assistant lane may be registered.

## 6. Lifecycle reports

`stages` is optional, bounded diagnostic metadata for a returned event. The v1 stages, in order where reached, are:

```
trigger-received
wake-requested
receiver-awake
companion-connected
app-known
launch-requested
receiver-ready
```

`status` is `started`, `ok` or `error`; `at` is Unix ms; optional `elapsedMs` is an integer from 0 through 300,000.
An error may add `reason`, one of `stale-event`, `offline`, `pairing-revoked`, `address-changed`, `app-missing`,
`connect-timeout`, `launch-timeout`, `protocol-error` or `internal`. den-edge keeps only the newest report per
event/stage for 10 minutes. It rejects neither a poll nor a target because a stage is late or out of order.

`launch-requested` means the Companion launch command was sent. `receiver-ready` means the controller can reach the
paired receiver and knows the configured app. Neither means Den is foregrounded, a queue was drained, the requested
title was selected, or playback began. Existing TV queue state and playback progress are the only playback truth.
Logs and reports MUST NOT include device credentials, queue credentials, keys, sealed data, titles or media URLs.

## 7. Registered, wakeable, online and playing

These are different states and MUST NOT be collapsed:

| State | Evidence |
|---|---|
| registered | A persisted controller target snapshot names the lane and inbox wake id |
| wakeable | Its controller has a current poll/keepalive and both v1 capabilities |
| online | Den on the TV drained its assistant lane within assistant-v1's 40-second window |
| playing | Existing TV playback/progress state says media is playing |

A sleeping receiver can be registered and wakeable while not online. A lifecycle report cannot make it online or
playing. When the lane is online, den-edge suppresses a wake event for its assistant append: Den is already draining
and pyatv cannot reliably prove which app is foregrounded. Household-inbox wake notifications may likewise be
suppressed only from equivalent, current Den receiver evidence, never from a controller lifecycle stage.

## 8. Sender views and compatibility

`GET /assistant/tv` remains assistant-v1's Bearer, write-scope route. Each lane may additionally carry
`"registered": <bool>` and `"wakeable": <bool>`. A registered lane is listed even if it has never drained since
den-edge started; then `online` is false and `lastSeenAt` is null. Older den-edge omits the fields, which means false.

den-mcp may address a named TV when it is online **or wakeable**. With no name it chooses the sole online or wakeable
TV; several possible TVs still require disambiguation. Its confirmation remains driven by queue/taken/progress
state. A controller's launch report is not “Playing on &lt;TV&gt;”.

An authenticated member sender lists the same targets with `GET /wake/targets`; its `200` answer is
`{"targets":[{"id","name","tv","registered","wakeable","online","lastSeenAt"}]}`. It never returns inbox wake
ids or controller credentials. Old clients and controllers that know no wake routes keep the assistant-v1 behavior:
only online lanes are selectable and a manually opened TV drains as before.

## 9. Failure and privacy rules

- A revoked Apple TV pairing, missing/re-signed app, changed address, sleeping/offline receiver or timeout is a
  bounded stage error. It never starts an unbounded permission prompt or launches a different app.
- A controller disconnect changes `wakeable`, not `registered` or `online`. A den-edge restart begins with no live
  keepalive; the controller's next poll re-establishes it and the full snapshot.
- den-edge learns that a registered queue received a message and the controller's lifecycle timing. The controller
  learns only that one or both registered queues received something. Neither fact opens the sealed request.
- The controller cannot acknowledge the play, and a false lifecycle report can at worst mislead diagnostics. It
  cannot make den-edge report playback, alter the queue or bypass the TV's freshness and replay checks.
