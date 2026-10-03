# den-spec

The wire formats Den's clients share, with the test vectors that keep them in step. The Apple TV app, Den Web
([den-edge](https://github.com/oxyc/den-edge)'s web app) and den-edge each load the vectors in their own
tests, so a client that drifts from the spec fails its build rather than corrupting a library.

- [`wire/library-v2.md`](wire/library-v2.md): the library in den-edge's record log. Keys, row encryption,
  records, clock stamps, merge rules, versioning.
- [`vectors/library-v2.json`](vectors/library-v2.json): key derivations and sealed rows for a fixed key.
  Generated: `swift tools/vectors.swift > vectors/library-v2.json` (macOS, CryptoKit).
- [`vectors/merge-v2.json`](vectors/merge-v2.json): merge and clock cases, written by hand.
- [`wire/pairing-v1.md`](wire/pairing-v1.md): how a device joins a library — CPace over ristretto255 on a
  short code, relayed by den-edge, which learns no secret.
- [`vectors/pairing-v1.json`](vectors/pairing-v1.json): the CPace draft's ristretto255 vectors and a whole
  pairing for fixed inputs. Generated: `npm ci --prefix tools && node tools/pairing-vectors.mjs >
  vectors/pairing-v1.json`.
- [`wire/inbox-v1.md`](wire/inbox-v1.md): a paired device's messages to its TV, sealed under its link key.
- [`vectors/inbox-v1.json`](vectors/inbox-v1.json): sealed messages for pairing's fixed link key. Generated:
  `node tools/inbox-vectors.mjs > vectors/inbox-v1.json`.
- [`vectors/library-v4-moves.json`](vectors/library-v4-moves.json): moving a library to another key
  (library-v4 §12) — rows sealed under one fixed key, and what the other holds after the move. Generated:
  `node tools/move-vectors.mjs > vectors/library-v4-moves.json`.
- [`wire/recovery-code.md`](wire/recovery-code.md) (proposal): a recovery code that unwraps the library key on a
  new device, with no device that holds the library at hand. Argon2id-wrapped, stored at den-edge under a locator
  it cannot tie to a library.
- [`vectors/recovery-v1.json`](vectors/recovery-v1.json): codes, Argon2id and sealed entries for fixed inputs.
  Generated: `node tools/recovery-vectors.mjs > vectors/recovery-v1.json` (Node 24.7 or later).
- [`wire/routes-v1.md`](wire/routes-v1.md): den-edge's table of every address for each service, and the rule
  every client applies to it — order, health check, and the home check before a plain LAN address.
- [`wire/store-v3.md`](wire/store-v3.md): the current single mmap'd artifact den-atlas serves from — every
  per-title signal and both vector matrices in one columnar file. Written by den-dataset, read by
  den-core's `den-store`. v3 adds structural-affinity profiles; [`wire/store-v2.md`](wire/store-v2.md)
  made `franchise` a list and [`wire/store-v1.md`](wire/store-v1.md) is the layout before that.
- [`vectors/store-v3.json`](vectors/store-v3.json) and `vectors/store-v3.store`: a three-title store and
  the values a correct reader gets out of it, covering a facts-only row, a declined facet, a genre Q-id
  that maps to two TMDB ids, a title in two raw series, a curated franchise spanning film and TV,
  premise tags shared across two titles, other versions of both kinds, Jev More Like This scores
  that are not symmetric, and fan picks for a title asked with picks, one asked with none and one never
  asked.
  Generated: `python3 tools/store-fixture.py
  --build-store ../den-dataset/pipeline/build_store.py` — by the real writer, so it cannot agree with a
  reimplementation instead of with the format.
- [`vectors/store-v1.json`](vectors/store-v1.json) and `vectors/store-v1.store`: the same titles as the
  last store-v1 writer wrote them. Frozen, not regenerated: a reader that still accepts v1 tests against it.
- [`tools/README.md`](tools/README.md): reusable operational HTTP checks for the seven Den services, with
  explicit profiles for their credentials and routing differences.

A change to a format is a new version file, never an edit to a released one.

## License

MIT
