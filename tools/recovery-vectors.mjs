// Writes vectors/recovery-v1.json: every value in wire/recovery-code.md for fixed inputs. It first checks Node's
// Argon2id against RFC 9106 §5.3, and refuses to write if it differs. Run from the repository root (Node 24.7 or
// later, for crypto.argon2Sync):
//
//   node tools/recovery-vectors.mjs > vectors/recovery-v1.json
//
// Argon2id, SHA-256, HKDF and AES-GCM all come from Node.

import { argon2Sync, createCipheriv, createHash, hkdfSync } from 'node:crypto'

const hex = (bytes) => Buffer.from(bytes).toString('hex')
const utf8 = (text) => Buffer.from(text, 'utf8')
const b64url = (bytes) => Buffer.from(bytes).toString('base64url')
const range = (from, to) => Buffer.from(Array.from({ length: to - from }, (_, i) => from + i))
const sha256 = (data) => createHash('sha256').update(data).digest()
const hkdf = (ikm, salt, info, length) => Buffer.from(hkdfSync('sha256', ikm, salt, utf8(info), length))

// --- RFC 9106 §5.3, checked before anything is written ---
{
  const tag = argon2Sync('argon2id', {
    message: Buffer.alloc(32, 0x01),
    nonce: Buffer.alloc(16, 0x02),
    secret: Buffer.alloc(8, 0x03),
    associatedData: Buffer.alloc(12, 0x04),
    parallelism: 4,
    tagLength: 32,
    memory: 32,
    passes: 3,
  })
  const want = '0d640df58d78766c08c037a34a8b53c9d01ef0452d75b65eb52520e96b01e659'
  if (hex(tag) !== want) throw new Error(`RFC 9106 Argon2id: got ${hex(tag)}, want ${want}`)
}

// --- Den recovery v1 ---

const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
const DATA = 22
const LABEL = 'den/recovery/v1'
const KDF = { algorithm: 'argon2id', version: 0x13, memoryKiB: 65536, passes: 3, parallelism: 1, tagLength: 32 }

/** 22 random bytes → the code's data characters, one per byte (its low 5 bits). */
const dataFrom = (bytes) => [...bytes].map((b) => ALPHABET[b & 31]).join('')

function check(data) {
  const h = sha256(Buffer.concat([utf8(`${LABEL}/check`), utf8(data)]))
  return ALPHABET[h[0] >> 3] + ALPHABET[((h[0] & 7) << 2) | (h[1] >> 6)]
}

const display = (code) => code.match(/.{4}/g).join('-')

function parse(input) {
  const code = input.toUpperCase().replace(/[\s-]/g, '')
  if (code.length !== DATA + 2 || [...code].some((c) => !ALPHABET.includes(c))) return { error: 'mistyped' }
  const data = code.slice(0, DATA)
  if (code.slice(DATA) !== check(data)) return { error: 'checksum' }
  return { data }
}

function derive(data) {
  const A = argon2Sync('argon2id', {
    message: utf8(data),
    nonce: utf8(LABEL),
    parallelism: KDF.parallelism,
    tagLength: KDF.tagLength,
    memory: KDF.memoryKiB,
    passes: KDF.passes,
  })
  return {
    argon2id: A,
    locator: hkdf(A, utf8(LABEL), 'locator', 16),
    wrapKey: hkdf(A, utf8(LABEL), 'wrap', 32),
  }
}

function seal(wrapKey, locator, nonce, plaintext) {
  const cipher = createCipheriv('aes-256-gcm', wrapKey, nonce)
  cipher.setAAD(Buffer.concat([utf8(LABEL), locator]))
  return Buffer.concat([nonce, cipher.update(utf8(plaintext)), cipher.final(), cipher.getAuthTag()])
}

const libraryKey = range(0, 32)
const libraryId = hex(hkdf(libraryKey, utf8('den/library/salt/v1'), 'den/library/id/v1', 16))

function entry(randomBytes, nonce, createdAt) {
  const data = dataFrom(randomBytes)
  const code = data + check(data)
  const { argon2id, locator, wrapKey } = derive(data)
  const plaintext = JSON.stringify({ v: 1, libraryKey: b64url(libraryKey), createdAt })
  return {
    random: hex(randomBytes),
    data,
    check: check(data),
    code: display(code),
    argon2id: hex(argon2id),
    locator: hex(locator),
    wrapKey: hex(wrapKey),
    nonce: hex(nonce),
    plaintext,
    sealed: b64url(seal(wrapKey, locator, nonce, plaintext)),
  }
}

// Fixed "random" bytes: the first 22 bytes of SHA-256 of a label, so the codes look like real ones.
const fixed = (label) => sha256(utf8(label)).subarray(0, 22)
const first = entry(fixed('den recovery vector 1'), range(0, 12), 1790000000000)
const second = entry(fixed('den recovery vector 2'), range(12, 24), 1790000000000)

// A sealed blob moved to another locator fails to open: the AD binds it.
const swapped = (() => {
  const cipher = createCipheriv(
    'aes-256-gcm',
    Buffer.from(first.wrapKey, 'hex'),
    Buffer.from(first.nonce, 'hex'),
  )
  cipher.setAAD(Buffer.concat([utf8(LABEL), Buffer.from(second.locator, 'hex')]))
  return b64url(Buffer.concat([Buffer.from(first.nonce, 'hex'), cipher.update(utf8(first.plaintext)), cipher.final(), cipher.getAuthTag()]))
})()

// One data character wrong: caught by the check characters, before any request.
const typo = (first.code[0] === 'A' ? 'B' : 'A') + first.code.slice(1)
const manage = range(32, 64)

const vectors = {
  comment:
    'wire/recovery-code.md, computed by tools/recovery-vectors.mjs. Byte strings are lowercase hex; sealed values ' +
    'base64url, unpadded, as they travel. The library key is pairing-v1.json\'s fixed key (bytes 0x00..0x1f).',
  alphabet: ALPHABET,
  kdf: { ...KDF, salt: hex(utf8(LABEL)) },
  libraryKey: hex(libraryKey),
  libraryId,
  codes: [
    { input: first.code, parsed: { data: first.data } },
    { input: ` ${first.code.toLowerCase().replace(/-/g, ' ')} `, parsed: { data: first.data } },
    { input: typo, parsed: parse(typo) },
    { input: first.code.slice(0, -2), parsed: parse(first.code.slice(0, -2)) },
    { input: `${first.code.slice(0, -1)}0`, parsed: parse(`${first.code.slice(0, -1)}0`) },
    { input: `${first.code}-A`, parsed: parse(`${first.code}-A`) },
  ],
  entries: [first, second],
  swappedLocator: {
    comment: 'first.sealed\'s plaintext sealed under first.wrapKey with second.locator in the AD: opening it with first.locator fails',
    sealed: swapped,
  },
  manage: { manage: hex(manage), manageHash: hex(sha256(manage)) },
}

for (const c of vectors.codes) {
  const got = parse(c.input)
  if (JSON.stringify(got) !== JSON.stringify(c.parsed)) throw new Error(`parse ${c.input}: ${JSON.stringify(got)}`)
}

process.stdout.write(`${JSON.stringify(vectors, null, 2)}\n`)
