// Writes vectors/inbox-v1.json: sealed inbox messages (wire/inbox-v1.md) under the link key of
// vectors/pairing-v1.json, with fixed nonces. Run from the repository root:
//
//   node tools/inbox-vectors.mjs > vectors/inbox-v1.json

import { createCipheriv, hkdfSync } from 'node:crypto'

const range = (from, to) => Buffer.from(Array.from({ length: to - from }, (_, i) => from + i))
const linkKey = range(32, 64)
const enc = Buffer.from(hkdfSync('sha256', linkKey, Buffer.from('den/link/v1'), Buffer.from('enc'), 32))
const aad = Buffer.from('den/inbox/v1')

const plaintexts = [
  {
    id: '00112233445566778899aabbccddeeff',
    sentAt: 1789000000000,
    message: { type: 'watchlist', tmdbId: 550, mediaType: 'movie', title: 'Fight Club', year: 1999 },
  },
  {
    id: 'ffeeddccbbaa99887766554433221100',
    sentAt: 1789000060000,
    message: { type: 'play', tmdbId: 1399, mediaType: 'tv', title: 'Game of Thrones', season: 1, episode: 2 },
  },
]

const cases = plaintexts.map((body, i) => {
  const plaintext = JSON.stringify(body)
  const nonce = range(i * 12, i * 12 + 12)
  const cipher = createCipheriv('aes-256-gcm', enc, nonce)
  cipher.setAAD(aad)
  const sealed = Buffer.concat([nonce, cipher.update(plaintext, 'utf8'), cipher.final(), cipher.getAuthTag()])
  return { nonce: nonce.toString('hex'), plaintext, sealed: sealed.toString('base64url') }
})

const vectors = {
  comment:
    'wire/inbox-v1.md, computed by tools/inbox-vectors.mjs. linkKey is pairing-v1.json\'s; enc is its HKDF ' +
    '"enc" derivation. Byte strings are lowercase hex; sealed is base64url as it travels.',
  linkKey: linkKey.toString('hex'),
  enc: enc.toString('hex'),
  cases,
}

process.stdout.write(`${JSON.stringify(vectors, null, 2)}\n`)
