# Household profiles, v1

**Status: active runtime contract.** This document is the normative profile topology for Den. It defines the
household and profile data model, persistent selection, policy, adoption filtering, Kid creation and compound key
reset. The one-time existing-household split and its transport were retired after rollout; they are not part of this
contract. Stored directory `migration` evidence remains permanent provenance and is still validated.
Words: *MUST* is a rule a client breaks at the cost of another client or profile; *should* is advice.

## 1. Topology and wire version

A profile-aware installation has this topology:

```text
household library (configuration only; profile-aware requests use wire 5)
├── household settings and download rows
├── profiles document
├── Kid A title-approval document
├── Kid B title-approval document
└── persistent profile keys
    ├── Owner  ── random key ──> ordinary library-v4 activity log
    ├── Kid A  ── random key ──> ordinary library-v4 activity log
    └── Kid B  ── random key ──> ordinary library-v4 activity log

Visitor ──> device-local namespace; no library and no directory entry
```

**Wire 5 is library-v4 row framing, sealing, settings and document merge rules plus the household/profile topology
in this document.** It is not a fifth row encoding. The household's rows use library-v4 §4 plaintext framing and
library-v2 sealing. A persistent profile's log is an ordinary library-v4 library: requests to that log advertise
`x-den-wire: 4`, and a new profile log starts with `x-den-wire-min: 4`. Profile-aware requests to a committed
household advertise `x-den-wire: 5`.

### 1.1 Runtime wire gate

A committed profile-aware household has minimum wire 5 and an authenticated `profiles` row which opens under the
household key, has the logical name `profiles`, and passes den-core stored-directory validation. Ordinary household
requests advertise wire 5; persistent profile libraries remain ordinary wire-4 libraries. A lower-wire request to
the household is update-required, never an empty or reset library. A minimum above the supported wire is likewise
update-required.

A missing, unreadable, name-mismatched or invalid directory at minimum 5 is recovery-blocked. The client MUST NOT
fall back to a legacy Owner, synthesize a directory, or expose profile activity. A visible directory below minimum 5
is an inconsistent partial state and also fails closed. New profile-aware households are constructed through the
current setup flow; no retired split capability or migration-storage route is implied by this runtime state.

The household key seals the directory, including all profile keys. Profiles are UX and data-isolation boundaries,
not protection from a hostile device that holds the household key. Selectively wrapped private profiles are out of
scope.

### 1.2 Library source and the exclusive-operation lock

The snapshot and exclusive-operation primitives serve compound key reset and construction of a household's first
directory. They belong to the persistent topology.

**Source counter.** den-edge keeps, for every library, a durable monotone safe-integer counter in the
historical **profile-migration fence namespace** (the stored name remains for compatibility). Reading it does not
advance it; every visible mutation of that library (an applied ordinary batch, a rewrite, a restore) advances it
atomically. It is never restored backwards or reused: a restore advances from the provider-retained high-water mark,
and the vanishingly unlikely safe-integer exhaustion refuses mutation rather than wrapping. It is not the public
`x-den-generation` sync header, is not parsed from that header and need not change when that header changes.

**Snapshot digests.** Every digest here is lowercase hexadecimal SHA-256 of RFC 8785 JCS bytes. Integers are
nonnegative JSON safe integers. A snapshot row is exactly `{k, seq, v}`; rows are sorted by the raw UTF-8 bytes of `k`
before canonicalization. Duplicate row keys are invalid, and sequences are distinct and positive. No implementation may
substitute a platform's ordinary sorted-key JSON mode unless it is proved byte-identical to RFC 8785 for the complete
accepted input domain.

- The **source** digest hashes `{libraryId, generation, base, wireMin, rows}`: the library's own id, its source
  counter, its sequence head, its stored minimum and every row through `base`. It is the authenticated `head` of a
  source observation `{generation, head}`. It includes the source counter, not the opaque public sync generation.
- The **profile-library head** hashes `{libraryId, base, wireMin, rows}` for an ordinary library-v4 profile library,
  read back through its observed head. It excludes both the source counter and the library's public sync generation.
  It is the directory's `migration.ownerHead` (§3) for the Owner library a first directory names.

The two are domain-separated by their distinct exact shapes. `profiles_snapshot_digest(kind, input)` is the den-core
boundary, with `kind` exactly `source` or `owner` (the profile-library head); any other kind is
`invalid_migration_digest_kind`. `snapshotDigestCases` in `vectors/profiles-v1.json` pin both answers. The operation
performs no I/O.

**Exclusive-operation lock.** A library carries at most one durable exclusive-operation lock for the §9.1 key reset.
It is acquired only at an exact `{generation, head}` the caller observed, and only while no other lock is held; the
only takeover is §9.1's journal-less recovery. Repeating the acquiring request is a
lookup that returns the stored answer; anyone else is refused with exactly `409 {"error":"migration_in_progress"}` and no attempt, purpose, fence, source or
timing metadata, before any comparison with the current source. While it is held, every ordinary mutation of the
library is refused before ordinary generation and rewrite handling with that same `409` and no effect; reads continue.
The lock and its record survive client and den-edge restart and never expire on an idle timer or a disconnect. Its
release is atomic with the terminal effect that ends it. A source that moved is `409
{"error":"migration_source_changed"}` with no side effect. These error codes keep their deployed names.

## 2. What each store may hold

The **household library** is configuration only. A profile-aware client MUST NOT derive or write title activity,
season activity, reactions, resume state, personal recommendations, tracker commands, tracker receipts, tracker
delivery documents, or tracker credentials there. It may hold:

- the `profiles` document (§3) and per-Kid title-approval documents (§5.1);
- the household-scoped settings in §6 (never `set:prefs`), including devices, recovery, credentials, addons, servers, routes/trust,
  private addresses, owner PIN and Visitor policy;
- the library-v4 §17 download rows and download lease; and
- rows of a newer kind that it must preserve under library-v4's unknown-row rule.

Every live **persistent profile**, Owner included, has exactly one distinct random library key and an ordinary
library-v4 activity log. That log holds title and season documents, personal preferences, and, only when the
profile owns a tracker, tracker credentials, delivery settings and delivery documents. The first product may expose
only Owner and Kid creation, but the representation accepts any number of `kid` and future `adult` profiles.

**Once a household is profile-aware, no client writes a profile-scoped row to it, on any path** — ordinary write,
kept write, write-back, merge, link or restore. The profile-scoped rows are `title:*`, `season:*`, `dlv:*`,
`set:deliver:*`, `set:handoff:*`, `set:trackers`, `set:assistant-applied`, the legacy `rec:*`, `wat:*`, `snt:*`,
`ep:*` and `set:tracker-event:*`, `set:prefs` in full (§6), and the `simkl` value of `set:keys`. A household-scoped value in `set:keys` may be
written; only the profile-scoped values are withheld. `profile_household_write` (§8) is the one rule every
client asks, so Swift and web cannot disagree on it.
Clients MUST use the same profile-store path for Owner and every other persistent profile; there is no permanent
Owner projection in the household and no Kid-specific row format.

A profile library MUST NOT hold the profile directory, owner PIN, household credentials, connections, devices,
downloads, Visitor state, or another profile's data. Household values are read from the simultaneously open
household control log; they are never copied into every profile log. The one exception is the one-time services
and region seed of §6.2.

## 3. Profile-directory document

The directory is one document named `profiles`. Its row key is
`hex(HMAC-SHA256(macKey, UTF8("profiles")))`. It is sealed and compressed exactly like a library-v4 document:
`0x00` followed by raw DEFLATE of its RFC 8785 JCS. Its logical JSON is validated by
[`schemas/profiles-v1.schema.json`](../schemas/profiles-v1.schema.json).

```json
{
  "format": 4,
  "kind": "profiles",
  "schema": 1,
  "owner": "00112233445566778899aabbccddeeff",
  "migration": {
    "version": 1,
    "attempt": "0123456789abcdef0123456789abcdef",
    "source": {"generation": 7, "head": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    "backup": {"id": "backup-7", "digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
    "ownerProfile": "00112233445566778899aabbccddeeff",
    "ownerHead": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  },
  "profiles": {
    "00112233445566778899aabbccddeeff": {
      "id": "00112233445566778899aabbccddeeff",
      "role": "owner",
      "display": {"value": {"name": "Cindy", "avatar": "fox"}, "at": [1800000000000, 0, "a1b2c3d4e5f60718"]},
      "library": {"value": {"keyVersion": 1, "key": "ERERERERERERERERERERERERERERERERERERERERERE="}, "at": [1800000000000, 1, "a1b2c3d4e5f60718"]},
      "policy": {"value": {"maximumAge": null, "unknownRatings": "allow"}, "at": [1800000000000, 2, "a1b2c3d4e5f60718"]},
      "deleted": {"value": false, "at": [1800000000000, 3, "a1b2c3d4e5f60718"]}
    }
  }
}
```

### Identities and fields

- A **profile id** is 16 random bytes written as 32 lowercase hexadecimal characters. It is stable for the life of
  the profile, is not a credential, and is never reused after deletion. The map key MUST equal the entry's `id`.
- `owner` is immutable and names the one entry whose immutable `role` is `owner`. There is exactly one owner; it
  cannot be deleted. `kid` is owner-managed. `adult` reserves the same persistent-profile path for a later UI and
  requires no format change; this revision provides no create-adult product flow.
- `display.value.name` is a nonempty NFC string of at most 64 Unicode scalar values. `avatar`, when present, is an
  opaque built-in asset id of 1–64 ASCII letters, digits, `_` or `-`. Cloud-hosted or arbitrary uploaded avatars
  are not defined here.
- `library.value.key` is a canonical padded base64 encoding of exactly 32 random bytes. It is not derived from the
  household key, profile id, name, role or another profile key. `keyVersion` starts at 1 and increases on a reset;
  zero is invalid. Two live profiles MUST NOT carry the same key.
- `policy.value.maximumAge` is an integer from 0 through 18, or `null` for no age ceiling. Values outside this
  supported human-age range are invalid rather than silently treated as policy.
  `unknownRatings` is `allow` or `block`. The effective check is in §5. Unknown policy members are extensions
  and travel with the stamped policy value that wins.
- `display`, `library`, `policy` and `deleted` are stamped fields. Stamps and their clock/future handling are
  library-v2 §4. A deletion is permanent: a deleted id cannot become live again, its library is retired by the
  lifecycle operation, and recreation uses a new id and key. The tombstone retains the last library reference so
  interrupted retirement can be completed; no activity is read from it.
- A stored directory MUST contain its immutable historical `migration` provenance. `attempt` is a random 16-byte id.
  `source` identifies the exact source generation and head used to construct it. `backup.id` and `backup.digest`
  retain the verified rollout evidence. `ownerProfile` MUST equal
  `owner`, and `ownerHead` is the verified activity-snapshot digest of that entry's destination library before
  commit: §1.2's profile-library head. It is deliberately not den-edge's opaque public sync generation and cannot reuse the
  household source head, whose input contains a provider-only migration generation. All digests and heads are 32
  bytes in lowercase hexadecimal. These values are immutable evidence, not a progress register;
  `migration` is persistent directory provenance, not active split machinery: stored validation (§3.1) requires it
  of every stored directory, whatever constructed it (§6.1).

The schema permits extension fields so a newer client can add display or lifecycle facts. A writer keeps unknown
fields byte-for-byte in the logical document unless den-core tells it how to merge them. A client that does not know
the `profiles` kind keeps its sealed row under library-v4's unknown-kind rule.

### Canonical validation and merge ownership

Clients do not independently implement directory decisions. den-core operations are authoritative:

- `profiles_validate(document)` returns the canonical document or an error code;
- `profiles_merge(a, b)` validates both versions, merges them, then validates the result; and
- lifecycle operations construct creates, renames, policy changes, deletions and key changes from validated input.

The merge is the union of profile ids. `owner`, `migration`, each entry's `id`, and `role` are immutable;
disagreement is `identity_conflict`. A directory without `migration` is accepted only as an uncommitted constructor
input and MUST NOT be published. Known stamped fields use library-v2's later-stamp rule, except `library`: greater `keyVersion`
wins first and its stamp breaks a tie. A `deleted: true` is irreversible even if another valid input has a later
false value. Unknowns use library-v4 §6's unknown-field rule. The merged document is rejected if the owner is
missing/deleted, map keys and ids differ, a role conflicts, a live key is duplicated, or any field is malformed.

An implementation MUST call these den-core operations for validation and convergence; matching Swift and
TypeScript reimplementations are not conforming. Vectors pin the operation boundary. The schema catches shape and
local values, while den-core catches cross-entry and historical invariants.

### 3.1 Canonical stored-directory materialization

`profiles_materialize(document)` is the only operation that turns an already constructed directory into bytes for
an ordinary post-migration household write. Its one input is the complete logical document; it accepts no logical
name, plaintext, compressor choice or storage authority from the caller. The operation:

1. validates `document` as a **stored** directory, so `migration` is mandatory;
2. enforces library-v4's maximum JSON nesting depth 32, serializes the unchanged logical value as RFC 8785 JCS,
   and refuses JCS above the 8 MiB inflation cap;
3. constructs library-v4 document plaintext as byte `0x00` followed by one complete raw-DEFLATE stream; and
4. inflates and parses its own result, validates it again as stored, and requires both the logical value and JCS
   bytes to round-trip exactly with no trailing compressed bytes.

The exact result shape is `{directory,jcs,plaintext}`. `directory` is the validated input value; `jcs` and
`plaintext` are unpadded base64url. A client seals only the returned `plaintext` and never serializes or compresses
`directory` itself. The native and WASM den-core bindings expose the same operation and error strings. Invalid
logical input is `invalid_profiles_materialize`, a missing migration proof is `profiles_migration_required`, an
oversize canonical document is `profiles_materialize_too_large`, and failure of the self-check is
`profiles_materialize_roundtrip`. As elsewhere in library-v4, the framing and inflated JCS are canonical while the
DEFLATE bitstream is den-core output rather than a cross-version identity.

### 3.2 Crash-safe persistent Kid creation

`profiles_create_next(evidence)` is den-core's pure lifecycle planner for persistent Kid creation. It performs no
storage or network I/O. Only an explicit, Owner-PIN-authorized Create Kid action may supply `explicitIntent: true`;
ordinary launch or upgrade supplies no intent and is a no-op. Before **any** library creation, household write or
other I/O, the client generates a random 16-byte profile id, random 32-byte library key and all four stamps, and
atomically persists them in secure local storage as this version-1 journal payload:

```json
{
  "version": 1,
  "profile": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "key": "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVU=",
  "display": {"value":{"name":"Sam","avatar":"fox"},"at":[1800000005000,0,"a1b2c3d4e5f60718"]},
  "libraryAt": [1800000005000,1,"a1b2c3d4e5f60718"],
  "maximumAge": 8,
  "policyAt": [1800000005000,2,"a1b2c3d4e5f60718"],
  "deletedAt": [1800000005000,3,"a1b2c3d4e5f60718"]
}
```

`maximumAge` is the Owner's choice and MUST be an integer from 0 through 18. Creation constructs role `kid`, key
version 1, `deleted: false`, and policy `{maximumAge,unknownRatings:"block"}`. `unknownRatings` is not a creation
choice: `profiles_apply`'s create operation forces `block` even if a legacy/full-entry caller supplies `allow`.
Loosening that policy requires a later, separately PIN-gated policy operation. The id, key and stamps in a live
journal are immutable and reused after every restart; a client never generates replacements merely because an I/O
result is unknown.

The journal's durable state then drives these effects, pinned by `profileCreateCases`:

- `persist-journal` is the only first effect. After it is durable, `create-profile-library` idempotently creates the
  ordinary wire-4 profile library from the journal key. `apply-create` asks `profiles_apply` for the complete Kid
  entry and candidate directory; clients do not assemble policy or stamps independently.
- Every candidate directory is passed to `profiles_materialize`. The household's authenticated profiles-row read
  supplies both its safe-integer row `base` and opaque `x-den-generation` string; `cas-directory` submits the
  materialized sealed row using the existing batch compare-and-swap over that exact pair. This is the ordinary
  library generation, not the historical source counter. No new den-edge endpoint is defined.
- A CAS conflict first performs a fresh authenticated read, calls `profiles_merge(candidate,current)` in den-core,
  materializes the merged result, and retries against the fresh `{base,generation}`. It never performs a blind
  last-write-wins retry.
- A missing response is `cas-unknown`. It also performs a fresh authenticated read. It may adopt only when
  `profiles_merge(candidate,current)` equals `current` byte-for-byte in RFC 8785 JCS, proving the authoritative
  directory already subsumes the candidate. Otherwise it follows the same merge-and-CAS path as a conflict.
- The directory is authoritative. A concurrent rename is adopted. If a reset made another key authoritative for
  the same profile id, the journal key's library is retired before adoption; if the directory contains a tombstone,
  that library is retired and the creation is discarded rather than selecting the deleted profile. Cancellation and
  cleanup keep the journal id/key until a fresh authenticated directory proves that key is absent from every live
  entry; only after confirmed retirement may the journal be discarded. A successful CAS response likewise requires
  a fresh directory read before adoption, and active selection requires fresh authority before final journal discard.

The secure journal also records each returned planner state, candidate and last authenticated CAS evidence before
performing its next effect. A corrupt or incomplete journal, invalid directory, missing fresh read, unsafe key
retirement, or evidence that a supposedly committed directory does not subsume the candidate fails closed. The
operation is deterministic and has identical native/WASM results; the clients own only durable execution of its
effects.

## 4. Owner, Kid and Visitor

**Owner** is a persistent profile. Its activity and personal settings live only in its profile library. Owner is
the only role that manages the directory, Kid policy, owner PIN, household connections, devices and downloads.
An existing owner's missing/unknown certification remains fail-open: its migrated/default policy has
`unknownRatings: "allow"`.

**Kid** is a persistent profile with independent activity, preferences and recommendations. Creation requires an
owner PIN to exist and defaults `unknownRatings` to `block`; the owner chooses the initial `maximumAge`. Only an
owner-authorized operation writes a Kid's policy or title approvals. A Kid session receives neither writer and has
no tracker in v1. A Kid's Settings, which open only with the owner PIN (§6), are such an operation: the PIN that
opened them authorizes `profiles_apply` `policy` for that Kid, written to the directory with the exact-row
compare-and-swap that creates a Kid. A conflict or a lost response is read again, and a policy the directory already
holds is not rewritten.
Kid cannot manage profiles or household connections. Resource-consuming download actions are disabled or gated by
the owner PIN.

**Visitor** is never a persistent profile. It has no directory entry, key, synced library, tracker writer,
assistant target or taste/recommendation training output. It may use household playback services and keeps resume
and Continue Watching only in one device-local visit namespace. It inherits Owner's effective policy in v1 and is
never implicitly unrestricted. An unpaired browser's personal local library is not Visitor.

Assistant/companion activity operations without an explicit profile id target Owner. Remote play targets the TV's
active mode and MUST enforce that mode's policy; it MUST NOT record against Owner merely because Kid or Visitor is
active.

## 5. Policy decisions

The title signal is `minimumAge`: an integer from 0 through 18, or unknown. Values outside that supported human-age
range are invalid. Unknown is a first-class value, not
zero and not evidence that a title is suitable for every age. Dataset and runtime sources resolve confidently
matched versions and authorities conservatively before producing this value; the highest verified applicable age
wins, as specified by den-dataset#226. Profile policy stores `maximumAge`, with `null` meaning no age ceiling.

Absent an explicit Kid approval (§5.1), a known title is eligible exactly when `maximumAge` is null or
`minimumAge <= maximumAge`; an unknown title is eligible exactly when `unknownRatings` is `allow`, or, for a Kid,
when the title is **made for children** (`made_for_children >= 0.5`). `made_for_teens` never admits an unknown
age: teens are 13 to 17, too wide to stand in for a missing rating, so a teen-audience title needs a known age.
That admission is a Kid rule, not a maturity age: it applies only where `minimumAge` is unknown, never overrides a
known one, and does not apply to Owner or Visitor policies. Conversely, a Kid's title that depicts
`sexual_content`, `graphic_violence`, `drug_use` or `self_harm` (the store's `depicts` evidence at its 0.5 floor)
and is not made for children is ineligible whatever its age, and fails the audience-relevance gate in every band,
so a single lenient public rating of an adult work cannot admit it; only an explicit approval (§5.1) overrides that
veto. `animal_harm` is not a veto. Owner migration
decodes legacy `pg13` as `maximumAge: 13`, `r` as `maximumAge: 17`, and absent/null as `maximumAge: null`; all three
preserve Owner's existing `unknownRatings: allow`. No named maturity band remains in the policy or title contract.

Age eligibility MUST be applied at discovery before scoring, ranking, limiting and pagination, so filtering cannot
produce a sparse page when eligible inventory exists. It MUST be checked again before showing a direct/deep-linked
detail or ambient trailer and at Play, Sources, autoplay/binge-next, download/offline use, companion/MCP/assistant
play and remote-play boundaries. Cache and ETag identity includes `maximumAge`, `unknownRatings`, and the Kid's
effective approval state. A source that cannot establish eligibility under a blocking policy is omitted or denied.
[den-atlas#141](https://github.com/oxyc/den-atlas/issues/141) owns pre-ranking and pre-pagination Atlas enforcement.

Age eligibility answers whether a title may be watched; it does not make every harmless adult title appropriate for
a Kid catalogue. Kid discovery therefore has a second, independent **audience-relevance gate**. After the age gate
and still before scoring, ranking, limiting and pagination, a title is discoverable for a Kid only when the store's
`audience` table has `made_for_children >= 0.5`, or, when the Kid's `maximumAge` is 12 through 18 or null,
`made_for_teens >= 0.5`. These are audience-intent signals: a Family/Kids genre, animation technique, young cast,
gentle tone, low intensity or absence of warnings is not sufficient on its own. Missing audience evidence fails
closed for Kid discovery. This gate applies to browse rows, recommendations and search results, including candidates
fetched to refill a page after filtering.

The audience-relevance gate is presentation policy, not a second maturity rating. It does not apply to Owner or
Visitor discovery, does not change `minimumAge`, and does not deny an otherwise age-eligible direct/deep-linked
detail, Play, Sources, download, autoplay or remote-play request. Those boundaries continue to use the age policy
and title approvals above, including the made-for-audience admission of an unknown title and the warning veto. Atlas
decides them from its store and answers a direct read's `/index/title` with `eligible` for the request's policy, so
every client applies the same answer; a client adds only the Kid's own approvals, which atlas never sees. This distinction lets an owner deliberately open a specific obscure or adult-oriented
but age-eligible work without filling the Kid's normal catalogue with such titles. Atlas cache and ETag identity for
Kid discovery additionally includes the Kid role and the child-only versus child-or-teen audience band.

Policy ordering is the product order of two components. A lower numeric `maximumAge` is stricter than a higher one,
and every numeric maximum is stricter than null; `unknownRatings: block` is stricter than `allow`. Policy A is
stricter than B only when neither component is looser and at least one is stricter. Opposite movements are
incomparable. A stricter remote policy applies immediately; equal policy changes nothing; a looser or incomparable
policy waits for the owner PIN on a device already enforcing a policy.

### 5.1 Per-Kid title approvals

Each Kid may have one owner-managed document in the household control library. Its logical name is
`kid-title-approvals:<kid-profile-id>` and its row key is
`hex(HMAC-SHA256(macKey, UTF8(<logical name>)))`. It uses the same compressed library-v4 document framing as the
directory and is deliberately separate from the Kid's activity log:

```json
{
  "format": 4,
  "kind": "kid-title-approvals",
  "schema": 1,
  "kid": "11112222333344445555666677778888",
  "approvals": {
    "movie:550": {"value": "allow", "at": [1800000010000, 0, "a1b2c3d4e5f60718"]},
    "tv:1399": {"value": "tombstone", "at": [1800000011000, 0, "a1b2c3d4e5f60718"]}
  }
}
```

- `kid` is immutable, equals the profile id in the logical name, and MUST name a live directory entry whose role is
  `kid`. There is no approval document for Owner, adult or Visitor.
- Approval keys are exactly `movie:<TMDB id>` or `tv:<TMDB id>`, with a positive uint32 id (`1..4294967295`) in
  canonical decimal. This is the store's packed title identity bound. The key is stable across title metadata
  changes and distinguishes a movie from a series with the same numeric id.
- Each entry is a stamped `allow` or `tombstone`. Removing approval writes a fresh tombstone and retains the key;
  physical absence is only “no known decision” and cannot erase an allow learned from another replica. Merge takes
  the union of keys and, per key, the later stamp. On an equal-stamp value conflict, `tombstone` wins; otherwise the
  byte-greater JCS wins, making merge commutative, associative and idempotent. A later owner-authorized `allow` may
  intentionally approve the title again.
- Only an owner-authorized operation may create or change this document. A Kid session can read its effective
  entries but receives no writer. Deleting the Kid makes all of its approvals ineffective; retirement is part of
  the deferred profile lifecycle contract, not an activity write.
- A live `allow` overrides `maximumAge` and `unknownRatings` only for this one Kid and title. It does not weaken the
  stored policy, affect another Kid or Visitor, or turn approval into evidence about the title's `minimumAge`.
- Document-level extensions and entry members unknown to a client are preserved. Entry extensions travel with the
  winning stamped entry. Top-level unknowns use library-v4 §6's unknown-set rule, using the document's greatest
  approval stamp as its monotone newest stamp; a document with none ranks below one with a stamp.

The existing owner-PIN-gated **play once** action remains device-local and session/playback scoped. It writes no
approval, profile activity policy, or household row, is not synced, and is discarded after that playback attempt
or when the active mode changes. Persistent approval is a distinct owner action and never an accidental side
effect of play once.

## 6. Normative settings ownership

**Every key of `set:prefs` is profile-scoped**: the watch region, picked streaming services, subtitle and audio
language, content filters, rating sources, autoplay and skip settings, and every key a later contract adds. There is
no per-key list and no household half of `set:prefs`. A client MUST NOT infer any other scope from a row name, UI
section or prefix.

`prefs.den.maturityCeiling` is a legacy input: it is decoded into Owner policy by §5 and then removed.
`keys.simkl` and future tracker credentials move to the owning profile. What stays household-wide is what is not a
viewing preference: the profiles directory and Kid approvals, `keys.parentalPIN` (the owner PIN), metadata and
Cloudflare credentials, `plugins.*`, `servers.*`, `trust.*`, `addresses.*`, `devices.*`, `recovery.*`, and download
rows and policy. The directory's Kid policy and approvals are owner-controlled household documents (a Kid
profile's Settings may edit them only behind the owner PIN); Visitor inherits Owner policy.

The following device categories are intentionally non-exhaustive and are not part of that contract:

| Device-only category | Rule |
|---|---|
| OLED and presentation preferences | Remain on that device |
| active profile/Visitor marker and Visitor namespace | Follow §7; never enter either library |
| transient play-once authorization and retained navigation | Clear at the boundaries in §§5.1 and 7 |

Every persistent profile opens its own `set:prefs` and reads and writes only that. Personal values are never copied
between profiles (the seed of §6.2 reads the household, not a profile).

### 6.1 Initial Owner-directory construction

`profiles_construct_directory(input)` is the only canonical constructor for the first stored directory. It is pure
and accepts no clock, randomness, ciphertext, storage handle or network authority beyond the explicit
verified values below:

```json
{
  "attempt": "0123456789abcdef0123456789abcdef",
  "source": {"generation": 7, "head": "<64 lowercase hex>"},
  "backup": {"id": "backup-7", "digest": "<64 lowercase hex>"},
  "owner": {
    "id": "00112233445566778899aabbccddeeff",
    "display": {"value": {"name": "Cindy", "avatar": "fox"}, "at": [1800000000000, 0, "a1b2c3d4e5f60718"]},
    "libraryKey": "ERERERERERERERERERERERERERERERERERERERERERE=",
    "libraryAt": [1800000000000, 1, "a1b2c3d4e5f60718"],
    "policy": {"value": {"maximumAge": 13, "unknownRatings": "allow"}, "at": [1800000000000, 2, "a1b2c3d4e5f60718"]},
    "deletedAt": [1800000000000, 3, "a1b2c3d4e5f60718"]
  },
  "ownerHead": "<64 lowercase hex>"
}
```

Every object above has exactly the shown members. `owner.id` is the single identity source: den-core derives the
directory's `owner`, its one map key, the entry `id`, and `migration.ownerProfile` from it, so those bindings cannot
disagree. It copies `display` and `policy` completely, including accepted unknown members inside their stamped
values; constructs `library` as key version 1 from `libraryKey` and `libraryAt`; and constructs `deleted` as false
at `deletedAt`. The logical directory fixes `format` 4, kind `profiles`, schema 1, role `owner`, and migration
version 1. The result is an exact object with three members:

```json
{
  "directory": {"format": 4, "kind": "profiles", "schema": 1, "...": "the validated stored directory"},
  "jcs": "<unpadded base64url of the directory's RFC 8785 UTF-8 bytes>",
  "plaintext": "<unpadded base64url of 0x00 followed by raw-DEFLATE of those JCS bytes>"
}
```

`jcs` is the canonical source for the logical result. `profiles` is a library-v4 document, so `plaintext` uses that
format's document framing: byte `0x00` followed by one complete raw-DEFLATE stream which inflates to exactly `jcs`,
with no trailing bytes. DEFLATE bitstreams are not canonical, so conformance compares their exact inflated JCS,
not compressor-specific bytes. The constructor accepts the complete finite JSON-number domain in preserved display
and policy extensions and serializes every number exactly as RFC 8785 requires (ECMAScript number serialization,
including `1.0` as `1` and negative zero as `0`); non-finite values are not JSON and are rejected. The caller must
decode and seal `plaintext`; it must not independently serialize `directory`.

The constructor then validates its result as a **stored** directory, including mandatory migration evidence. A
malformed id, display, policy, stamp, canonical 32-byte key, attempt, source generation/head, backup id/digest or
Owner head is an error; it never returns a partially valid document or caller-selected bytes. The caller seals the
returned `plaintext` and publishes it through the ordinary authenticated household write path. Constructing it alone
does not raise a wire minimum.

### 6.2 Seeding a profile preferences from an older household

Each persistent profile, Owner and Kid alike, keeps its own preferences in its own `set:prefs`; a profile never reads
from or writes to another's, and a client writes a preference change to the active profile's `set:prefs` only.
Visitor has no library; its preferences are device-local. A household that became profile-aware before this rule
still holds its viewing preferences in its own `set:prefs`, and its profiles hold none (or only some).

`profiles_seed_preferences({household, profile})` answers the values a profile library must be given. `household` and
`profile` are `set:prefs.values` objects (`profile` may be `null` for a profile with no `set:prefs` row); anything
else is `invalid_preferences`. The answer is `{values}`: every member of `household` except `den.maturityCeiling`
whose key the profile's `values` lacks, each complete stamped value kept byte-for-byte, and `{}` when there is
none. Any member already in the profile counts as a decision, including a cleared one, and is never overwritten.

- **Idempotence.** A seeded value keeps the household's stamp, so devices that seed the same profile concurrently
  write identical stamped values, and a later edit in the profile (a later stamp) wins by the ordinary stamp rule.
  A client seeds a profile when it opens that profile's library, and writes only a non-empty answer.
- **Household copy.** After seeding, the household's `set:prefs` is a stale seed that no profile reads for a key it
  holds. It is removed by the §8 repair (`set:prefs` is `remove`), which a client runs only once every live
  profile has been seeded, because a profile created later is seeded from it too.

## 7. Sticky device-local selection

Selection is local, never a library row. Its JSON shape is `profile-selection-v1` in the schema and vectors:

```json
{"version": 1, "mode": "persistent", "profileId": "00112233445566778899aabbccddeeff"}
```

or `{"version": 1, "mode": "visitor", "visitorNamespace": "<32 lowercase hex>"}`. The namespace is random and
local. Owner and Kid remember the profile id; Visitor remembers only its mode and namespace. A valid selection
opens directly across process death, TV sleep/reboot and browser reload. There is no launch picker, duration or
expiry.

- A missing/corrupt marker, unknown/deleted profile id, or marker whose role is unsupported opens the picker. If
  any live profile is restricted (`maximumAge` non-null or `unknownRatings: block`), Owner and management remain behind
  the owner PIN; the client MUST NOT silently choose Owner.
- Owner → Kid or Visitor and Visitor → Kid need no PIN. Kid → Owner and Visitor → Owner require the owner PIN.
  Kid → Visitor requires it whenever Owner's inherited Visitor policy is looser than the Kid policy (§5).
- Switching away from Visitor deletes the complete local namespace before the new mode can record activity.
  Switching away from a persistent profile cancels its tasks and closes it but never deletes its library.
- Playback is dismissed, profile-owned delayed progress/tracker/recommendation work is cancelled, navigation is
  reset, and profile-specific caches are cleared before another mode becomes active. Household services stay open.

## 8. Adoption and household filtering

A generation change that brings in profile topology is **adoption**, never a restore. It is any generation change
after which the household's wire minimum is 5 where the client's held state had a lower minimum, or after which the
household carries a valid `profiles` directory that the held state lacked. A topology-establishing rewrite is a
library-v4 §11 generation change, and a device that did not perform it sees only the new generation.

On adoption a client MUST, for that household:

- **discard** every held row, document and kept write that the §2 rule bars from a profile-aware household, together
  with any cached snapshot of the pre-profile household and any pending restore or write-back work built from it. Held
  work is dropped, not deferred, and is not reported as a conflict;
- **not write back** any of it. library-v4 §11's write-back applies only to a generation change that does not add
  profile topology, and even then every row passes the §2 rule first;
- re-read the household from 0 and derive state from the committed rows alone. The earlier Owner activity now
  lives in the Owner library; a device cache of it is neither merged into the household nor into Owner.

The same rule holds after adoption: a client that later holds a pre-profile cache (a browser profile restored from a
backup, a device offline across the commit) filters it through `profile_household_write` before any write.

`profile_household_write(row)` answers whether a row may be written to a profile-aware household. `row` is exactly
`{name}` for any row except `set:prefs` and `set:keys`, which also carry `values` (the settings object of the row). The
answer is `{effect}`:

- `allow` — write it as it is;
- `refuse` — never write it; for a `set:prefs` or `set:keys` row, none of its values may be written;
- `restrict` — write the row with exactly the returned `values`: for `set:keys`, every value except `simkl`. Stamps
  and unknown members of the retained values are unchanged. A row from which nothing is withheld is `allow`; one from
  which every value is withheld is `refuse`. A `set:prefs` row is always `refuse`, whatever it holds.

A `set:prefs` or `set:keys` row without a `values` object is `invalid_household_write`, as is an unknown shape.

`profile_household_audit({rows})` applies the same rule to rows already stored in a household, for a one-off repair.
`rows` is a list of `row` values as above (a name may appear once; a repeat is `invalid_household_write`). The answer is
`{remove: [name…], rewrite: [{name, values}…]}`, in input order: `remove` lists the rows that are `refuse`, `rewrite`
lists the `restrict` rows with their household-only values, and an `allow` row appears in neither. A repair deletes
or rewrites by the library-v4 rules (a tombstone for a removed row) and does not touch the Owner library.

## 9. Multi-profile key reset and revocation

A household §12 reset is one fenced compound move. It generates a successor household key and one distinct random
successor key for every live persistent profile, increments each profile's `keyVersion` exactly once, and re-seals
each complete ordinary library-v4 log while preserving delivery state. Deleted profiles are not moved. No successor
profile key may equal any old or other successor key. The whole move runs under one durable exclusive lock per
library, the household and every live persistent profile, acquired before the first copy and defined in §9.1:
without that set a device could write an old library after it was copied, or a second reset could race this one.

`profiles_reset_next(input)` is den-core's canonical pure planner. It receives the validated committed directory,
the household move fence, and for each live profile the old key/version, proposed new key/version, copy head and
verification state. Its phases are `prepared`, `profiles-moving`, `ready-to-publish`, `committed`, `retiring-old`
and `complete`. It returns the next profiles to copy/verify or the one permitted commit/retirement effect.

The input MUST contain exactly one evidence entry for every live directory profile, identified by profile id, and
none for a deleted or unknown profile. Each entry's old key and version MUST equal that profile's current directory
field. Its successor key is a canonical padded base64 encoding of 32 bytes, is distinct from every old and proposed
key, and its version is exactly the old version plus one. A verified move has a 32-byte lowercase-hex `copiedHead`;
an unverified move has `copiedHead: null`. The fence is `{generation, head}` with a safe nonnegative generation and
a 32-byte lowercase-hex head. Omitting a live profile, duplicating an entry, relabelling another profile's old key,
or supplying malformed evidence is an error, never permission to publish.

The answer is an object with `effect`. `move-live-profiles` additionally carries the sorted profile ids still to
copy or verify. `retire-old-libraries` carries the sorted profile ids whose old libraries remain live and a
`household` boolean indicating whether the old household remains live. The other effects are
`commit-successor-household` and `complete`. Errors use the operation's error envelope rather than an `ok` value.
Evidence claiming that an old library retired before the successor household committed, or that the successor
committed before every copy verified, is contradictory and returns an error; it is never normalized into a next
effect.

The successor directory is not published until every live profile move is verified. The household move then
publishes that directory under the successor household, records the §9.1 successor commit on the household lock,
and only afterward retires the old household and every old profile library. A conflict or unknown outcome is
resolved by looking up the household lock (§9.1) and reading the committed household move before any retirement.
The planner's `fence` is the household source observation that the household lock was acquired at; the lock set
adds no planner input, phase or effect (§9.1 *Planner boundary*). Missing verification, a non-incremented version, reused key, unknown/newer
row that library-v4 §12 cannot move, or any unavailable profile returns an error and leaves all old libraries live.
Revocation is complete only when every old profile library and the old household are confirmed retired; leaving one
old profile key usable is not a reset.

### 9.1 Exclusive locks for a compound reset

A compound reset is not conforming if it is assembled from ordinary reads, ordinary writes and an ordinary `DELETE`
(library-v4 §12): a device could write an old library after it was copied and then lose the write to retirement, two
resets could mint different successors for one household. The reset therefore holds §1.2's **per-library durable
exclusive-operation lock**, with `purpose: "key-reset"`, on the household and on every live persistent profile library. The store primitive is the
generic one (compare-and-set at an exact source, same-attempt replay, metadata-free refusal of everyone else,
atomic release with the terminal effect, atomic handoff) and needs no knowledge of profiles beyond the lock record
below.

This section names the required operations. The routes are the interoperable HTTP surface; an in-process store may
expose equivalent calls but MUST return the same evidence and errors. Every request is authenticated as the library
it names (`x-den-library-token`) and carries exactly the `x-den-wire` that library requires: 5 for the household, 4
for a profile library (§1). The surface follows the §1.1 runtime wire gate.

| Route | Result |
|---|---|
| `GET /lib/{library}/exclusive-operation/source` | `200 {"source":{"generation","head"}}`, read-only |
| `POST /lib/{library}/exclusive-operation` | acquire (household or profile) or open a recovery; `200` open body, or a replay of the stored answer |
| `GET /lib/{library}/exclusive-operation/{attempt}` | the stored answer, or a durable terminal result; `404` for an unknown attempt |
| `POST /lib/{household}/exclusive-operation/{attempt}/successor` | the one successor-household commit |
| `DELETE /lib/{household}/exclusive-operation/{attempt}` | abort the whole reset, before the successor commit only; household record only |
| `DELETE /lib/{library}` with `x-den-exclusive-attempt` and `x-den-successor` | retire a locked library and release its lock in one commit |

#### Authority and per-library commits

den-edge stores every library in its own database, so **no storage commit spans two libraries, and this contract
never requires one.** Every atomic step below touches exactly one library, and the **household's lock record is the
single authority** over the reset. It holds the reset's phase: `open` → `committed` → `retiring` → `retired`, or
`open`/`committed` → `aborted` (an abort and a rollback are the same terminal phase), plus the current owner, the
`proven` flag of a recovery owner and the declaration of every member and successor. A member lock is a subordinate
record that is **in force** only while its household record is non-terminal (`open`, `committed` or `retiring`)
and the member itself is not retired.

The complete list of commits, each in one library:

| Commit | Library | Effect |
|---|---|---|
| acquire | household | records the lock, the declaration and phase `open` |
| acquire | one member | records the member lock (only while the household is `open` and owned by the attempt) |
| successor commit | household | `open` → `committed` |
| abort | household | `open` → `aborted` |
| takeover | household | replaces the owner, sets `proven`; or, proofless on `committed`, `committed` → `aborted` (rollback) |
| retirement door | household | `committed` → `retiring`, taken before the first member is retired |
| retire | one member | retires the library and records its lock `retired` |
| retire | household | `retiring` → `retired` with the library retired, after every member |
| mark swept | household | records that every declared successor was deleted |
| delete successor | one successor library | deletes it (idempotent) |
| void-lock cleanup | one member | clears a member lock whose household is `aborted` (idempotent, optional) |

Member effects (lock release after an abort or rollback, retirement, successor deletion) are always separate,
idempotent commits **performed after the household decision**. They are driven by the owner, a recovery owner or
den-edge itself, in any number of repetitions, until done. A member checks its household record on every request
that depends on its lock (den-edge reads the household's database in the same process; a read is not a commit).

*Void locks.* A member lock whose household record says `aborted` is treated as released at once, before any
cleanup: ordinary writes are allowed, a new reset may lock the library, and den-edge clears the record on next
access or by a sweep. A member lock whose household says `committed` or `retiring` is released only by the member's
retirement. A household record that is `retired` implies every member retired. This makes the household commit the
single linearization point of abort and rollback: the writes refused before it are retried by clients, and no write
is accepted under a lock that is not in force.

#### Source observation and the lock set

`GET /lib/{library}/exclusive-operation/source` is §1.2's source observation for any library: `generation` is the
library's source counter and `head` is the source digest computed with that library's own id and stored `wireMin` (4
for a profile library). den-edge MUST keep the counter for every library, not only households, advancing it
atomically on each visible mutation as §1.2 describes. The household observation is the planner's `fence`. A client
authenticates each observation before it acquires at it: it opens every known row, checks every AEAD and
logical-name binding, preserves every unknown row and recomputes the digest.

The lock set is the household plus one lock for every live directory profile, no more and no fewer. A client
acquires in a deterministic order: the **household first**, then the profile libraries in ascending order of library
id (raw bytes of the 32-hex id). The household lock carries the complete declaration of the set, so its attempt id
binds every other lock and the server always knows what the set is. A profile lock is refused unless the household
lock of the same attempt is open and declares that profile library. Retirement and recovery act only on the declared
set, so a profile cannot be orphaned by omission: a live profile missing from the declaration would have been
missing from the planner's evidence, which §9 already treats as an error.

#### Acquire

The household request is:

```json
{
  "attempt": "0123456789abcdef0123456789abcdef",
  "purpose": "key-reset",
  "role": "household",
  "expectedSource": {"generation": 14, "head": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},
  "successor": "c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0",
  "members": [
    {"libraryId": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "successor": "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1"},
    {"libraryId": "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2", "successor": "e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2"}
  ]
}
```

`attempt` is the planner journal's 32-hex attempt id. `successor` and each member `successor` are the library ids
derived from the proposed successor keys (ids reveal nothing about a key). `members` is sorted ascending by
`libraryId`, is non-empty, and the household id, every member id and every successor id are pairwise distinct;
anything else is `400 {"error":"bad_request"}` with no effect. A profile request is:

```json
{
  "attempt": "0123456789abcdef0123456789abcdef",
  "purpose": "key-reset",
  "role": "profile",
  "expectedSource": {"generation": 3, "head": "1111111111111111111111111111111111111111111111111111111111111111"},
  "successor": "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1",
  "household": "5e1ab5db12f9b30e640e3ee80fb3e072"
}
```

den-edge evaluates a request in exactly this order, holding the serialization of every library it reads or writes,
taken in ascending library-id order so household and profile requests cannot deadlock:

1. **Lookup first.** The same attempt, purpose, role, `expectedSource`, `successor` and `members`/`household` return
   the stored answer even if the library has since changed. The same attempt with any other value is
   `409 migration_attempt_mismatch` with no effect.
2. A retired library is `410 library_moved`.
3. **Any other lock in force on the library** (a void member lock is not in force), of any purpose or attempt, a profile request whose household lock is not
   open, not of this attempt, not owned by it, or does not declare this library with this `successor`: exactly
   `409 {"error":"migration_in_progress"}`, no attempt id,
   purpose, source, role or timing metadata, and no side effect. This check precedes the source comparison so an
   ineligible caller cannot probe the lock with mismatched sources.
4. A source that is not the library's current `{generation, head}` is `409 {"error":"migration_source_changed"}` with
   no side effect.
5. Otherwise one storage commit records the lock and answers:

```json
{
  "attempt": "0123456789abcdef0123456789abcdef",
  "state": "open",
  "purpose": "key-reset",
  "role": "household",
  "library": "5e1ab5db12f9b30e640e3ee80fb3e072",
  "wireMin": 5,
  "lock": "lock-8f2c61d0a94b37e5c1d2e3f405162738",
  "fence": {"generation": 14, "base": 418, "head": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},
  "successor": "c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0",
  "members": [{"libraryId": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "successor": "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1"}]
}
```

A profile answer has `"role": "profile"`, `household` in place of `members`, and the same `lock`, `fence` and
`successor`. `fence` is `{generation, base, head}` frozen by the lock; `fence.generation` and `fence.head` equal
`expectedSource`. `lock` is an opaque durable handle of at least 128 random bits, unique per lock and returned by
every lookup: it names the lock for a journal, but it carries no authority. Authority is the attempt id together with
the library token. Because a profile lock is only granted after the household lock, a competitor that holds some
other library's token learns nothing about this reset.

The household acquire commit also records which declared successor ids **already existed** as libraries at that
moment (`preexisting`, a subset of the declared `successor` ids, fixed for the life of the record). A successor
that already exists was not created by this reset; the sweep (*Abort*) never deletes it, so a household cannot make
den-edge delete another library by naming it as a successor. The recorded set is internal and is not returned.

Acquisition does not advance any counter. A same-attempt replay is the only request allowed through an active lock.
A client whose profile acquire answers `migration_source_changed` re-observes and re-authenticates that profile
source and retries; the failed attempt had no effect, and the household lock keeps every other library stable. A
client that cannot acquire the whole set (a bounded number of source races, `migration_in_progress`, a 5xx it cannot
resolve by replay) aborts what it holds (*Abort*) before telling the person to try again. Acquisition happens before
den-core is consulted for any copy.

#### What a lock does

While a library holds a key-reset lock **in force**, every mutation that is not an effect of the owning reset is refused
before ordinary generation and rewrite handling with exactly `409 {"error":"migration_in_progress"}` and no effect:
`/batch`, member registration, an ordinary `DELETE /lib/{id}`, and rewrite open/rows/commit/abort on
the household. Reads continue, so playback and inspection work from the frozen old libraries and the executor copies
from them. The one error code is deliberate: deployed clients already map `migration_in_progress` to a blocking
setup-in-progress state and pause write retries. The lock and its record are durable across client and
den-edge restart and MUST NOT disappear on an idle timer or a client disconnect; it ends only through the
terminal effects below. The successor libraries are not locked: they are new ids, created by ordinary batches that
name an old library of the same store as `x-den-library-member` (library-v3 §10). Reading an old library to prove
that membership is not a mutation of it.

#### Successor commit

The household move commits exactly once, on the household lock. After the client has written the complete successor
household under the successor key, read it back and verified it against the successor directory it intends, it sends
`POST /lib/{household}/exclusive-operation/{attempt}/successor` with `{"successor":"<successor household id>"}`.
Under the serialization of the household, with read-only checks of the successor library and every member (member
locks leave `open` only after the household decides, so these reads cannot go stale before the household commit),
den-edge verifies that:

- the household lock is open, of this attempt and owned by it (not taken over, *Recovery*);
- `successor` equals the id declared at acquisition;
- every declared member holds an open lock of this attempt, else `409 {"error":"locks_incomplete"}`; and
- the successor library exists, is not retired, has stored minimum at least 5 and holds at least one row, else
  `409 {"error":"successor_not_ready"}`.

It then moves the household lock to `state: "committed"` in one commit on the household and answers the lookup body with that state. The server
cannot decrypt anything, so the completeness and correctness of the successor remain the client's and den-core's
obligation; the commit is the point at which they are final. **This is the only definition of "the successor
household committed".** A successor household library that exists but has no recorded commit is not committed: the
old libraries remain authoritative, and an abort remains allowed. A replay with the same body is the stored answer.
Any other caller gets `409 migration_in_progress`; a different `successor` is `409 migration_attempt_mismatch`.

#### Retirement

Once the household lock is `committed` (or `retiring`), each old library is retired by the §12 `DELETE /lib/{library}` with the
existing token and `x-den-generation`, plus two headers:

- `x-den-exclusive-attempt: <attempt>`, the owning attempt (the reset attempt, or the recovery attempt that took the
  household lock over); a present but malformed value (not 32 lowercase hex) is `400 {"error":"bad_request"}`
  with no effect, as is a malformed `{attempt}` in any §9.1 path; and
- `x-den-successor: <successor library id>`, the id the lock declared for this library.

The request MUST also carry `x-den-generation` as every library-v4 write does; a missing or different value is
`409 generation_changed` (condition 6).

On a library with a key-reset lock den-edge requires the owner and the declared successor, then retires the library
(subsequent requests get `410 library_moved`, as library-v4 §12), records the lock's terminal `retired` result
naming that successor and releases the lock, all in one storage commit **on that library**. There is no state in
which the library is retired but its lock is held, or released but not retired.

**The retirement door.** Retirement is irreversible, and rollback (*Recovery*) is decided on the household record
alone, so the two must not race across libraries. The first retirement of a reset therefore first commits the
household `committed` → `retiring` (a commit on the household only, idempotent when it is already `retiring`) and
only then retires the member in a second commit on the member. Rollback is possible only from `committed`, so once
the door is crossed it can never happen, and a retirement that arrives after a rollback finds the household
`aborted`, whose member locks are void, and is refused. A crash between the door and the member retirement leaves
the household `retiring` with nothing retired: the owner or a proven recovery repeats the request; a proofless
recovery is refused (fail-closed, the old libraries stay readable). The conditions, in order:

1. A library already retired by this lock: `200 {"deleted":true}` when the presented attempt is the lock's owner
   and the presented successor equals the recorded one, otherwise `410 library_moved`. For a member the owner is
   decided by the household lock's **current** owner: after a proven takeover the recovery owner may repeat (and look
   up) a retirement the original owner already made, and still gets `200` and `retired`; the original owner keeps
   that right for its own retirements; an unrelated attempt gets `410`. This is the idempotent replay a lost
   response needs. The `410` body of a library retired by a key-reset lock is exactly `{"error":"library_moved"}`
   and never names the successor; only a legacy library-v4 §12 retirement marker may still carry one.
2. No lock in force (no record, or a void member lock): `409 {"error":"lock_not_held"}` when the headers are
   present. A `DELETE` of a locked library with
   **no** attempt header, or an attempt that is not the owner, is `409 migration_in_progress` with no effect;
   retiring a locked library under a different or missing attempt is refused.
3. A successor that differs from the declared one: `409 {"error":"successor_mismatch"}`.
4. The household lock is not `committed` or `retiring`: `409 {"error":"successor_not_committed"}`. Nothing retires
   before the commit, for any library.
5. For the household only: the household is `retiring` and every declared member lock is `retired`, else
   `409 {"error":"locks_held"}` (the member retirements are read-only checks; retirement is irreversible, so they
   cannot go stale). The **household is retired last**, because the old household is the only place the old directory, and with it every old
   profile key, can be read. Retiring it earlier could strand a profile a recovery could no longer name.
6. `x-den-generation` mismatch is `409 generation_changed` as library-v3 §10, with no effect. Under a lock the
   generation cannot change; a mismatch is a storage failure, so the client stops and surfaces it rather than
   re-copying (library-v4 §12's re-copy rule applies to an unlocked move).

An ordinary `DELETE` of an unlocked library is unchanged.

#### Abort

Before the successor commit nothing irreversible has happened: the old libraries were never altered and any copies
exist only under keys the journal holds. The owner may therefore release the set, but never inferred from idleness.
`DELETE /lib/{household}/exclusive-operation/{attempt}` names the presenting attempt and is **one commit on the
household only**: it succeeds only when that attempt owns the household lock and the phase is `open`, and moves it
to the terminal `aborted`. At that commit every member lock becomes void (*Authority and per-library commits*), so
the whole set is released at once, with no ordering between libraries to get wrong and no member state to wait for.
Against a lock of another owner, or after the successor commit, it is `409 migration_in_progress` and changes
nothing: from then on the only release is retirement (or the proofless rollback of a `committed` reset, in
*Recovery*). A replay of an aborted attempt returns the `aborted` result. The route is not defined on a profile
library: a client has nothing to release there, and its lookup reports `aborted` once the household is.

*Successor deletion.* After the household decision (abort or rollback) the declared successor libraries (the
household's and every member's, all listed in the household record) are deleted by separate idempotent commits, one
per successor library; a successor that was never created is skipped, and so is one recorded as `preexisting` at
acquire (*Acquire*): the sweep deletes only libraries this reset can have created. The driver is **den-edge
itself**, which on startup, and on the next access to an `aborted` household record, deletes every other declared
successor that exists and then commits "swept" on the household; a client has nothing to repeat. A crash between the decision and a deletion therefore leaves at most an
unreferenced successor that the next driver deletes, and the guarantee that no storage-counting orphan remains is
eventual, owned by den-edge. Deleting is safe because an `aborted` household was never retired: no device can have
adopted a successor (*Recovery*).

Abort is a client decision, not a planner effect. A client may abort only while the journal is in the planner's
`prepared`, `profiles-moving` or `ready-to-publish` phases, and discards the journal afterwards; a retry generates
entirely new keys.

#### Lookup and lost responses

`GET /lib/{library}/exclusive-operation/{attempt}` returns, with the library token, the stored open body
byte-for-byte for `open` (and for the household `committed`, with `"state":"committed"`; the household phase
`retiring` is rendered as `committed`, because committed is monotone and the door is not client-visible), or a
durable terminal:

```json
{"attempt": "0123456789abcdef0123456789abcdef", "state": "retired", "library": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "successor": "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1"}
{"attempt": "0123456789abcdef0123456789abcdef", "state": "aborted", "library": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"}
```

Terminal results are durable tombstones and lookup works for a retired library with the owning attempt; an unknown
attempt is `404` with no metadata. A member whose household is `aborted` answers `aborted` (computed from the
household record, even before its own record is cleaned up). A client resolves every lost or unknown response by lookup-first replay, never by
guessing:

| Lost response | Resolution |
|---|---|
| acquire (either role) | repeat the identical `POST`; it returns the stored answer |
| successor commit | lookup the household lock; `committed` is success, `open` repeats the identical `POST` |
| retire | repeat the identical `DELETE` (idempotent), or lookup for `retired` |
| abort | repeat the identical `DELETE`, or lookup for `aborted` |
| recovery open or takeover | repeat the identical `POST`. The same recovery attempt's repeat is looked up **before** any decision, so after a rollback it returns the stored `{"attempt","state":"completed","outcome":"aborted"}` and after a takeover the stored open body, never `409` |

The `observeSuccessorHousehold` step of a reset executor reports `committed` only when the household lookup says
`committed`; a successor library that exists without it is verified against the intended directory and then
committed as above, or reported unknown.

#### Journal-less Owner-PIN recovery

A device that lost its reset journal (or a different device) cannot resume, and a lock has no expiry, so §9.1
defines recovery. It is offered only to a client with no matching local journal, after a **fresh Owner PIN**
authorization bound to the recovery attempt and the exact source it observed; a client with the journal resumes
instead. The recovery attempt is a new random 32-hex id `R`. The client reads `GET /lib/{household}/exclusive-operation/source`,
authenticates it, and sends

```json
{"attempt": "fedcba9876543210fedcba9876543210", "purpose": "key-reset-recovery", "role": "household",
 "expectedSource": {"generation": 14, "head": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}}
```

to `POST /lib/{household}/exclusive-operation`, optionally with `x-den-successor-token` (the successor household
library's token, proving possession of the successor key; see *`committed` with proof* below). It is a **takeover**
of the active reset lock and succeeds only when all of these hold at once under the household serialization:

- the household holds a key-reset lock in phase `open`, `committed` or `retiring` (a reset or an earlier
  recovery's), and no other purpose holds it;
- the request's `expectedSource`, the lock's frozen source and the library's current authoritative source are
  identical;
- `R` is not already the lock's attempt with another purpose or source (that is `migration_attempt_mismatch`);
- a present `x-den-successor-token` matches the declared successor library (a wrong proof is refused in any phase);
  and
- in phase `committed` without a proof, the rollback applies; in phase `retiring` a proof is required.

Any other request is exactly `409 migration_in_progress` with no metadata, a recovery of a profile library included.
On success (except the proofless rollback of a committed reset, below) one storage commit replaces the lock's owner with `R`, tombstones the previous owner attempt as
`recovered` by `R`, and answers:

```json
{
  "attempt": "fedcba9876543210fedcba9876543210",
  "state": "open",
  "purpose": "key-reset-recovery",
  "role": "household",
  "library": "5e1ab5db12f9b30e640e3ee80fb3e072",
  "wireMin": 5,
  "lock": "lock-8f2c61d0a94b37e5c1d2e3f405162738",
  "fence": {"generation": 14, "base": 418, "head": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},
  "recovers": {
    "attempt": "0123456789abcdef0123456789abcdef",
    "phase": "open",
    "successor": "c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0",
    "members": [{"libraryId": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "successor": "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1"}]
  }
}
```

The lock handle is unchanged. The household lock is never released by the takeover, so no ordinary write can slip in
between it and the finish. After it the previous owner is shut out atomically: its successor commit, its retirements
and its profile acquires get `409 migration_in_progress`, because owner authority on every member lock follows the
household lock's current owner. Its lookup answers
`{"attempt","state":"recovered","recovery":"<R>"}`, and gains `"outcome":"aborted"|"retired"` once `R` completes.
A journaled executor that finds `recovered` stops: `outcome: "aborted"` means discard the journal, `outcome:
"retired"` means the old libraries are retired and it adopts its successor (it holds the keys). Without an `outcome`
it waits. A second recovery `R2` may take `R` over by the same rule; recovery is therefore itself crash-recoverable.

The finish is decided only by `recovers.phase`, which is the lock's phase at takeover, and whether a proof was
accepted, never by anything the client infers (the rollback below completes inside the takeover itself, so its
answer is the terminal one and carries no `recovers.phase` to act on):

- **`open` (before the successor commit): abort.** `R` sends one `DELETE /lib/{household}/exclusive-operation/{R}`,
  a single commit on the household (*Abort*). Every member lock is void at that commit, the old libraries are intact
  and writable, and den-edge deletes the declared successors afterwards. The result is `state:"completed"`,
  `outcome:"aborted"` for `R`. A successor household that exists without a recorded commit is not committed
  (see *Successor commit*) and is among the deleted successors; the commit by the old owner is refused for good
  by its `recovered` tombstone. A proof (below) is not needed.
- **`committed` or `retiring` with proof of the successor key: finish retirement.** The request carries the header
  `x-den-successor-token`, the successor household library's token. den-edge compares its hash with the stored
  credential of the declared successor library (which must exist and not be retired); a match is the proof that
  the caller holds the successor household key. The takeover records `proven: true`, and only then may `R` retire:
  every declared member with `x-den-exclusive-attempt: R` and `x-den-successor` taken from `recovers.members`,
  never chosen by the client, and only then the household with `recovers.successor`. The result is
  `state:"completed"`, `outcome:"retired"`. A proof also takes over a `retiring` lock (the door was crossed, possibly with members already retired). A lock owned
  by a recovery that did not prove possession refuses every retirement (`409 migration_in_progress`).
- **`committed` without proof: roll back, only before the retirement door.** Possession of the successor key
  cannot be shown, and finishing retirement could strand the household with a successor nobody can open. Nobody can
  have adopted that successor yet, because executors adopt only after every old library is retired. If the
  household phase is exactly `committed` (the door of *Retirement* has not been crossed, hence nothing is
  retired), the takeover is itself the rollback, **one commit on the household**: phase `committed` → `aborted`,
  the previous owner tombstoned as `recovered` with `outcome: "aborted"`, and `R` answered as
  `{"attempt","state":"completed","outcome":"aborted"}`. That one commit voids every member lock. The member
  clean-up and the deletion of the declared successors follow as separate idempotent commits driven by den-edge
  (*Abort*). Whenever the phase is `retiring` (or `retired`) the request is exactly `409 migration_in_progress`
  with no metadata and no effect: the lock stays, the old libraries stay readable and every write stays refused
  until a holder of the successor key finishes retirement with proof.
- **A wrong proof** (the header present but not the successor's credential, or the successor library absent or
  retired) is exactly `409 migration_in_progress` in every phase, with no effect.

Why this is safe. Recovery can release a lock only through a state change the owner could have made, or by the
rollback above, and it cannot do either unsafe thing:

- *It cannot release a lock out from under a live successor.* A successor is live only once it can be adopted, and
  adoption follows the retirement of every old library. Commit, door, abort, rollback and takeover are all commits
  on the one household record, so they serialize: whichever commits first wins and the loser is refused. Abort needs
  `open`, where no successor is committed. Rollback needs exactly `committed` and no proof; the door makes the first
  retirement a household commit that precedes any member retirement, so a rollback and a retirement can never both
  take effect. A caller that can prove the successor key takes the retirement path, so a successor is never deleted
  under a holder of its key who has proof. A journaled executor that was merely slow sees `recovered` with
  `outcome: "aborted"`, discards its journal and starts again; it had not adopted anything.
- *It cannot orphan a profile or lose a write.* The set is declared in the household record, and every member lock
  is subordinate to it: it is void exactly when the household is `aborted`, and released only by its own retirement
  otherwise. A member write is refused before the household decides and allowed after an abort; it is never
  accepted under a lock still in force, and a void lock is never enforced because every check reads the household
  record.
- *It cannot strand the household.* Old libraries are retired only by a caller that proved the successor key, or
  by the original owner, whose journal holds it. Without proof the only outcomes are a full rollback or a refusal
  that leaves the old libraries readable.

Retiring is required for a reset to be complete: leaving old keys usable is not a reset (§9). A reset left
partially retired with no key holder reachable stays locked, fails closed and keeps the old libraries readable.

#### Crash boundaries

| Crash | Durable state | Next |
|---|---|---|
| before the household acquire commit | no lock | repeat or start again |
| after it | household `open`, set declared | acquire the rest in order, or abort |
| between profile acquires | household and some members locked | replay acquires (lookup-first), or abort |
| before the successor commit | `open`; successor may exist, uncommitted | lookup, verify, commit; or abort |
| after it | household `committed` | lookup shows `committed`; cross the door, retire members, household last |
| after the household abort or rollback, before any member or successor effect | household `aborted`; member locks void by rule; successors may exist | writes already allowed; den-edge clears member records and deletes the declared successors, then marks the household swept |
| between successor deletions | some declared successors gone | the next sweep deletes the rest (idempotent); no live successor can exist because the household was never retired |
| after the door, before the first member retirement | household `retiring`, nothing retired | owner or proven recovery repeats; proofless recovery is refused; old libraries stay readable and locked |
| between member retirements | each retirement is one commit on one member | repeat the `DELETE`s; household last |
| after the last member, before the household retirement | household `retiring`, members `retired` | repeat the household `DELETE`; the old directory is still readable |
| takeover | owner replaced in one commit on the household | `R` finishes by `recovers.phase` and proof; `R2` may take over |
| proofless takeover of a `committed` reset | the rollback is one commit on the household | `R` is `completed`/`aborted`; member locks void; den-edge finishes clean-up and successor deletion |
| a member acquire racing a takeover or abort | the member record names a non-owner or a terminal household | void by rule, or owned by the new owner; harmless |

Every row converges with no lost write (a write is refused only while a lock is in force and is allowed the instant
the household is `aborted`), no live-successor release (abort, rollback and the door are mutually exclusive
commits on the household, and a household that was ever `retiring` can no longer be rolled back) and no profile
orphan (the declaration is in the household record, the household is retired last, and a void or retired member is
decided by that record). No intermediate state exposes a library retired with its lock held, a lock released
without its terminal result, a retirement before the commit, or a household retirement before every member.

#### Planner boundary

`profiles_reset_next` needs **no change**: its inputs, phases (`prepared`, `profiles-moving`, `ready-to-publish`,
`committed`, `retiring-old`, `complete`), effects and error envelope are exactly §9's, and
`resetCases` in the vectors are unchanged. The lock set is acquired before the first planner call and is not planner
evidence. `householdCommitted` is set only after the household lock lookup says `committed` and the client has
verified the successor against the intended directory; `oldRetired` and `oldHouseholdRetired` only after the
corresponding retirement answered `200` or a lookup says `retired`. Abort and recovery have no planner effect: abort
is a lock release available only before `householdCommitted`, and recovery runs without a journal and therefore
without planner evidence, using only the server-recorded phase and declaration above.

## 10. Vectors and operation boundary

[`vectors/profiles-v1.json`](../vectors/profiles-v1.json) pins directory schema and operations, numeric policy,
approvals, the exact settings table, sticky selection, snapshot digests, initial directory construction, wire
topology, multi-profile reset and its exclusive-lock contract (`resetLockContract`, §9.1), and the profile-aware
household write rule (`householdWriteCases`, `householdAuditCases`, §8). `python3 tools/validate_profiles.py` checks
the canonical input boundary and expected pure answers.
