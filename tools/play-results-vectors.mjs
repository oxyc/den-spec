// Writes vectors/play-results-v1.json: a request id's result address and key, and one sealed result of each state
// (wire/play-results-v1.md), with fixed nonces. Run from the repository root:
//
//   node tools/play-results-vectors.mjs > vectors/play-results-v1.json

import { createCipheriv, hkdfSync } from 'node:crypto'

const range = (from, to) => Buffer.from(Array.from({ length: to - from }, (_, i) => from + i))
// inbox-v1.json's play envelope id: the same 16 bytes would name an assistant message's id.
const id = 'ffeeddccbbaa99887766554433221100'
const salt = Buffer.from('den/play-result/v1')
const derive = (info, length) => Buffer.from(hkdfSync('sha256', Buffer.from(id, 'hex'), salt, Buffer.from(info), length))
const address = derive('address', 16).toString('hex')
const key = derive('key', 32)
const aad = Buffer.concat([salt, Buffer.from([0]), Buffer.from(address)])

const PLAINTEXT_BYTES = 128
const bodies = [
  { at: 1789000061000, state: 'preparing', v: 1 },
  { at: 1789000069000, state: 'playing', v: 1 },
  { at: 1789000066000, reason: 'no_source', state: 'failed', v: 1 },
]

const results = bodies.map((body, i) => {
  const json = JSON.stringify(body)
  const plaintext = json + ' '.repeat(PLAINTEXT_BYTES - Buffer.byteLength(json))
  const nonce = range(i * 12, i * 12 + 12)
  const cipher = createCipheriv('aes-256-gcm', key, nonce)
  cipher.setAAD(aad)
  const sealed = Buffer.concat([nonce, cipher.update(plaintext, 'utf8'), cipher.final(), cipher.getAuthTag()])
  return { nonce: nonce.toString('hex'), plaintext, sealed: sealed.toString('base64url') }
})

const vectors = {
  comment:
    'wire/play-results-v1.md, computed by tools/play-results-vectors.mjs. id is inbox-v1.json\'s play envelope id; ' +
    'address and key are its HKDF derivations, aad the UTF-8 of "den/play-result/v1", 0x00 and the address. Byte ' +
    'strings are lowercase hex; plaintext is padded with spaces to 128 bytes; sealed is base64url as it travels.',
  id,
  address,
  key: key.toString('hex'),
  aad: aad.toString('hex'),
  results,
}

process.stdout.write(`${JSON.stringify(vectors, null, 2)}\n`)
