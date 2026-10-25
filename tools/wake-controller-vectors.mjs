// Writes vectors/wake-controller-v1.json. Run from the repository root:
//
//   node tools/wake-controller-vectors.mjs > vectors/wake-controller-v1.json

import { createHash, hkdfSync } from 'node:crypto'
import { readFileSync } from 'node:fs'

const pairing = JSON.parse(readFileSync(new URL('../vectors/pairing-v1.json', import.meta.url)))
const inboxCredential = pairing.pairing.link.inbox
const inboxDomain = 'den/wake/inbox/v1:'
const wakeInbox = createHash('sha256').update(inboxDomain, 'utf8').update(inboxCredential, 'utf8').digest('hex')

const householdPlayKey = Buffer.from(Array.from({ length: 32 }, (_, i) => i))
const assistantLane = Buffer.from(
  hkdfSync('sha256', householdPlayKey, Buffer.from('den/link/v1'), Buffer.from('assistant-tv'), 16),
).toString('hex')

const vectors = {
  comment:
    'wire/wake-controller-v1.md, computed by tools/wake-controller-vectors.mjs. The inbox credential is ' +
    'pairing-v1.json\'s fixed link. Inputs and outputs are lowercase hex. wakeInbox hashes the UTF-8 domain and ' +
    'canonical credential text; assistantLane repeats assistant-v1.',
  inbox: {
    domain: inboxDomain,
    credential: inboxCredential,
    wakeId: wakeInbox,
  },
  assistant: {
    householdPlayKey: householdPlayKey.toString('hex'),
    salt: 'den/link/v1',
    info: 'assistant-tv',
    laneId: assistantLane,
  },
  invalidInboxCredentials: [
    inboxCredential.toUpperCase(),
    `${inboxCredential}00`,
    inboxCredential.slice(0, -2),
    ` ${inboxCredential}`,
  ],
}

process.stdout.write(`${JSON.stringify(vectors, null, 2)}\n`)
