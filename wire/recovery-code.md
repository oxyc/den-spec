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
| **den-edge reads what it stores** (its disk, its backups, its logs, its operator) | It holds a locator, a sealed blob and a hash (§4). Opening the blob takes the code. Testing a guess takes one Argon2id at 64 MiB (§3); the code has 110 random bits (§2), so an offline search is out of reach. den-edge does not store which library an entry belongs to. |
| **den-edge swaps or forges an entry** | The blob is AES-GCM under a key only the code derives, with the locator in the additional data. A forged blob fails to open; a blob moved to another locator fails to open. |
| **den-edge withholds, deletes or rolls back an entry** | Not prevented: den-edge is trusted for availability, as it is for the library itself. A rollback can bring back a code that was turned off (§9 *Known limits*); a key reset (§9) still defeats it. |
| **A stolen code** | The code is the library key. The holder of a stolen code can do what any linked device can. Detection: den-edge counts opens (§5), which Settings shows. Response: turn the code off **and reset the library key** (§6, §9) — turning it off alone stops future opens, not a key already taken. The code is never stored in the library, never logged and shown once (§6). |
| **Online guessing at den-edge** | One guess is one Argon2id on the guesser's side and one request; a request reveals only whether a locator exists, and finding one that exists means knowing the code. Rate limits (§5) keep den-edge from being a cheap existence oracle or load target; they are not what makes guessing fail — the 110 bits are. |
| **A lost code** | While any device holds the library: make a new one (§6), which turns the old one off. With every device and the code lost, the library is gone; den-edge cannot help, by design. |
| **An active den-edge serving Den Web** | **Not covered, and Den Web is the main path.** Den Web's code is served by den-edge, so a den-edge that serves a modified page can read a code typed or pasted into it, and anything else a browser holds, the library key included — today, with or without recovery codes. Making and redeeming on the TV avoids it. |
| **A device that was shut out by a key reset** | It may hold an old code's `manage` (§7), which only acts on entries of the old key. A new code is written to the new library only. |

## 2. The code

- **24 characters** from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (pairing v1's alphabet: no 0/O or 1/I): **22 data
  characters**, each chosen uniformly (110 bits), then **2 check characters**. Shown as six groups of four:
  `GEB2-LP9U-C63W-Q95U-NSLT-XMFL`.
- **Making one**: take 22 random bytes from the platform's CSPRNG; data character *i* is `ALPHABET[byte_i & 31]`
  (uniform, since 32 divides 256).
- **Check characters**: `h = SHA-256(UTF-8("den/recovery/v1/check") ‖ UTF-8(data))`; the first is
  `ALPHABET[h[0] >> 3]`, the second `ALPHABET[((h[0] & 7) << 2) | (h[1] >> 6)]` — the first 10 bits of `h`. A
  typo slips past them once in 1,024.
- **Reading a typed code**: uppercase it and drop spaces and dashes. Anything that is then not 24 characters of the
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
  total work. It is margin on top of the code's entropy: it is what keeps a partial disclosure (a photo with two
  groups hidden) out of reach.
- **Cost**: ~110 ms natively on an Apple-silicon Mac (measured with Node's Argon2id); expect well under a second on
  an Apple TV and one to three seconds in a browser's WebAssembly. Both run it once per make or redeem. A client
  runs it off the main thread and shows progress.
- **A fixed salt** is deliberate: the device has nothing to look up before it derives the locator, and a per-user
  salt only defends against precomputation, which 110 random bits already rule out. Deriving the locator any faster
  than the wrap key would let den-edge test guesses against the locator at that speed, so both come from `A`.
- **One implementation**: den-core (§12), so the TV and Den Web cannot disagree on a parameter.

## 4. The entry

What a device stores at den-edge for a code:

- `locator`: 16 bytes, lowercase hex in requests. Unrelated to the library id; den-edge cannot compute one from the
  other.
- `sealed`: unpadded base64url of `nonce (12) ‖ ciphertext ‖ tag (16)`, AES-256-GCM under `wrapKey` with a fresh
  random nonce and the additional data `UTF-8("den/recovery/v1") ‖ locator` (16 raw bytes). The plaintext is UTF-8
  JSON:

  ```json
  {"v": 1, "libraryKey": "<base64url, 32 bytes>", "createdAt": 1790000000000}
  ```

  A reader MUST refuse a plaintext without a 32-byte `libraryKey` and ignore fields it does not know.
- `manageHash`: SHA-256 of `manage`, 32 random bytes the making device generates, **not** derived from the code.
  `manage` is what turns the entry off and reads its counter (§5); it is kept in the library (§7), so any device
  holding the library can do both without the code.

## 5. den-edge

| Route | Body / headers | Answer |
|---|---|---|
| `POST /recovery` | `{"locator", "sealed", "manageHash"}`; `x-den-library-member: <id>:<member>` | `201 {"createdAt"}`; `403 forbidden` without a valid member proof; `409 locator_taken`; `409 recovery_full` |
| `POST /recovery/open` | `{"locator"}` | `200 {"sealed"}`; `404 {"error": "unknown_code"}` |
| `POST /recovery/status` | `{"locator"}`; `x-den-recovery-manage: <hex manage>` | `200 {"createdAt", "opens", "lastOpenedAt"}`; `404 unknown_code`; `403 forbidden` |
| `DELETE /recovery` | `{"locator"}`; `x-den-recovery-manage: <hex manage>` | `200 {"deleted": true \| false}` (idempotent; `false` when there was none); `403 forbidden` on a wrong `manage` |

- **Storage**: one small table in den-edge's durable store, beside the libraries, so a store backup carries it. Per
  entry: `locator`, `sealed` (at most 512 characters), `manageHash`, `createdAt`, `opens`, `lastOpenedAt`. **No
  library id, no member, no address.** Entries never expire. At most **32** entries per store (a household holds one
  or two); past that, `409 recovery_full`.
- **Making** needs a member proof of **some** library on the store (as `NEW_LIBRARIES=members` does for a first
  batch), so a stranger cannot fill the table. den-edge checks it and does not record which library it named.
  (**Known limit**: at that request den-edge sees both the member's library id and the locator; with one or two
  libraries per store, linking them is trivial anyway. What the rule buys is that den-edge's store and backups do
  not record the link, and that redeeming names no library.)
- **Opening** increments `opens` and sets `lastOpenedAt`. These are advisory: den-edge is untrusted and can lie.
- **Secrets stay out of URLs and logs.** Locators and `manage` travel in bodies and headers, never in a path or
  query. den-edge MUST NOT log a locator, `sealed`, `manage` or `manageHash`; its request line logs the route and the
  status only.
- **Limits**, per client address bucket (an IPv4 address, an IPv6 /64): `open` 5 per 10 minutes and 20 per day;
  `POST /recovery` 10 per day; `status` and `DELETE` 60 per hour. Past one: `429 rate_limited` with `Retry-After`.
  No store-wide limit on `open`: one would let anyone lock the owner out of recovery, and it buys no security.
- Lookups are by the exact locator; `manage` compares in constant time against `manageHash`.
- `/recovery` routes carry no `x-den-wire` and are not fenced by a library rewrite: they are not part of any library.

## 6. Making, showing, replacing, turning off

**Who**: any device that holds the library — a TV, or a linked browser. A holder can already copy the key; a code
gives it no more. Not offered for a device's own library (library v2 §1), which den-edge does not hold.

**Making**:

1. Make the code (§2) and `manage`; derive (§3); seal the plaintext.
2. `POST /recovery`. On `409 locator_taken` (never expected: 110 bits) start again from 1. The entry is not yet in the
   library, so nothing points at it and the current code, if any, still works.
3. **Show the code**, once, and **confirm it was saved**: the person types the code's last group (four characters),
   compared on the device and never sent. A mismatch keeps the screen. Leaving without confirming `DELETE`s the entry
   from step 2 and changes nothing else.
4. Write the entry into `set:recovery` (§7) by compare-and-set, as `live`, and mark every other live entry `retired`
   in the same write. If the compare-and-set conflicts and the fresh read shows a live entry made after the read this
   attempt started from, another device has just made a code: `DELETE` this one and tell the person to discard the
   code they wrote down. Any other failure that ends the attempt does the same. Otherwise retry the write.
5. `DELETE` each entry step 4 retired (§7 *Retiring*).

The screen says what the code is and is not: "Anyone with this code can open your library. Write it down or keep it
in a password manager. Den can't show it again." Den Web also offers **Copy**. The code MUST NOT be written to the
library, to storage, to a log (the TV's remote log included), to analytics or to a URL; a client holds it in memory
only until the screen closes or derivation is done.

**Replacing** is making: the new code retires the old one. **Turning off** marks the live entry `retired` and
`DELETE`s it, behind a confirmation.

**Status**: Settings shows the live entry's `createdAt` and the device that made it (`by`, joined to `set:devices`),
and, from `POST /recovery/status`, how many times it was opened and when last. When a code has been opened more times
than the person expects, Settings offers "Turn off and reset library key".

## 7. In the library: `set:recovery`

A settings row (v2 §3), sealed and stamped like every other. Its settings are an **open group**, one per locator (hex),
each `{"string": "<JSON>"}`:

```json
{"library": "<library id, 32 hex>", "manage": "<hex, 32 bytes>", "createdAt": 1790000000000,
 "by": "<stamp device id>", "retired": null}
```

`retired` is null while live and the Unix ms it was retired otherwise. A setting merges by the later stamp (v2 §5);
a setting is never written back to live once retired.

- **Live** = `retired` is null **and** `library` is the id of the library the row was read from. An entry for another
  library (copied by a build that predates this spec, §8) is not live and is neither shown nor acted on.
- **At most one live entry.** A device that reads more than one retires all but the one with the latest stamp.
- **Retiring** writes `retired`, then `DELETE /recovery` with its `manage`. A device repeats the `DELETE` for every
  retired entry of this library after each generation change (library v2 §2), since a store restore can bring an
  entry back; `deleted: false` is the normal answer.
- Retired entries stay in the row (about 200 bytes each), so a later device can still delete them. A device may drop
  a retired entry (write the setting `null`) once a `DELETE` of it answered `deleted: false` after the last
  generation change it saw.
- `set:recovery` holds no code and nothing derived from one but the locator, which opens nothing.

## 8. Redeeming

On a new device: Den Web's link screen offers "Open with a recovery code" and accepts a pasted code; the TV's
first-run screen and Settings › Library offer the same.

1. Read the code (§2). A typo is reported at once, with no request.
2. Derive (§3), then `POST /recovery/open`. `404 unknown_code`: "This code doesn't open a library. It may have been
   replaced or turned off." `429`: wait as told.
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
     message moves a TV (inbox v1 §2; library v4 §12): its rows are merged in.
6. Zero the code, `A` and `wrapKey`. Show once: "Your recovery code still works. If anyone else may have seen it,
   make a new one in Settings."

A recovered browser has no TV link; it pairs with a TV later as any browser does. A recovered TV is a TV holding the
library; other devices pair with it.

## 9. Key reset, linking, moving

- **Key reset** (v2 §1, v4 §12). The code wraps the old key, so after the reset it opens a key whose library answers
  `410`. The resetting device MUST NOT copy `set:recovery` into the new library. After its `DELETE /lib/{id}` of the
  old library succeeds, it retires and `DELETE`s every entry it read in the old library's `set:recovery` (it holds
  their `manage`), and then offers "Make a new recovery code". Its confirmation says first that the current code will
  stop working.
- **Moving into another library** (an inbox `libraryKey` message, linking a device's own library, step 5 of §8): no
  `set:recovery` entry is copied into the destination; the source library's code stays the source's.
- **Pairing** changes no key and no code.
- **Known limits**:
  - A build that predates this spec copies `set:recovery` like any settings row when it resets or moves. The copied
    entries name another library, so §7 ignores them; their den-edge entries stay, opening a key that answers `410`,
    and count toward the 32.
  - A den-edge store restored to a backup taken before a code was turned off serves that code again until a device
    holding the library sees the generation change and repeats its `DELETE`s (§7). A restore that also brings back a
    library deleted by a key reset is a wider problem of restores, not of codes.

## 10. Library v4, and v3 before the switch

The recovery entry is outside the library's log: `/recovery` routes carry no wire version, and the blob holds only
the key. The one thing inside the library is `set:recovery`, a settings row, which every format treats the same way:

- **v4** (library v4 §3, §4): a settings row, written as uncompressed JSON, merged per setting. `v4_form` stages it
  with `k` and `v` unchanged (v4 §10 *Settings*). A key reset re-seals settings rows (v4 §12), with the exception in §9
  above.
- **Which builds**: recovery ships in v4 builds only. A v4 build that opens a v3 library converts it (v4 §1), so it
  meets a v3 library only while a failed switch keeps it read-only (v4 §10 step 3). Then:
  - **making** a code waits for the conversion, since it writes `set:recovery`: Settings shows "Available after the
    library update";
  - **redeeming** works: it needs only `/recovery` and a read of the library. The redeeming device is then the first
    v4 build to open that library and converts it (v4 §10), as any v4 build would; on a failure it stays read-only and
    retries as v4 says.
- A v3 build never makes or redeems a code. One that copies `set:recovery` on a key reset is covered by §9's first
  known limit.
- A code made on v4 opens the library under any later format: it carries the key, not rows.

## 11. Vectors

[`../vectors/recovery-v1.json`](../vectors/recovery-v1.json), generated by
`node tools/recovery-vectors.mjs > vectors/recovery-v1.json` (Node 24.7 or later; the script first checks Node's
Argon2id against RFC 9106 §5.3), pins:

- making a code from 22 fixed bytes, its check characters and its display form;
- reading typed codes: lowercase with spaces accepted; a data character wrong refused as `checksum`; too short, an
  extra group and a `0` refused as `mistyped`;
- `A`, `locator` and `wrapKey` for two codes, and their sealed blobs for fixed nonces and the library key of
  `pairing-v1.json` (whose library id is `library-v2.json`'s);
- a blob sealed with another locator in its additional data, which MUST fail to open;
- `manage` and `manageHash`.

den-edge's tests MUST cover: `open` of an unknown locator `404`; the per-address limits and their `Retry-After`;
`POST /recovery` without a member proof `403` and past 32 entries `409 recovery_full`; `DELETE` idempotent and
`403` on a wrong `manage`; `status` counting opens; nothing about a locator, `sealed` or `manage` in its log lines;
and an entry surviving a restart and a store backup and restore.

## 12. Work per repo

- **den-spec**: this file and its vectors.
- **den-core**: three ops in den-sync, so both clients share them: `recovery_code` (22 bytes → code), `recovery_read`
  (typed text → data or `mistyped`/`checksum`) and `recovery_derive` (data → `locator`, `wrapKey`). This adds
  RustCrypto `argon2`, `hkdf` and `sha2`, pinned in `Cargo.lock`; the ops stay pure (randomness arrives as input), and
  sealing stays in the clients, as for rows. Load `recovery-v1.json` in the tests; measure the WebAssembly build's
  Argon2id in Safari, Chrome and Firefox on a phone.
- **den-edge**: the four `/recovery` routes (§5), the table in the durable store, the limits, the 32-entry cap, log
  redaction, CORS; Den Web as below.
- **Den Web** (the main path): Settings › Recovery code (make, show once with Copy, confirm the last group, status,
  replace, turn off); "Open with a recovery code" on the link screen, accepting a pasted code; a library key held without a TV link; derivation in a Worker; the key-reset and move
  rules of §9; `set:recovery` handling (§7).
- **TV**: Settings › Library › Recovery code, built from `SettingsScreen`, `PrimaryActionButton`,
  `DestructiveActionButton` with `.confirmDelete`, and a code screen whose focusable element is the last-group confirmation (`TextInputRow`); "Open with a
  recovery code" on the first-run screen and in Settings via `TextInputRow`; the Keychain write; the key-reset and move
  rules of §9; `DenLog` never sees the code, `A`, `wrapKey` or `manage`.

## 13. Decisions

1. **24 characters** (110 bits). Den Web is the main path, where pasting and typing are easy; the TV remote's cost is
   secondary. The web known limit stays prominent (intro, §1).
2. **Any device holding the library** may make a code (§6).
3. **The open count is shown**, from den-edge, as advisory (§5, §6).
4. **The person types the last group** to confirm the code was saved; the code goes live only after that (§6).
5. **v4 builds only** (§10).
6. **A key reset offers a new code**; it does not force one (§9).
