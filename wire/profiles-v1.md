# Household profiles, v1 — proposal

**Status: contract only; not activated.** This document is the backward-compatible first part of
[den-spec#39](https://github.com/oxyc/den-spec/issues/39) and the architecture in
[den#234](https://github.com/oxyc/den/issues/234). It defines the data model clients and den-core will implement.
It does **not** authorize a client to split an existing library, publish a profile directory, raise a production
wire minimum, or make an existing wire-4 household refuse a wire-4 client. Those state machines require a later
revision with backup, fencing, adoption, cleanup and reset vectors.

Words: *MUST* is a rule a client breaks at the cost of another client or profile; *should* is advice.

## 1. Topology and wire version

A profile-aware installation has this topology:

```text
household library (configuration only; profile-aware requests use wire 5)
├── household settings and download rows
└── profiles document
    ├── Owner  ── random key ──> ordinary library-v4 activity log
    ├── Kid A  ── random key ──> ordinary library-v4 activity log
    └── Kid B  ── random key ──> ordinary library-v4 activity log

Visitor ──> device-local namespace; no library and no directory entry
```

**Wire 5 is library-v4 row framing, sealing, settings and document merge rules plus the household/profile topology
in this document.** It is not a fifth row encoding. The household's rows use library-v4 §4 plaintext framing and
library-v2 sealing. A persistent profile's log is an ordinary library-v4 library: requests to that log advertise
`x-den-wire: 4`, and a new profile log starts with `x-den-wire-min: 4`. Profile-aware requests to the household
advertise `x-den-wire: 5` once profiles have been enabled by the later split protocol.

Merely understanding this document, constructing a directory in memory, or reading the vectors MUST NOT raise a
household's `x-den-wire-min`. In particular, this revision has no operation that commits the initial split. The
eventual committed split will raise the existing household minimum to 5; until that operation is specified and
implemented, its minimum stays unchanged.

The household key seals the directory, including all profile keys. Profiles are UX and data-isolation boundaries,
not protection from a hostile device that holds the household key. Selectively wrapped private profiles are out of
scope.

## 2. What each store may hold

The **household library** is configuration only. A profile-aware client MUST NOT derive or write title activity,
season activity, reactions, resume state, personal recommendations, tracker commands, tracker receipts, tracker
delivery documents, or tracker credentials there. It may hold:

- the `profiles` document (§3);
- the household-scoped settings in §6, including devices, recovery, credentials, addons, servers, routes/trust,
  private addresses, owner PIN and Visitor policy;
- the library-v4 §17 download rows and download lease; and
- rows of a newer kind that it must preserve under library-v4's unknown-row rule.

Every live **persistent profile**, Owner included, has exactly one distinct random library key and an ordinary
library-v4 activity log. That log holds title and season documents, personal preferences, and, only when the
profile owns a tracker, tracker credentials, delivery settings and delivery documents. The first product may expose
only Owner and Kid creation, but the representation accepts any number of `kid` and future `adult` profiles.
Clients MUST use the same profile-store path for Owner and every other persistent profile; there is no permanent
Owner projection in the household and no Kid-specific row format.

A profile library MUST NOT hold the profile directory, owner PIN, household credentials, connections, devices,
downloads, Visitor state, or another profile's data. Household values are read from the simultaneously open
household control log; they are never copied into every profile log.

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
  "profiles": {
    "00112233445566778899aabbccddeeff": {
      "id": "00112233445566778899aabbccddeeff",
      "role": "owner",
      "display": {"value": {"name": "Cindy", "avatar": "fox"}, "at": [1800000000000, 0, "a1b2c3d4e5f60718"]},
      "library": {"value": {"keyVersion": 1, "key": "ERERERERERERERERERERERERERERERERERERERERERE="}, "at": [1800000000000, 1, "a1b2c3d4e5f60718"]},
      "policy": {"value": {"maxBand": null, "unknownRatings": "allow"}, "at": [1800000000000, 2, "a1b2c3d4e5f60718"]},
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
- `policy.value.maxBand` is `null`, `littleKids`, `family`, `teen` or `mature`.
  `unknownRatings` is `allow` or `block`. The effective check is in §5.
- `display`, `library`, `policy` and `deleted` are stamped fields. Stamps and their clock/future handling are
  library-v2 §4. A deletion is permanent: a deleted id cannot become live again, its library is retired by the
  lifecycle operation, and recreation uses a new id and key. The tombstone retains the last library reference so
  interrupted retirement can be completed; no activity is read from it.

The schema permits extension fields so a newer client can add display or lifecycle facts. A writer keeps unknown
fields byte-for-byte in the logical document unless den-core tells it how to merge them. A client that does not know
the `profiles` kind keeps its sealed row under library-v4's unknown-kind rule.

### Canonical validation and merge ownership

Clients do not independently implement directory decisions. den-core operations are authoritative:

- `profiles_validate(document)` returns the canonical document or an error code;
- `profiles_merge(a, b)` validates both versions, merges them, then validates the result; and
- lifecycle operations construct creates, renames, policy changes, deletions and key changes from validated input.

The merge is the union of profile ids. `owner`, each entry's `id`, and `role` are immutable; disagreement is
`identity_conflict`. Known stamped fields use library-v2's later-stamp rule, except `library`: greater `keyVersion`
wins first and its stamp breaks a tie. A `deleted: true` is irreversible even if another valid input has a later
false value. Unknowns use library-v4 §6's unknown-field rule. The merged document is rejected if the owner is
missing/deleted, map keys and ids differ, a role conflicts, a live key is duplicated, or any field is malformed.

An implementation MUST call these den-core operations for validation and convergence; matching Swift and
TypeScript reimplementations are not conforming. Vectors pin the operation boundary. The schema catches shape and
local values, while den-core catches cross-entry and historical invariants.

## 4. Owner, Kid and Visitor

**Owner** is a persistent profile. Its activity and personal settings live only in its profile library. Owner is
the only role that manages the directory, Kid policy, owner PIN, household connections, devices and downloads.
An existing owner's missing/unknown certification remains fail-open: its migrated/default policy has
`unknownRatings: "allow"`.

**Kid** is a persistent profile with independent activity, preferences and recommendations. Creation requires an
owner PIN to exist and defaults `unknownRatings` to `block`; the owner chooses the initial `maxBand`. Only an
owner-authorized operation writes a Kid's policy. A Kid session receives no policy writer and has no tracker in v1.
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

The normalized bands, from stricter to looser, are `littleKids`, `family`, `teen`, `mature`, then `null` (no band
ceiling). The eventual rating-source mapping is separate and must be pinned before Kid discovery ships. Legacy
Owner input decodes `pg13` as `teen`, `r` as `mature`, and absent/null as null.

For a normalized title band, allow only when it is no looser than `maxBand`, or when `maxBand` is null. For an
unknown title band, allow exactly when `unknownRatings` is `allow`. Eligibility MUST be applied before ranking and
pagination and again at play/deep-link/download boundaries. A session-only title unlock changes no stored policy.

A stricter remote policy applies immediately. A looser policy waits for the owner PIN on a device already enforcing
a policy. The ordering compares `maxBand` first and treats changing `unknownRatings` from `allow` to `block` as
stricter, the reverse as looser. Policies whose two components move in opposite directions are incomparable and
require the owner PIN before either loosening is applied.

## 6. Normative settings ownership

Scope is assigned key by key. A client MUST NOT infer it from a row name, UI section or an unknown key's prefix.
Adding a synced setting requires assigning it in a later version of this table.

| Existing setting | Scope after profiles | Non-owner behavior |
|---|---|---|
| `keys.tmdb`, `keys.omdb`, `keys.doesthedogdie` | Household | Read/use; cannot manage |
| `keys.simkl`, future tracker credentials | Profile owning that tracker | Kid/Visitor do not read or deliver in v1 |
| `keys.cfAccessId`, `keys.cfAccessSecret` | Household | Read/use; cannot manage |
| `keys.parentalPIN` | Household owner PIN | Verify gates; cannot manage |
| profile-directory Kid policy | Household, owner-managed | Kid reads; Kid session has no policy writer |
| Visitor policy | Household; inherits Owner policy in v1 | Read only |
| `plugins.*`, `servers.*`, `trust.*`, `addresses.*` | Household | Read/use; cannot manage |
| `devices.*`, `recovery.*` | Household | Hidden from non-owner administration |
| download rows and download policy | Household | Kid/Visitor actions disabled or PIN-gated |
| `prefs.den.watchRegion` | Household | Inherit |
| `prefs.den.myServiceIDs` (legacy) | Household | Inherit |
| `prefs.den.myServicePicks` | Household | Inherit |
| `prefs.den.excludedGenreIDs` | Profile | Read/write own |
| `prefs.den.excludedLanguages` | Profile | Read/write own |
| `prefs.den.preferredSubtitleLanguage` | Profile | Read/write own |
| `prefs.den.hasExplicitSubtitlePreference` | Profile | Read/write own |
| `prefs.den.shownSubtitleLanguages` | Profile | Read/write own |
| `prefs.den.maxSubtitlesPerLanguage` | Profile | Read/write own |
| `prefs.den.preferredAudioLanguage` | Profile | Read/write own |
| `prefs.den.autoSkipSegments` | Profile | Read/write own |
| `prefs.den.autoplayTrailers` | Profile | Read/write own, subject to Kid policy |
| `prefs.den.hideAnime` | Profile | Read/write own |
| `prefs.den.hideWatched` | Profile | Read/write own |
| `prefs.den.minReleaseYear` | Profile | Read/write own |
| `prefs.den.enabledRatingSources` | Profile | Read/write own |
| `prefs.den.shownWarningCategories` | Profile | Read/write own |
| `prefs.den.maturityCeiling` | Owner profile migration input | Translate to Owner policy; not retained as household policy |
| OLED/presentation and other unsynced settings | Device | Follow device |

Every persistent profile opens its own `set:prefs`; household preferences are read separately from the control log.
Personal values are never copied between profiles. An unassigned future synced key MUST NOT be moved or copied by a
split until a later contract assigns its scope.

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
  any live profile is restricted (`maxBand` non-null or `unknownRatings: block`), Owner and management remain behind
  the owner PIN; the client MUST NOT silently choose Owner.
- Owner → Kid or Visitor and Visitor → Kid need no PIN. Kid → Owner and Visitor → Owner require the owner PIN.
  Kid → Visitor requires it whenever Owner's inherited Visitor policy is looser than the Kid policy (§5).
- Switching away from Visitor deletes the complete local namespace before the new mode can record activity.
  Switching away from a persistent profile cancels its tasks and closes it but never deletes its library.
- Playback is dismissed, profile-owned delayed progress/tracker/recommendation work is cancelled, navigation is
  reset, and profile-specific caches are cleared before another mode becomes active. Household services stay open.

## 8. Vectors and deferred lifecycle work

[`vectors/profiles-v1.json`](../vectors/profiles-v1.json) pins valid and invalid directories, more than one Kid,
distinct keys, role semantics, settings scope, sticky selection and wire topology. The schema and vector-specific
cross-field checks run with `python3 tools/validate_profiles.py`.

This revision intentionally has no vector for the destructive initial split, concurrent winner adoption, orphan
cleanup, backup, or multi-profile §12 reset. It must not be cited as authorization to implement them. The follow-up
contract will add those state machines and vectors before any product code enables profiles or changes a production
minimum.
