# Inbox, v1

How a paired device ([pairing-v1.md](pairing-v1.md)) sends messages to its TV through den-edge's `/inbox`
without den-edge reading them, forging them or replaying them unnoticed.
[`../vectors/inbox-v1.json`](../vectors/inbox-v1.json) pins the sealing; each client's tests load it.

Words: *MUST* is a rule a client breaks at the cost of the other client or of the user's secrets; *should* is
advice.

## 1. Sending

The device appends with its link's credential and a sealed message:

```
POST /inbox/append
x-den-link: <inbox>
{"sealed": "<base64url>"}
```

- `inbox` and `enc` are the link key's derivations (pairing-v1 §6).
- `sealed` = unpadded base64url of `nonce (12) ‖ ciphertext ‖ tag (16)`: AES-256-GCM with `enc`, a fresh
  random nonce and the additional data `den/inbox/v1`.
- The plaintext is a UTF-8 JSON object: a random `id` (16 bytes, lowercase hex), `sentAt` (Unix ms) and the
  `message`:

```json
{"id": "00112233445566778899aabbccddeeff", "sentAt": 1789000000000,
 "message": {"type": "watchlist", "tmdbId": 550, "mediaType": "movie", "title": "Fight Club", "year": 1999}}
```

## 2. Messages

| `type` | Fields | The TV |
|---|---|---|
| `watchlist` | `tmdbId`, `mediaType` (`movie`/`tv`), `title`; `posterPath`, `year` optional | adds it |
| `play` | `tmdbId`, `mediaType`, `title`; `season`, `episode` optional | plays it, if `sentAt` is within two minutes |
| `addon` | `manifestUrl`: https, or http to a LAN host | offers it for approval on the TV |
| `libraryKey` | `key`: the new library key, base64 of 32 bytes | moves to it, as when joining a library (library-v2 §1): its rows go up to the new key's log, and the link the message came from stays paired |
| `tmdbKey` | `key` | sets the TMDB key |
| `apiKey` | `service` (`omdb`, `doesthedogdie`), `key` | sets that key |
| `device` | `name`: a label as pairing-v1 §3 cleans it; `deviceId` optional | names the link and records the sender's stable identity |

`deviceId`, when present, is the sender's 16-character lowercase-hex stamp device id (library-v2 §4): the same
`d` it puts in stamps and uses for its entries in `set:devices`. A sender that supports the field MUST include it.
It sends `device` after opening a new handover, once on every existing link when upgrading from a version that did
not send it, and again when its name or stamp device id changes. This lets the host persist the id on the exact
link whose `enc` key opened the message, then join that link to the sender's entry in `set:devices`; if the entry
has not arrived yet, the host keeps the association and resolves it later. Labels are never used as identity.

A receiver that supports the field MUST drop a `device` message whose present `deviceId` is not exactly 16
lowercase hex characters. An omitted `deviceId` from an older sender still updates the label but MUST NOT erase
an id already associated with the link. Existing receivers ignore the new field. `deviceId` remains inside the
sealed plaintext and is not a credential or a key input, so den-edge learns no new identifier and the association
grants no authority. den-edge can't tell sealed messages apart, so it can't keep just the latest one, and a queue
holds fifty messages.

## 3. Receiving

The TV drains its links' queues as before and, for a paired link:

- MUST drop any message that isn't sealed. Anything den-edge could write itself counts for nothing.
- MUST drop a message that doesn't open under the link's `enc`, or whose plaintext isn't as above.
- MUST drop a message whose `sentAt` is more than 7 days old or more than a day ahead of its clock, and one
  whose `id` it has already applied. It remembers ids for 7 days, the queue's lifetime on den-edge, so a
  message replayed from an old queue is refused either way.
- Unknown `type`s and fields are ignored, so a newer device doesn't break an older TV.

## 4. den-edge

Stores a sealed message as it came — at most 4096 characters of base64url — in the link's queue, newest fifty
for 7 days, and hands the queue to the next drain. It learns when a device sends and how much, not what.
