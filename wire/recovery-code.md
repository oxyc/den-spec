# Recovery code, v1 — proposal

**Status: for review; the owner's decisions are in §13.** Nothing here is implemented.

A recovery code lets a person open their library on a new device when no device that holds it is at hand: every TV
lost or reset, a new Apple ID, or a browser with nothing linked. The code unwraps the **library key** ([library
v2](library-v2.md) §1); with the key, the device reads the library from den-edge like any other holder.
[`../vectors/recovery-v1.json`](../vectors/recovery-v1.json) pins every value below; each client's tests load it.

What already exists, and what this adds. A TV keeps the library key in iCloud Keychain, so a new Apple TV on the same
Apple ID with Keychain on gets the key with no code. Pairing ([pairing v1](pairing-v1.md)) needs a device that still
holds the library. The recovery code covers everything else, and it is the only path that does not depend on Apple or
on a surviving device.

**Den Web is the main path.** Most people will make and redeem codes in Den Web, where pasting and typing are easy;
the TV supports both, with the remote as the slower way in. Den Web's page is served by den-edge, so **a den-edge
serving a modified page can read a code typed into it** (§1). That is the one place where this design trusts den-edge
with a secret, and it is the same trust a browser already places in it for the library key.

Words: *MUST* is a rule a client or den-edge breaks at the cost of the user's secrets or of another client;
*should* is advice.

## 1. Threat model

| Threat | What holds |
|---|---|
| **den-edge reads what it stores** (its disk, its backups, its logs, its operator) | It holds, per entry, a locator, a sealed blob and the library the entry belongs to (§4, §5). Opening the blob takes the code. Testing a guess takes one Argon2id at 64 MiB (§3); the code has 110 random bits (§2), so an offline search is out of reach. Knowing which library an entry belongs to gains nothing toward the key; with one or two libraries per store it was never hidden anyway (§13, decision 7). |
| **den-edge swaps or forges an entry** | The blob is AES-GCM under a key only the code derives, with the locator in the additional data. A forged blob fails to open; a blob moved to another locator fails to open. |
| **den-edge withholds, deletes or rolls back an entry** | Not prevented: den-edge is trusted for availability, as it is for the library itself. Devices reconcile what den-edge holds against the library (§7): a lost live entry is posted again, and a stale one deleted. A key reset (§9) defeats any rolled-back code. |
| **A stolen code** | The code is the library key. The holder of a stolen code can do what any linked device can. Detection: den-edge counts opens (§5), which Settings shows. Response: turn the code off **and reset the library key** (§6, §9) — turning it off alone stops future opens, not a key already taken. The code is never stored, never logged and shown once (§6). |
| **Online guessing at den-edge** | One guess is one Argon2id on the guesser's side and one request; a request reveals only whether a locator exists, and finding one that exists means knowing the code. Rate limits (§5) keep den-edge from being a cheap existence oracle or load target; they are not what makes guessing fail — the 110 bits are. |
| **A lost code** | While any device holds the library: make a new one (§6), which turns the old one off. With every device and the code lost, the library is gone; den-edge cannot help, by design. |
| **An active den-edge serving Den Web** | **Not covered, and Den Web is the main path.** Den Web's code is served by den-edge, so a den-edge that serves a modified page can read a code typed or pasted into it, and anything else a browser holds, the library key included — today, with or without recovery codes. Making and redeeming on the TV avoids it. |
| **A device that was shut out by a key reset** | Its member proof names the old library, whose entries den-edge deleted with it (§5, §9), and den-edge no longer takes that proof. A new code exists only in the new library. |

## 2. The code

- **24 characters** from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (pairing v1's alphabet: no 0/O or 1/I): **22 data
  characters**, each chosen uniformly (110 bits), then **2 check characters**. Shown as six groups of four:
  `GEB2-LP9U-C63W-Q95U-NSLT-XMFL`.
- **Making one**: take 22 random bytes from the platform's CSPRNG; data character *i* is `ALPHABET[byte_i & 31]`
  (uniform, since 32 divides 256).
- **Check characters**: `h = SHA-256(UTF-8("den/recovery/v1/check") ‖ UTF-8(data))`; the first is
  `ALPHABET[h[0] >> 3]`, the second `ALPHABET[((h[0] & 7) << 2) | (h[1] >> 6)]` — the first 10 bits of `h`. A
  typo slips past them once in 1,024.
- **Reading a typed code**: drop whitespace (any Unicode `White_Space` character: a pasted code may carry a newline
  or a no-break space) and dashes. Anything left that is not ASCII is refused as **mistyped**, before any case
  mapping, so `ſ` never reads as `S`; the rest is uppercased as ASCII. Anything that is then not 24 characters of the
  alphabet is refused as **mistyped**; check characters that do not match are refused as **a typo**. Both are
  refused before any derivation or request, and never corrected into a guess.

## 3. Derivation

```
A        = Argon2id(password = UTF-8(data), salt = UTF-8("den/recovery/v1"),
                    t = 3, m = 65536 KiB, p = 1, tag = 32 bytes, version 0x13)      (RFC 9106)
locator  = HKDF-SHA256(ikm = A, salt = UTF-8("den/recovery/v1"), info = "locator", 16 bytes)
wrapKey  = HKDF-SHA256(ikm = A, salt = UTF-8("den/recovery/v1"), info = "wrap",    32 bytes)
```

`data` is the 22 data characters, without the check characters. Infos are UTF-8.

- **Why Argon2id**: memory-hard, so a GPU or ASIC search gains little over the device's own cost; RFC 9106 §4's
  second recommended setting (64 MiB, t = 3), with one lane because a browser runs one thread and lanes change no
  total work. It is margin on top of the code's entropy, not what the code's security rests on.
- **Partial disclosure.** The check characters let an attacker filter candidates *before* any Argon2id: a photo that
  hides two data groups but shows the last group leaves about 2^30 candidates that pass the check, each needing one
  Argon2id. A surviving candidate is still only a guess at a locator: testing it needs den-edge's table (or a backup of
  it) or an online `open`. So against a partial disclosure the code holds through §5's rate limits and the secrecy of
  den-edge's store, with Argon2id as the cost of each step — not through Argon2id alone. A code partly seen should be
  replaced.
- **Cost**: ~110 ms natively on an Apple-silicon Mac (measured with Node's Argon2id). The Apple TV and phone browsers
  are **not yet measured**; the parameters are pinned only once §12's measurements show both acceptable. A client
  runs it off the main thread and shows progress.
- **A fixed salt** is deliberate: the device has nothing to look up before it derives the locator, and a per-user
  salt only defends against precomputation, which 110 random bits already rule out. Deriving the locator any faster
  than the wrap key would let den-edge test guesses against the locator at that speed, so both come from `A`.
- **One implementation**: den-core (§12), so the TV and Den Web cannot disagree on a parameter.

## 4. The entry

What a device stores at den-edge for a code:

- `locator`: 16 bytes, lowercase hex in requests. den-edge cannot compute it from the library id or the reverse.
- `sealed`: unpadded base64url of `nonce (12) ‖ ciphertext ‖ tag (16)`, AES-256-GCM under `wrapKey` with a fresh
  random nonce and the additional data `UTF-8("den/recovery/v1") ‖ locator` (16 raw bytes). The plaintext is UTF-8
  JSON:

  ```json
  {"v": 1, "libraryKey": "<base64url, 32 bytes>", "createdAt": 1790000000000}
  ```

  A reader MUST refuse a plaintext without a 32-byte `libraryKey` and ignore fields it does not know.

The same `{locator, sealed}` is also kept in the library (§7), so a device can post it again without the code.

## 5. den-edge

| Route | Body / headers | Answer |
|---|---|---|
| `POST /recovery` | `{"locator", "sealed"}`; `x-den-library-member: <id>:<member>` | `201 {"createdAt"}`; `403 forbidden` without a valid member proof; `409 locator_taken`; `409 recovery_full` |
| `GET /recovery` | `x-den-library-member: <id>:<member>` | `200 {"entries": [{"locator", "createdAt", "opens", "lastOpenedAt"}]}`, that library's entries; `403 forbidden` |
| `DELETE /recovery` | `{"locator"}`; `x-den-library-member: <id>:<member>` | `200 {"deleted": true \| false}`: idempotent, and `false` for a locator this library has no entry under; `403 forbidden` |
| `POST /recovery/open` | `{"locator"}`, no credential | `200 {"sealed"}`; `404 {"error": "unknown_code"}` |

- **Owner.** An entry belongs to the library whose member proof made it, and den-edge records that library's id with
  it. The member proof is the one den-edge already checks for relayed household requests (library v2 §1,
  `holds_member_hash`): a library's registered `member`, or its token before one is registered. Only that library's
  proof lists or deletes its entries; a locator under another library reads as absent.
- **Storage**: one small table in den-edge's durable store, beside the libraries, so a store backup carries it. Per
  entry: `locator`, `sealed` (at most 512 characters), `library`, `createdAt`, `opens`, `lastOpenedAt`. Entries never
  expire. At most **4** entries per library (a live code, one being made, and room for clean-up); past that,
  `409 recovery_full`.
- **Cascade.** When den-edge deletes a library (`DELETE /lib/{id}`, which a key reset ends with), it deletes that
  library's entries in the same transaction. `POST /recovery` is ordered against it: den-edge checks the member proof
  again under the same lock that the cascade and the library's retirement hold, so an entry is never written for a
  library already retired (it would open, and no proof could list or delete it).
- **Opening** names no library and needs no credential: it is the new device's path. It increments `opens` and sets
  `lastOpenedAt` on any successful lookup, the owner's own redeems included. Both are advisory: den-edge is untrusted
  and can lie.
- **Secrets stay out of URLs and logs.** Locators travel in bodies, never in a path or query. den-edge MUST NOT log a
  locator, `sealed` or a member proof; its request line logs the route and the status only.
- **Limits**, per **visitor** address: the address den-edge's `client_ip` reports, which is what a proxy listed in
  `TRUSTED_PROXIES` says the visitor was (IPv6 collapsed to a /64), never the proxy's own address. `open` 5 per 10
  minutes and 20 per day; `POST`, `GET` and `DELETE /recovery` 60 per hour. Past one: `429 rate_limited` with
  `Retry-After`. No store-wide limit on `open`, and none that would fall on a proxy's address: either would let anyone
  lock every visitor out of recovery, and neither buys security. That includes the limiter's own table: when it is
  full it MUST evict (the bucket nearest expiry, say) rather than refuse new visitors, or filling it from many
  addresses is a store-wide limit by another name; and recovery's buckets MUST NOT share a table whose refusal falls
  on other routes. The owner routes are counted after the member proof is checked, so anonymous requests make no
  bucket.
- Lookups are by the exact locator.
- `/recovery` routes carry no `x-den-wire` and are not fenced by a library rewrite: entries are not rows of the log.

## 6. Making, showing, replacing, turning off

**Who**: any device that holds the library — a TV, or a linked browser. A holder can already copy the key; a code
gives it no more. Not offered for a device's own library (library v2 §1), which den-edge does not hold.

**Making**:

1. Make the code (§2); derive (§3); seal the plaintext.
2. Write the entry into `set:recovery` (§7) as **`pending`**, by compare-and-set. A den-edge entry is never made that
   the library does not already name.
3. `POST /recovery`. On `409 recovery_full`, reconcile (§7) and post once more; still full, stop and say why. On
   `409 locator_taken` (never expected: 110 bits) start again from 1.
4. **Show the code**, once, and **confirm it was saved**: the person types the code's last group (four characters),
   compared on the device and never sent. A mismatch keeps the screen. Leaving without confirming nulls the pending
   entry and `DELETE`s it; a closed tab or a crash leaves that to the next reconcile (§7 *Abandoned*).
5. By compare-and-set, turn this entry from `pending` into `live` and null the previous live entry, in one write. A
   conflict is re-read: if this entry is no longer pending (abandoned by another device), or the fresh read shows a
   live entry that was not live in the version step 2 was based on (another device made a code meanwhile), this
   attempt **loses**: it nulls and `DELETE`s its own entry and, before the screen closes, says "This code wasn't saved
   — discard it, a code was just made on *<device>*" (or "…setup took too long"). Otherwise retry the write. The
   compare-and-set orders concurrent makes; no clock decides.
6. `DELETE` the entry step 5 nulled. A failure is left to the next reconcile.

The screen says what the code is and is not: "Anyone with this code can open your library. Write it down or keep it
in a password manager. Den can't show it again." Den Web also offers **Copy**, and says beside it that clipboard
history and the system's clipboard sync can carry the code to other devices, and that the person should clear the
clipboard once the code is saved. Den Web does not promise to clear it: a browser lets a page read the clipboard
(which a clear only if it still holds the code needs) only inside a gesture, and an unconditional clear would destroy
whatever the person copied since. The code MUST NOT be written to the library, to storage, to a log
(the TV's remote log included), to analytics or to a URL; a client holds it in memory only until the screen closes or
derivation is done.

**Replacing** is making: the new code ends the old one. **Turning off** nulls the live entry and `DELETE`s it,
behind a confirmation.

**Status**: Settings shows the live entry's `createdAt` and the device that made it (`by`, joined to `set:devices`),
and, from `GET /recovery`, how many times it was opened and when last — "your own redeems count too". When a code has
been opened more times than the person expects, Settings offers "Turn off and reset library key". A live entry
posted again by a reconcile starts its count from zero; Settings says so.

**Notice of change.** Each device remembers the locator of the live entry it last saw. When it reads that the live
entry changed or is gone, and it did not make that change, it shows once: "A new recovery code was made on
*<device>*. The code you had no longer works." or "Your recovery code was turned off on another device."

## 7. In the library: `set:recovery`

A settings row (v2 §3), sealed and stamped like every other. Its settings are an **open group**, one per locator (hex),
each `{"string": "<JSON>"}`, or `null` once the entry is ended:

```json
{"state": "live", "library": "<library id, 32 hex>", "sealed": "<base64url>", "createdAt": 1790000000000,
 "by": "<stamp device id>"}
```

`state` is `pending` or `live`. A setting merges by the later stamp (v2 §5); a writer never turns a `null` back into an
entry.

- **Own entries only.** An entry whose `library` is not the id of the library the row was read from (copied by a
  build that predates this spec) is not acted on as a code; a reconcile nulls it.
- **One live entry.** The compare-and-set of §6 step 5 keeps it to one. A merge (a write-back after a generation
  change) can bring two together; a reconcile keeps the one den-edge lists, then the greater `createdAt`, then the
  byte-greater locator, and nulls the other.
- **Abandoned.** A `pending` entry is abandoned when its `createdAt` is more than an hour before the reader's clock,
  or it was made by this device in an earlier launch. A reconcile nulls and `DELETE`s it. The making device, on its
  next launch, tells the person: "Your recovery code setup didn't finish. The code you saw doesn't work; make a new
  one."
- `set:recovery` holds no code. `sealed` and the locator are opaque without it, and the row is sealed under the
  library key, so keeping them there exposes nothing den-edge does not already hold.

**Reconcile.** A device holding the library makes den-edge match the row:

1. `GET /recovery`, then read the log to its head. (In this order: an entry is pending in the row before it is
   posted, so every listed entry was already named.) A reconcile that did not read the log to its head in this pass —
   the read failed, den-edge was busy, the library was missing, or the read stopped at a generation change — **stops
   here**, with no `DELETE`, no row write and no `POST`. Acting on a row read earlier could delete the person's
   current code and post a replaced one again; `GET /recovery` sits on a lane that may answer while the log read is
   refused, so this is the expected failure under load, not a corner case. A reconcile at launch counts as the day's
   only once it got past this step.
2. `DELETE` every listed entry that the row does not name as `live` or as a `pending` entry that is not abandoned.
3. Null abandoned pending entries and entries of another library (by compare-and-set).
4. If the live entry is not listed, `POST` its `{locator, sealed}` again, unchanged: the person's code keeps working.
   If that fails, Settings shows "Your recovery code no longer works: make a new one."

A device reconciles at launch (at most once a day), whenever Settings › Recovery code opens, and after each
generation change (library v2 §2) once its write-back is done — so a row written back from a device that saw a newer
code wins before den-edge is made to match it. A device whose library is read-only (v4 §10 step 3) does steps 1, 2
and 4 and skips 3.

## 8. Redeeming

On a new device: Den Web's link screen offers "Open with a recovery code" and accepts a pasted code; the TV's
first-run screen and Settings › Library offer the same.

1. Read the code (§2). A typo is reported at once, with no request.
2. Derive (§3), then `POST /recovery/open`. `404 unknown_code`: "This code doesn't open a library. Check it for a
   typo; it may also have been replaced or turned off." `429`: wait as told.
3. Open `sealed` with `wrapKey`. A blob that fails to open is reported as den-edge's error, not as a wrong code
   (the code passed its check and found a locator).
4. Derive the library id and token (v2 §1) and read the library (`GET /lib/{id}/changes`). `410 library_moved`:
   "This code is out of date: the library's key was reset after it was made." A `426` on a build below the library's
   minimum: "Library update required", keeping the key so updating is enough.
5. **Adopt the key**:
   - A device holding no den-edge library keeps it as its library key: a TV in its Keychain as the first TV does, a
     browser as it keeps a link's key, with no link. It then joins `set:devices` as usual (v2 §3).
   - A device already holding this library: nothing to do.
   - A device holding another den-edge library asks first, then moves into the recovered one as an inbox `libraryKey`
     message moves a TV (inbox v1 §2; library v4 §12): its rows are merged in. **Known limit**: a TV holding a v4
     library cannot move yet (oxyc/den#192), and every TV that could have made a code holds one, so until that lands
     a TV redeems only where it holds no den-edge library; it says why otherwise. Den Web is not affected.
6. Zero the code, `A` and `wrapKey`. Show once: "Your recovery code still works. If anyone else may have seen it,
   make a new one in Settings."

A recovered browser has no TV link; it pairs with a TV later as any browser does. A recovered TV is a TV holding the
library; other devices pair with it.

## 9. Key reset, linking, moving

- **Key reset** (v2 §1, v4 §12). The code wraps the old key, and the reset's `DELETE /lib/{id}` deletes the old
  library's entries with it (§5 *Cascade*), so the old code stops opening anything. The resetting device MUST NOT copy
  `set:recovery` entries into the new library. It then **offers** "Make a new recovery code"; it does not force one.
  Its confirmation says first that the current code will stop working.
- **Moving into another library** (an inbox `libraryKey` message, linking a device's own library, step 5 of §8): no
  `set:recovery` entry is copied into the destination; the source library's code stays the source's, and goes with it
  if the source is deleted.
- **Pairing** changes no key and no code.
- **Known limit**: a den-edge store restored to a backup taken before a code was ended, or before the current one was
  made, holds the wrong entries until a device holding the library reconciles after the generation change (§7). With
  no such device left, the restored entries stand.

## 10. Library v4, and v3 before the switch

The recovery entry is outside the library's log: `/recovery` routes carry no wire version, and the blob holds only
the key. The one thing inside the library is `set:recovery`, a settings row, which every format treats the same way:

- **v4** (library v4 §3, §4): a settings row, written as uncompressed JSON, merged per setting. `v4_form` stages it
  with `k` and `v` unchanged (v4 §10 *Settings*). A key reset re-seals settings rows (v4 §12), with the exception in §9
  above.
- **Which builds**: recovery ships in v4 builds only. A v4 build that opens a v3 library converts it (v4 §1), so it
  meets a v3 library only while a failed switch keeps it read-only (v4 §10 step 3). Then:
  - **making** a code waits for the conversion, since it writes `set:recovery`: Settings shows "Available after the
    library update"; a reconcile skips its row writes (§7);
  - **redeeming** works: it needs only `/recovery` and a read of the library. The redeeming device is then the first
    v4 build to open that library and converts it (v4 §10), as any v4 build would; on a failure it stays read-only and
    retries as v4 says.
- A v3 build never makes or redeems a code. One that copies `set:recovery` on a key reset copies entries naming the
  old library, which §7 nulls; den-edge already deleted their entries with the old library.
- A code made on v4 opens the library under any later format: it carries the key, not rows.

## 11. Vectors

[`../vectors/recovery-v1.json`](../vectors/recovery-v1.json), generated by
`node tools/recovery-vectors.mjs > vectors/recovery-v1.json` (Node 24.7 or later; the script first checks Node's
Argon2id against RFC 9106 §5.3), pins:

- making a code from 22 fixed bytes, its check characters and its display form;
- reading typed codes: lowercase with spaces accepted; a data character wrong, and a check character wrong, refused as
  `checksum`; too short, an extra group, a `0`, and a lowercase `o` or `i` (which uppercase to letters outside the
  alphabet) refused as `mistyped`; no-break spaces between groups accepted; a `ſ` in place of an `S` refused as
  `mistyped`;
- `A`, `locator` and `wrapKey` for two codes, and their sealed blobs for fixed nonces and the library key of
  `pairing-v1.json` (whose library id is `library-v2.json`'s);
- a blob sealed with another locator in its additional data, which MUST fail to open.

den-edge's tests MUST cover: `open` of an unknown locator `404`, and needing no credential; the per-visitor limits
behind a trusted proxy (two visitors behind one proxy are two buckets) and their `Retry-After`; `POST`, `GET` and
`DELETE /recovery` refused without a member proof, and a library's proof neither listing nor deleting another
library's entry; the fifth entry of a library `409 recovery_full`; `DELETE` idempotent; `GET` counting opens;
`DELETE /lib/{id}` deleting that library's entries; nothing about a locator, `sealed` or a member proof in its log
lines; an entry surviving a restart and a store backup and restore; the limiter's table filled from many addresses
leaving a new visitor's `open` and every other route working; and a `POST` that raced a `DELETE /lib/{id}` writing
no entry.

Clients' tests MUST cover §6 and §7: a pending entry written before the `POST`; a make abandoned after the `POST`
(crash) nulled and deleted by the next reconcile; a losing concurrent make; a live entry missing at den-edge posted
again; an unnamed listed entry deleted; a reconcile whose log read fails (a `503`) or stops at a generation change
deleting, writing and posting nothing; the reconcile triggers, with a failed launch reconcile not counting as the
day's.

## 12. Work per repo

- **den-spec**: this file and its vectors.
- **den-core**: three ops in den-sync, so both clients share them: `recovery_code` (22 bytes → code), `recovery_read`
  (typed text → data or `mistyped`/`checksum`) and `recovery_derive` (data → `locator`, `wrapKey`). This adds
  RustCrypto `argon2`, `hkdf` and `sha2`, pinned in `Cargo.lock`; the ops stay pure (randomness arrives as input), and
  sealing stays in the clients, as for rows. Load `recovery-v1.json` in the tests. **Measure** Argon2id at §3's
  parameters on the slowest supported Apple TV and in Safari, Chrome and Firefox on a phone before the parameters are
  pinned.
- **den-edge**: the four `/recovery` routes (§5), the table in the durable store with each entry's library, the
  per-library cap, the cascade on `DELETE /lib/{id}`, per-visitor limits through `client_ip`, log redaction, CORS;
  Den Web as below.
- **Den Web** (the main path): Settings › Recovery code (make, show once with Copy and its clipboard warning, confirm
  the last group, status, replace, turn off, the change notice); "Open with a recovery code" on the link screen,
  accepting a pasted code; a library key held without a TV link; derivation in a Worker; the reconcile and its
  triggers (§7); the key-reset and move rules of §9.
- **TV**: Settings › Library › Recovery code, built from `SettingsScreen`, `PrimaryActionButton`,
  `DestructiveActionButton` with `.confirmDelete`, and a code screen whose focusable element is the last-group
  confirmation (`TextInputRow`); "Open with a recovery code" on the first-run screen and in Settings via
  `TextInputRow`; the Keychain write; the reconcile and its triggers (§7); the key-reset and move rules of §9;
  `DenLog` never sees the code, `A` or `wrapKey`.

## 13. Decisions

1. **24 characters** (110 bits). Den Web is the main path, where pasting and typing are easy; the TV remote's cost is
   secondary. The web known limit stays prominent (intro, §1).
2. **Any device holding the library** may make a code (§6).
3. **The open count is shown**, from den-edge, as advisory (§5, §6).
4. **The person types the last group** to confirm the code was saved; the code goes live only after that (§6).
5. **v4 builds only** (§10).
6. **A key reset offers a new code**; it does not force one (§9).
7. **den-edge knows which library an entry belongs to.** Hiding it bought nothing with one or two libraries per
   store, and recording it lets den-edge cap entries per library, delete them with their library and authorize every
   change with the member proof (§5). Redeeming still names no library.
