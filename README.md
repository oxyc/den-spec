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
- [`wire/routes-v1.md`](wire/routes-v1.md): den-edge's table of every address for each service, and the rule
  every client applies to it — order, health check, and the home check before a plain LAN address.
- [`wire/store-v2.md`](wire/store-v2.md): the single mmap'd artifact den-atlas serves from — every
  per-title signal and both vector matrices in one columnar file. Written by den-dataset, read by
  den-core's `den-store`. v2 made `franchise` a list; [`wire/store-v1.md`](wire/store-v1.md) is the
  layout before that.
- [`vectors/store-v2.json`](vectors/store-v2.json) and `vectors/store-v2.store`: a three-title store and
  the values a correct reader gets out of it, covering a facts-only row, a declined facet, a genre Q-id
  that maps to two TMDB ids, and a title in two series. Generated: `python3 tools/store-fixture.py
  --build-store ../den-dataset/scripts/v2/build_store.py` — by the real writer, so it cannot agree with a
  reimplementation instead of with the format.
- [`vectors/store-v1.json`](vectors/store-v1.json) and `vectors/store-v1.store`: the same titles as the
  last store-v1 writer wrote them. Frozen, not regenerated: a reader that still accepts v1 tests against it.
- [`tools/README.md`](tools/README.md): reusable operational HTTP checks for the seven Den services, with
  explicit profiles for their credentials and routing differences.

A change to a format is a new version file, never an edit to a released one.

## License

MIT
