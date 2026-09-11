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

A change to a format is a new version file, never an edit to a released one.

## License

MIT
