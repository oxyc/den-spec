// Writes vectors/pairing-v1.json: every value in wire/pairing-v1.md for fixed inputs. It first checks itself
// against draft-irtf-cfrg-cpace-21's ristretto255 vectors (Appendix B.3), and refuses to write if one differs.
// Run from the repository root:
//
//   npm ci --prefix tools && node tools/pairing-vectors.mjs > vectors/pairing-v1.json
//
// SHA-512, HMAC, HKDF and AES-GCM come from Node; only ristretto255 comes from @noble/curves.

import { createCipheriv, createHash, createHmac, hkdfSync } from 'node:crypto'
import { ristretto255, ristretto255_hasher } from '@noble/curves/ed25519.js'

const hex = (bytes) => Buffer.from(bytes).toString('hex')
const unhex = (text) => Buffer.from(text, 'hex')
const utf8 = (text) => Buffer.from(text, 'utf8')
const cat = (...parts) => Buffer.concat(parts.map((part) => Buffer.from(part)))
const b64url = (bytes) => Buffer.from(bytes).toString('base64url')
const range = (from, to) => Buffer.from(Array.from({ length: to - from }, (_, i) => from + i))

function expect(label, got, want) {
  if (hex(got) !== want) throw new Error(`${label}: got ${hex(got)}, want ${want}`)
}

// draft-21 Appendix A.1: LEB128 length, then the bytes.
function prependLen(data) {
  const length = []
  let n = data.length
  do {
    length.push(n < 128 ? n : (n & 0x7f) | 0x80)
    n >>= 7
  } while (n > 0)
  return cat(Buffer.from(length), data)
}
const lvCat = (...args) => cat(...args.map(prependLen))
const sha512 = (data) => createHash('sha512').update(data).digest()
const hmac512 = (key, data) => createHmac('sha512', key).update(data).digest()
const hkdf = (ikm, salt, info, length) => Buffer.from(hkdfSync('sha256', ikm, salt, utf8(info), length))

// CPACE-RISTR255-SHA512 (draft-21 §8.3).
const DSI = utf8('CPaceRistretto255')
const IDENTITY = Buffer.alloc(32)
const S_IN_BYTES = 128

function generatorString(prs, ci, sid) {
  const zpad = Math.max(0, S_IN_BYTES - 1 - prependLen(prs).length - prependLen(DSI).length)
  return lvCat(DSI, prs, Buffer.alloc(zpad), ci, sid)
}
const calculateGenerator = (prs, ci, sid) => ristretto255_hasher.deriveToCurve(sha512(generatorString(prs, ci, sid)))
const scalar = (littleEndian) => BigInt(`0x${hex(Buffer.from(littleEndian).reverse()) || '0'}`)
const scalarMult = (y, g) => Buffer.from(g.multiply(scalar(y)).toBytes())

function scalarMultVfy(y, encoded) {
  let point
  try {
    point = ristretto255.Point.fromBytes(encoded)
  } catch {
    return IDENTITY
  }
  return point.is0() ? IDENTITY : Buffer.from(point.multiply(scalar(y)).toBytes())
}
const transcriptIr = (Ya, ADa, Yb, ADb) => cat(lvCat(Ya, ADa), lvCat(Yb, ADb))
const isk = (sid, K, transcript) => sha512(cat(lvCat(cat(DSI, utf8('_ISK')), sid, K), transcript))

// --- draft-21 Appendix B.3, checked before anything is written ---

const draft = {
  prs: utf8('Password'),
  ci: unhex('0b415f696e69746961746f720b425f726573706f6e646572'),
  sid: unhex('7e4b4791d6a8ef019b936c79fb7f2c57'),
  ya: unhex('da3d23700a9e5699258aef94dc060dfda5ebb61f02a5ea77fad53f4ff0976d08'),
  yb: unhex('d2316b454718c35362d83d69df6320f38578ed5984651435e2949762d900b80d'),
  ADa: utf8('ADa'),
  ADb: utf8('ADb'),
}
const want = {
  generatorHash:
    'da6d3ddc8802fca9058755ffd3ebde08a9c2c74945901a258482a288b6663af06bf645c93cd1c51512307199c80e84908916d983b34af77205f90851a657ee27',
  g: '222b6b195fe84b1652badb6f6a3ae3d24341e7306967f0b8115b40d5698c7e56',
  Ya: 'd6bac480f2c386c394efc7c47adb9925dcd2630b64f240c50f8d0eec482b9157',
  Yb: '3ea7e0b19560d7c0b0f5734f63b955286dfa8232b5ebe63324e2d9e7433f7258',
  K: '80b69a8a76457ab6a4d7f887a4bf6b55a2f80ac19c333f917a05fc9887c8b40f',
  ISK: 'b69effbf61b51d56401c0f65601abe428de8206feaaf0e32198896dcae7b35cd2b38950a39dfd5d4a79164614c2984f7daa460b588c1e80c3fa2068af7900447',
  s: '7cd0e075fa7955ba52c02759a6c90dbbfc10e6d40aea8d283e407d88cf538a05',
  X: '2c3c6b8c4f3800e7aef6864025b4ed79bd599117e427c41bd47d93d654b4a51c',
  sX: '7c13645fe790a468f62c39beb7388e541d8405d1ade69d1778c5fe3e7f6b600e',
  invalid: '2b3c6b8c4f3800e7aef6864025b4ed79bd599117e427c41bd47d93d654b4a51c',
}
{
  const g = calculateGenerator(draft.prs, draft.ci, draft.sid)
  expect('draft generator hash', sha512(generatorString(draft.prs, draft.ci, draft.sid)), want.generatorHash)
  expect('draft g', g.toBytes(), want.g)
  const Ya = scalarMult(draft.ya, g)
  const Yb = scalarMult(draft.yb, g)
  expect('draft Ya', Ya, want.Ya)
  expect('draft Yb', Yb, want.Yb)
  expect('draft K (a)', scalarMultVfy(draft.ya, Yb), want.K)
  expect('draft K (b)', scalarMultVfy(draft.yb, Ya), want.K)
  expect('draft ISK', isk(draft.sid, unhex(want.K), transcriptIr(Ya, draft.ADa, Yb, draft.ADb)), want.ISK)
  expect('draft scalar_mult_vfy', scalarMultVfy(unhex(want.s), unhex(want.X)), want.sX)
  expect('draft invalid Y_i1', scalarMultVfy(unhex(want.s), unhex(want.invalid)), hex(IDENTITY))
  expect('draft invalid Y_i2', scalarMultVfy(unhex(want.s), IDENTITY), hex(IDENTITY))
}

// --- Den pairing v1 ---

const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
function parseCode(input) {
  const code = input.toUpperCase().replace(/[\s-]/g, '')
  if (code.length !== 12 || [...code].some((c) => !ALPHABET.includes(c))) return null
  return { nameplate: code.slice(0, 4), secret: code.slice(4) }
}
const codes = [' abcd efgh-jklm ', 'ABCD-EFGH-JKLM', 'ABCD-EFGH-JKL0', 'ABCD-EFGH-JK', 'ABCD-EFGH-JKLM-N'].map(
  (input) => ({ input, parsed: parseCode(input) }),
)

const { nameplate, secret } = parseCode('ABCD-EFGH-JKLM')
const sid = range(0, 16)
const ci = utf8('den/pair/v1')
const joinerLabel = 'Mac · Chrome'
const hostLabel = 'Living room'
const ADa = lvCat(utf8('joiner'), utf8(joinerLabel))
const ADb = lvCat(utf8('host'), utf8(hostLabel))
const [ya, yb] = [draft.ya, draft.yb]

const g = calculateGenerator(utf8(secret), ci, sid)
const Ya = scalarMult(ya, g)
const Yb = scalarMult(yb, g)
const K = scalarMultVfy(ya, Yb)
expect('pairing K agrees', scalarMultVfy(yb, Ya), hex(K))
const ISK = isk(sid, K, transcriptIr(Ya, ADa, Yb, ADb))
const macKey = sha512(cat(utf8('CPaceMac'), sid, ISK))
const Ta = hmac512(macKey, lvCat(Ya, ADa))
const Tb = hmac512(macKey, lvCat(Yb, ADb))

// The host with one character of the secret wrong: its tag differs, so the joiner stops at message b.
const wrongG = calculateGenerator(utf8('EFGHJKLN'), ci, sid)
const wrongYb = scalarMult(yb, wrongG)
const wrongISK = isk(sid, scalarMultVfy(yb, Ya), transcriptIr(Ya, ADa, wrongYb, ADb))
const wrongTb = hmac512(sha512(cat(utf8('CPaceMac'), sid, wrongISK)), lvCat(wrongYb, ADb))

const linkKey = range(32, 64)
const libraryKey = range(0, 32)
const handoverKey = hkdf(ISK, sid, 'den/pair/v1/handover', 32)
const nonce = range(0, 12)
const deviceNonce = range(12, 24)
const aad = utf8('den/pair/v1/handover')
const plaintext = JSON.stringify({ v: 1, host: hostLabel, linkKey: b64url(linkKey), libraryKey: b64url(libraryKey) })
const hostDeviceId = 'a1b2c3d4e5f60718'
const devicePlaintext = JSON.stringify({
  v: 1,
  host: hostLabel,
  hostDeviceId,
  linkKey: b64url(linkKey),
  libraryKey: b64url(libraryKey),
})
const sealHandover = (body, iv) => {
  const cipher = createCipheriv('aes-256-gcm', handoverKey, iv)
  cipher.setAAD(aad)
  return cat(iv, cipher.update(utf8(body)), cipher.final(), cipher.getAuthTag())
}
const sealed = sealHandover(plaintext, nonce)
const deviceSealed = sealHandover(devicePlaintext, deviceNonce)

const vectors = {
  comment:
    'wire/pairing-v1.md, computed by tools/pairing-vectors.mjs. Byte strings are lowercase hex; messages a–d are ' +
    'base64url as they travel. The cpace block is draft-irtf-cfrg-cpace-21 Appendix B.3, repeated so a client ' +
    'checks its CPace core before the Den layer.',
  cpace: {
    prs: hex(draft.prs),
    ci: hex(draft.ci),
    sid: hex(draft.sid),
    generatorString: hex(generatorString(draft.prs, draft.ci, draft.sid)),
    generatorHash: want.generatorHash,
    g: want.g,
    ya: hex(draft.ya),
    ADa: hex(draft.ADa),
    Ya: want.Ya,
    yb: hex(draft.yb),
    ADb: hex(draft.ADb),
    Yb: want.Yb,
    K: want.K,
    ISK: want.ISK,
    scalarMultVfy: { s: want.s, X: want.X, result: want.sX },
    invalidPoints: [want.invalid, hex(IDENTITY)],
  },
  codes,
  pairing: {
    code: 'ABCD-EFGH-JKLM',
    nameplate,
    secret,
    sid: hex(sid),
    ci: hex(ci),
    joinerLabel,
    hostLabel,
    ADa: hex(ADa),
    ADb: hex(ADb),
    generatorString: hex(generatorString(utf8(secret), ci, sid)),
    g: hex(g.toBytes()),
    ya: hex(ya),
    Ya: hex(Ya),
    yb: hex(yb),
    Yb: hex(Yb),
    K: hex(K),
    ISK: hex(ISK),
    macKey: hex(macKey),
    Ta: hex(Ta),
    Tb: hex(Tb),
    a: b64url(lvCat(Ya, ADa)),
    b: b64url(lvCat(Yb, ADb, Tb)),
    c: b64url(Ta),
    wrongSecret: { secret: 'EFGHJKLN', b: b64url(lvCat(wrongYb, ADb, wrongTb)) },
    handover: {
      key: hex(handoverKey),
      nonce: hex(nonce),
      plaintext,
      d: b64url(sealed),
    },
    handoverWithHostDeviceId: {
      key: hex(handoverKey),
      nonce: hex(deviceNonce),
      plaintext: devicePlaintext,
      d: b64url(deviceSealed),
    },
    link: {
      linkKey: hex(linkKey),
      inbox: hex(hkdf(linkKey, utf8('den/link/v1'), 'inbox', 24)),
      enc: hex(hkdf(linkKey, utf8('den/link/v1'), 'enc', 32)),
    },
  },
}

process.stdout.write(`${JSON.stringify(vectors, null, 2)}\n`)
