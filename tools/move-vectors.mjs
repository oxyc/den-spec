// Writes vectors/library-v4-moves.json: moving a library to another key (wire/library-v4.md §12, §15 *Moves*). A move
// is the clients' — sealing under two keys — so den-core's library-v4.json can't pin it. Rows are sealed as
// wire/library-v2.md §2 says, with fixed nonces. Run from the repository root:
//
//   node tools/move-vectors.mjs > vectors/library-v4-moves.json

import { createCipheriv, createHmac, hkdfSync } from 'node:crypto'
import { deflateRawSync } from 'node:zlib'

const DEVICE = 'a1b2c3d4e5f60718'
const OTHER = '0f1e2d3c4b5a6978'
const at = (t, d = DEVICE) => [t, 0, d]

function keys(libraryKey) {
  const derive = (salt, info, length) => Buffer.from(hkdfSync('sha256', libraryKey, Buffer.from(salt), Buffer.from(info), length))
  return {
    libraryKey: libraryKey.toString('base64'),
    id: derive('den/library/salt/v1', 'den/library/id/v1', 16).toString('hex'),
    member: derive('den/library/v2', 'member', 32).toString('hex'),
    enc: derive('den/library/v2', 'enc', 32),
    mac: derive('den/library/v2', 'mac', 32),
  }
}

/** RFC 8785 for the values these vectors use: objects, arrays, strings, integers, booleans and null. */
function jcs(value) {
  if (Array.isArray(value)) return `[${value.map(jcs).join(',')}]`
  if (value && typeof value === 'object') {
    const keys = Object.keys(value).sort()
    return `{${keys.map((k) => `${JSON.stringify(k)}:${jcs(value[k])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

/** A document's plaintext (§4): 0x00, then raw DEFLATE of its JCS. */
const compressed = (document) => Buffer.concat([Buffer.from([0]), deflateRawSync(Buffer.from(jcs(document)))])
const json = (row) => Buffer.from(JSON.stringify(row))

let nonces = 0
function seal(library, name, plaintext) {
  const mac = createHmac('sha256', library.mac).update(name).digest()
  const nonce = Buffer.alloc(12)
  nonce.writeUInt32BE(++nonces, 8)
  const cipher = createCipheriv('aes-256-gcm', library.enc, nonce)
  cipher.setAAD(mac)
  const sealed = Buffer.concat([nonce, cipher.update(plaintext), cipher.final(), cipher.getAuthTag()])
  return { k: mac.toString('hex'), v: sealed.toString('base64url') }
}
const k = (library, name) => createHmac('sha256', library.mac).update(name).digest('hex')

const from = keys(Buffer.alloc(32, 9))
const to = keys(Buffer.alloc(32, 3))

const film = {
  format: 4, kind: 'title', title: { type: 'movie', id: 550 },
  status: { value: 'watched', at: at(1789000000000) },
  addedAt: 1788000000000, watchedAt: 1760000000000,
  watch: { plays: { 0: 1760000000000, 1: 1789000000000 }, cleared: null },
}
const season = {
  format: 4, kind: 'season', title: { type: 'tv', id: 1399 }, season: 1, seasonReset: null,
  episodes: { 2: { progress: { value: 1, at: at(1788900000000), viewing: 0 }, imported: false, plays: { 0: 1788900000000 }, cleared: null } },
}
const receipts = {
  format: 4, kind: 'delivery', provider: 'simkl', account: '4812736', title: { type: 'movie', id: 550 },
  entries: { list: ['in', at(1788000000000), [3, 41, DEVICE]] },
}
const settings = (name, values) => ({ kind: 'set', schema: 2, name, values })
const prefs = settings('prefs', { 'den.hideAnime': { value: { bool: true }, at: at(1788000000000) } })
const deliver = settings('deliver:simkl:4812736', {
  since: { value: { string: JSON.stringify(at(1787000000000)) }, at: at(1787000000000) },
  lease: { value: { strings: [OTHER, '7'] }, at: at(1789000000000, OTHER) },
})
const recovery = settings('recovery', {
  '00112233445566778899aabbccddeeff': { value: { string: '{"state":"live"}' }, at: at(1788000000000) },
})
const devices = settings('devices', {
  [`${DEVICE}.name`]: { value: { string: 'Living room' }, at: at(1788000000000) },
  [`${DEVICE}.kind`]: { value: { string: 'tv' }, at: at(1788000000000) },
  [`${OTHER}.name`]: { value: { string: 'Phone' }, at: at(1788000000000, OTHER) },
})
const newer = Buffer.from(jcs({ format: 5, kind: 'title', title: { type: 'movie', id: 680 }, shelf: 'future' }))

/** A source row, and what the destination holds for it after the move: decoded `row`, exact `plaintext`, or nothing. */
const row = (name, plaintext, after) => ({ name, ...seal(from, name, plaintext), after })
const moved = (name, value) => ({ name, k: k(to, name), row: value })

const reset = [
  row('title:movie:550', compressed(film), moved('title:movie:550', film)),
  row('season:tv:1399:1', compressed(season), moved('season:tv:1399:1', season)),
  row('dlv:simkl:4812736:movie:550', compressed(receipts), moved('dlv:simkl:4812736:movie:550', receipts)),
  row('set:prefs', json(prefs), moved('set:prefs', prefs)),
  row('set:deliver:simkl:4812736', json(deliver), moved('set:deliver:simkl:4812736', deliver)),
  row('set:recovery', json(recovery), null),
  row('set:devices', json(devices), moved('set:devices', settings('devices', {
    [`${DEVICE}.name`]: devices.values[`${DEVICE}.name`],
    [`${DEVICE}.kind`]: devices.values[`${DEVICE}.kind`],
  }))),
  row('title:movie:680', newer, { name: 'title:movie:680', k: k(to, 'title:movie:680'), plaintext: newer.toString('base64url') }),
  // Sealed under one document's name but holding another's: it can't be attributed, and stays behind.
  { name: 'title:movie:999', ...seal(from, 'title:movie:999', json(title1())), after: null },
]

function title1() {
  return { format: 4, kind: 'title', title: { type: 'movie', id: 1 } }
}

const vectors = {
  version: 1,
  spec: 'wire/library-v4.md',
  notes: [
    'Generated by tools/move-vectors.mjs. Moving a library to another key (§12): a key reset, or linking into another library. §15 *Moves*.',
    'from and to are library keys (base64, 32 bytes) with what they derive (library-v2 §1). Rows are {k, v} as den-edge stores them in `from`, sealed with fixed nonces; `name` is what k is the HMAC of, given for reading.',
    'Each case: a client reads `rows` from `from` and moves them to `to` as §12 says, with `device` the one moving it. `refused` names why the move does not start, and then nothing is written and `from` keeps every row. Otherwise the destination holds exactly the rows `after` lists, by k: `row` as it decodes (JCS-equal), or `plaintext` (base64url) byte for byte. A null `after` is a row the move leaves behind.',
    "The destination's first write carries the `first_batch` headers.",
  ],
  device: DEVICE,
  from: { libraryKey: from.libraryKey, id: from.id, member: from.member },
  to: { libraryKey: to.libraryKey, id: to.id },
  first_batch: { 'x-den-wire-min': '4', 'x-den-library-member': `${from.id}:${from.member}` },
  cases: [
    {
      name: 'a reset re-seals every document and setting under its new name; set:recovery, other devices and an unattributable row stay behind; a format-5 document keeps its plaintext',
      rows: reset,
    },
    {
      name: 'a row of a kind this build does not know refuses the move',
      rows: [reset[0], row('playlist:x', json({ kind: 'playlist', name: 'x' }), null)],
      refused: 'update_required',
    },
    {
      name: 'a row of a newer framing refuses the move',
      rows: [reset[0], row('playlist:y', Buffer.from([1, 2, 3]), null)],
      refused: 'update_required',
    },
  ],
}

process.stdout.write(`${JSON.stringify(vectors, null, 2)}\n`)
