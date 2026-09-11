# Pairing, v1

How a device joins a Den library — a phone, a laptop, or another TV — through den-edge, without den-edge
learning a secret or being able to swap one in. The two devices run CPace, a password-authenticated key
exchange, on a short code shown on one screen and typed or scanned on the other; den-edge only carries the
messages. [`../vectors/pairing-v1.json`](../vectors/pairing-v1.json) pins every value below; each client's tests
load it.

Words: *MUST* is a rule a client breaks at the cost of the other client or of the user's secrets; *should* is
advice.

## 1. Roles and the code

The **host** is a device already in the library, and shows the code; today that is a TV. The **joiner** is the device
joining, and types or scans it.

The host asks den-edge for a **nameplate** (§2) and generates the **secret** itself: 8 characters, each chosen
uniformly from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (no 0/O or 1/I), so 40 bits. The code is the nameplate
followed by the secret, shown as `ABCD-EFGH-JKLM`, and as a QR of `<den-edge origin>/#pair=ABCDEFGHJKLM`, which opens Den Web.
A URL fragment is never sent to a server, so den-edge sees the nameplate and never the secret.

A joiner reads a typed code by uppercasing it and dropping spaces and dashes. Anything that is then not 12
characters of the alphabet is refused as mistyped, never corrected into a guess.

With a PAKE, someone who doesn't know the secret — den-edge included — gets one online guess per pairing and
no offline guessing at all. Every failed pairing burns its code (§5), so a guess costs a visible failure on
the host.

## 2. Relay

den-edge keeps pairing sessions in memory. A session lives 10 minutes from `new`, and is gone once `d` has been
read or either side deletes it. Every slot is write-once.

| Route | Body | Answer |
|---|---|---|
| `POST /pair/new` | `{"sid": <32 hex>}` | `{"nameplate", "expiresAt"}`; 409 `sid_taken` |
| `POST /pair/open` | `{"nameplate"}` | `{"sid"}`, once; 409 `already_opened`; 410 `expired_or_unknown` |
| `PUT /pair/{sid}/{slot}` | `{"m": <base64url>}` | 200; 409 `already_written`; 410 once the session is gone |
| `GET /pair/{sid}/{slot}` | | `{"m"}`; 202 `{"status":"pending"}`; 410 |
| `DELETE /pair/{sid}` | | 200, idempotent |

- The host generates `sid`: 16 random bytes, lowercase hex. den-edge answers with a nameplate of 4 characters
  from the same alphabet, unique among live sessions.
- A nameplate opens once. A second `open` is refused, so the host sees a stranger's claim as a failed
  pairing rather than a quiet second device.
- Slots, in order: `a` and `c` are the joiner's, `b` and `d` the host's (§4). den-edge doesn't check which side
  writes a slot; the `sid` is the only capability, and CPace catches anything else.
- `new` and `open` are limited per client address, like `/link`. A message is at most 2 KiB.

## 3. CPace

The cipher suite is CPACE-RISTR255-SHA512 from draft-irtf-cfrg-cpace-21 §8.3, in the initiator-responder
setting: the joiner is party A, the host party B.

| Input | Value |
|---|---|
| `PRS` | the secret's 8 characters, UTF-8 |
| `CI` | `den/pair/v1`, UTF-8 |
| `sid` | the 16 bytes of the session's `sid` |
| `ADa` | `lv_cat("joiner", label)`, the joiner's label |
| `ADb` | `lv_cat("host", label)`, the host's label |

`lv_cat` is the draft's (each argument prefixed with its LEB128 length). A **label** is what the device calls
itself, as its list of linked devices shows it: "Mac · Chrome", "Living room". It is trimmed, has control
characters removed, and is at most 40 Unicode scalars; it MUST NOT be empty.

- The generator is `element_derivation(SHA-512(generator_string(DSI, PRS, CI, sid, 128)))` with DSI
  `CPaceRistretto255` (draft §8.1, §8.3).
- A scalar is 32 random bytes with the top 4 bits cleared, read little-endian. A scalar is used for one
  session and then forgotten.
- `ISK = SHA-512(lv_cat("CPaceRistretto255_ISK", sid, K) ‖ transcript_ir(Ya, ADa, Yb, ADb))`.
- A party MUST abort when the other's share doesn't decode as a ristretto255 element, or when `K` is the
  identity (32 zero bytes). The vectors' `invalidPoints` are both cases.
- `K` never leaves the function that computes `ISK` (draft §10.3).

## 4. Messages

Each message is bytes, sent in a slot as unpadded base64url.

| Slot | From | Bytes |
|---|---|---|
| `a` | joiner | `lv_cat(Ya, ADa)` |
| `b` | host | `lv_cat(Yb, ADb, Tb)` |
| `c` | joiner | `Ta` |
| `d` | host | the handover (§6) |

A reader MUST refuse a message whose lengths don't add up, that has bytes left over, whose share isn't 32 bytes
or whose tag isn't 64, or whose AD isn't `lv_cat` of the expected role and a valid label.

## 5. Confirmation and approval

Both sides confirm the key before anything secret is sent (draft §10.4):

- `macKey = SHA-512("CPaceMac" ‖ sid ‖ ISK)`
- `Ta = HMAC-SHA-512(macKey, lv_cat(Ya, ADa))`, `Tb = HMAC-SHA-512(macKey, lv_cat(Yb, ADb))`

In order:

1. The joiner writes `a`.
2. The host reads `a`, computes `ISK`, and writes `b`.
3. The joiner reads `b`, checks `Tb` in constant time, and only then writes `c`.
4. The host reads `c` and checks `Ta`.
5. The host MUST then ask its user, "Allow *joiner label*?". The label is authenticated: it is in `ADa`, so
   den-edge can't rename the device. Only on yes does the host write `d`.

A failed check, a declined prompt, a cancel or an expired session ends the pairing: that side MUST `DELETE` the
session, and the host shows a new code. A joiner that finds its session gone says the pairing was declined or
failed, not which.

## 6. Handover

The host seals one message to the joiner:

- `handoverKey = HKDF-SHA256(ikm: ISK, salt: sid, info: "den/pair/v1/handover")`, 32 bytes.
- `d = nonce (12) ‖ ciphertext ‖ tag (16)`: AES-256-GCM with a fresh random nonce and the additional data
  `den/pair/v1/handover`.
- The plaintext is a UTF-8 JSON object:

```json
{"v": 1, "host": "Living room", "linkKey": "<base64url, 32 bytes>", "libraryKey": "<base64url, 32 bytes>"}
```

- `libraryKey` is the library's key ([library-v2.md](library-v2.md) §1). A joiner MUST refuse a handover
  without it.
- `linkKey` is 32 random bytes the host makes for this joiner, and keeps with the joiner's label in its list
  of linked devices. It replaces the `inboxKey` den-edge used to generate. From it, HKDF-SHA256 with salt
  `den/link/v1`:
  - `inbox` (info `inbox`, 24 bytes, lowercase hex): the link's credential at den-edge — the `x-den-link`
    header of `/inbox`, `/plugins` and `/settings`, where a v1 link sent its `inboxKey`.
  - `enc` (info `enc`, 32 bytes): the key the link's inbox messages will be sealed under.
- Fields a client doesn't know are ignored.

## 7. What den-edge still sees

The nameplate, the `sid`, both public shares and both labels (associated data is not encrypted), message
sizes, timing and client addresses. None of it lets den-edge guess the secret offline, read the handover, or
pass as either device.

## 8. Replaces

- **`/link` v1**, where den-edge generated the `inboxKey` on claim and so held every link's credential.
- **Handover v1** ([library-v2.md](library-v2.md) §1), where the `libraryKey` travelled in a `/sync` backup
  den-edge could open.

A device linked under v1 pairs again once; after that the TV stops writing the key into its backup.
