# Household profiles, v1 — proposal

**Status: complete contract; not activated.** This document is the normative contract for
[den-spec#39](https://github.com/oxyc/den-spec/issues/39) and the architecture in
[den#234](https://github.com/oxyc/den/issues/234). It defines the data model clients and den-core will implement.
Its maturity values follow the conservative numeric model selected in
[den-dataset#226](https://github.com/oxyc/den-dataset/issues/226).
It does **not** authorize a client to split an existing library, publish a profile directory or approval document,
raise a production wire minimum, or make an existing wire-4 household refuse a wire-4 client. Sections 8 and 9 pin
those state machines so implementations can be built and reviewed; activation still requires an explicit product
release after every participant implements this contract. Reading or shipping this specification is not activation.

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
`x-den-wire: 4`, and a new profile log starts with `x-den-wire-min: 4`. Profile-aware requests to the household
advertise `x-den-wire: 5` only after §8's fenced commit.

Merely understanding this document, constructing a directory in memory, or reading the vectors MUST NOT raise a
household's `x-den-wire-min`. A §8 commit atomically rewrites the household and raises its minimum to 5, but this
contract does not enable any caller to initiate that commit. Until a separately reviewed implementation is
activated, every existing household minimum stays unchanged.

Experiments or canary behavior at `d.oxy.fi` may exercise this contract behind deployment controls, but do not
alter the wire contract, activate the split, or authorize any production wire-minimum change. Wire behavior changes
only through §8's fenced activation operation after that operation is deliberately enabled in product code.

The household key seals the directory, including all profile keys. Profiles are UX and data-isolation boundaries,
not protection from a hostile device that holds the household key. Selectively wrapped private profiles are out of
scope.

## 2. What each store may hold

The **household library** is configuration only. A profile-aware client MUST NOT derive or write title activity,
season activity, reactions, resume state, personal recommendations, tracker commands, tracker receipts, tracker
delivery documents, or tracker credentials there. It may hold:

- the `profiles` document (§3) and per-Kid title-approval documents (§5.1);
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
- A directory stored by the initial split MUST contain `migration`. `attempt` is a random 16-byte id. `source`
  identifies the exact den-edge rewrite generation and head that the backup and copy came from. `backup.id` is the
  durable provider handle and `backup.digest` authenticates the verified backup artifact. `ownerProfile` MUST equal
  `owner`, and `ownerHead` is the verified head of that entry's destination library before commit. All digests and
  heads are 32 bytes in lowercase hexadecimal. These values are immutable evidence, not a progress register;
  pre-commit progress lives only in the local journal in §8.

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

## 4. Owner, Kid and Visitor

**Owner** is a persistent profile. Its activity and personal settings live only in its profile library. Owner is
the only role that manages the directory, Kid policy, owner PIN, household connections, devices and downloads.
An existing owner's missing/unknown certification remains fail-open: its migrated/default policy has
`unknownRatings: "allow"`.

**Kid** is a persistent profile with independent activity, preferences and recommendations. Creation requires an
owner PIN to exist and defaults `unknownRatings` to `block`; the owner chooses the initial `maximumAge`. Only an
owner-authorized operation writes a Kid's policy or title approvals. A Kid session receives neither writer and has
no tracker in v1.
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
`minimumAge <= maximumAge`; an unknown title is eligible exactly when `unknownRatings` is `allow`. Owner migration
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
and title approvals above. This distinction lets an owner deliberately open a specific obscure or adult-oriented
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

Scope is assigned key by key. These are the exact sixteen non-legacy `SettingsLogBridge` preference keys after the
split. A client MUST NOT infer scope from a row name, UI section or prefix.

| Existing key | Scope after profiles | Split rule |
|---|---|---|
| `prefs.den.watchRegion` | Household | Retain; every profile inherits it |
| `prefs.den.myServicePicks` | Household | Retain; every profile inherits it |
| `prefs.den.excludedGenreIDs` | Profile | Move to Owner |
| `prefs.den.excludedLanguages` | Profile | Move to Owner |
| `prefs.den.preferredSubtitleLanguage` | Profile | Move to Owner |
| `prefs.den.hasExplicitSubtitlePreference` | Profile | Move to Owner |
| `prefs.den.shownSubtitleLanguages` | Profile | Move to Owner |
| `prefs.den.maxSubtitlesPerLanguage` | Profile | Move to Owner |
| `prefs.den.preferredAudioLanguage` | Profile | Move to Owner |
| `prefs.den.autoSkipSegments` | Profile | Move to Owner |
| `prefs.den.autoplayTrailers` | Profile | Move to Owner; Kid use remains subject to policy |
| `prefs.den.hideAnime` | Profile | Move to Owner |
| `prefs.den.hideWatched` | Profile | Move to Owner |
| `prefs.den.minReleaseYear` | Profile | Move to Owner |
| `prefs.den.enabledRatingSources` | Profile | Move to Owner |
| `prefs.den.shownWarningCategories` | Profile | Move to Owner |

Two legacy inputs are outside the sixteen-key post-split table: `prefs.den.myServiceIDs` remains in the household
for old readers but is not copied, and `prefs.den.maturityCeiling` is decoded into Owner policy by §5 and then
removed. `keys.simkl` and future tracker credentials move to the owning profile. Everything else already named
household-wide in den#234 remains in the household rewrite: metadata and Cloudflare credentials, `keys.parentalPIN`,
`plugins.*`, `servers.*`, `trust.*`, `addresses.*`, `devices.*`, `recovery.*`, and download rows and policy. The
directory's Kid policy and approvals are household documents; Visitor inherits Owner policy.

The following device categories are intentionally non-exhaustive and are not part of the sixteen-key contract:

| Device-only category | Rule |
|---|---|
| OLED and presentation preferences | Remain on that device |
| active profile/Visitor marker and Visitor namespace | Follow §7; never enter either library |
| transient play-once authorization and retained navigation | Clear at the boundaries in §§5.1 and 7 |

Every persistent profile opens its own `set:prefs`; household preferences are read separately from the control log.
Personal values are never copied between profiles. An unknown or future synced key MUST remain in the household and
MUST NOT be copied into a profile until a later contract assigns it explicitly.

`profiles_partition_preferences(values)` is den-core's canonical pure split operation for the legacy
`set:prefs.values` object. It returns three disjoint outputs:

- `household` contains `den.watchRegion`, `den.myServicePicks`, legacy `den.myServiceIDs`, and every unknown key;
- `owner` contains exactly the fourteen profile-scoped keys in the table, with each complete stamped value kept
  byte-for-byte; and
- `ownerMaturity` contains the complete legacy `den.maturityCeiling` stamped value, or `null` when absent. It is an
  input to Owner-policy migration and is retained in neither output settings row.

The operation never infers scope from a prefix, never interprets a stamped setting value, and rejects a non-object
input. The split executor MUST use its result rather than maintaining a second key list. An empty `owner` or
`household` object means that destination has no `set:prefs` row to write; the executor does not invent an empty row.

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

## 8. Initial split: explicit, backed up and fenced

The split starts only from an explicit owner action after the owner PIN is verified. Upgrade, launch, opening these
vectors, creating a directory in memory, or merely seeing a capable peer MUST NOT start it. The client durably keeps
a local journal keyed by a random `attempt`; it never writes intermediate migration state to the shared log.

The states and only permitted next effects are:

| Local state | Required durable evidence | Next effect |
|---|---|---|
| `idle` | explicit owner intent | Open a den-edge rewrite and record its generation and base head as `fenced` |
| `fenced` | source fence | Create a complete backup at that exact fence; verify its digest and readability |
| `backed-up` | source fence plus verified backup | Create one random Owner key/library and copy the Owner partition |
| `owner-copied` | candidate library id/key | Verify every source row is accounted for and derived state and delivery pending state agree |
| `ready-to-commit` | all prior evidence | Stage the configuration-only household form and commit `{base, wireMin: 5}` |
| `commit-unknown` | attempted commit | Read the household; never guess or submit the same rewrite blindly |
| `adopting` | a valid committed directory | Open and verify its Owner library, then make its profile the local active mode |
| `cleaning-loser` | winner and a distinct local candidate | Retire/delete only the unreferenced candidate; repeat until absence is confirmed |
| `active` | winner usable and any losing candidate absent | Delete the local journal; use household wire 5 and profile wire 4 |
| `restore-required` | verified backup and no valid committed directory | Stop; require an explicit recovery action under a fresh fence |

The backup MUST be complete through the recorded head, restorable under the unchanged household key/id, readable
after creation, and retained at least until `active`. No Owner destination write may occur first. The Owner copy
contains every title and season document, all fourteen profile preferences in §6, SIMKL credentials, commands,
receipts, pending/held work, epochs and delivery documents, including facts needed to avoid replay. Its verification
compares decoded/derived activity and pending delivery decisions, not merely row counts.

The staged household is built from the same fenced source. It retains all household state named in §§2 and 6,
adds the directory and its immutable migration evidence, and contains no activity, personal preference, tracker
credential, command, receipt or delivery row. Its directory Owner key is the verified candidate key. The single
den-edge generation/base-fenced rewrite commits the staged rows and `x-den-wire-min: 5` together. It keeps the
existing household key, id, membership and links. A split is not a §12 reset and never asks a device to pair again.

den-edge alone selects a concurrent winner by accepting one fence. `profiles_migration_next(input)` in den-core is
the canonical pure decision operation. Its input contains `state`, `observed` (`legacy`, `committed` or `corrupt`),
and the following evidence when applicable:

- `explicitIntent` is true before `idle` may return `acquire-fence`;
- `fence` is the journal's `{generation, head}` and `observedFence` is the legacy household's current pair. Both use
  a nonnegative safe-integer generation and a 32-byte lowercase-hex head. Post-`idle` legacy states require both;
- `directory` is the stored, canonically validated committed directory. It is required for a `committed`
  observation and forbidden as evidence of a legacy or corrupt household;
- `backupVerified`, `ownerVerified` and `winnerVerified` are observations, not commands. A verified candidate Owner
  also carries its authenticated `candidateHead`; an unverified candidate has no head;
- `candidateKey`, when a candidate exists, is its canonical padded base64 32-byte library key.
  `candidateAttempt` is the local journal's random attempt id when comparing that candidate with a committed
  directory, and `candidateRetired` reports confirmed absence.

The operation validates all supplied evidence before deciding. A local candidate is the committed Owner only when
its `candidateAttempt`, `candidateKey` and verified `candidateHead` equal the directory's immutable migration
attempt, Owner key and Owner head. That winner is verified and activated; it never enters loser cleanup. Cleanup
compares any actual losing `candidateKey` with the library key of **every live profile** in the committed directory.
A referenced key while in `cleaning-loser` returns `cleanup_targets_winner`; the same mismatch outside cleanup is
`winner_evidence_mismatch`. Evidence claiming that a committed key is already retired is the contradiction
`committed_key_retired`. Only a valid, distinct, unreferenced candidate may produce
`retire-losing-candidate`. A malformed/missing committed directory, malformed fence or key, verified copy without a
head, missing explicit intent, or phase/evidence contradiction is an error rather than a next effect. The operation
returns one of the effects named in the table or an error. Swift and web MUST execute that answer and MUST NOT infer
success from their own request outcome.

After any conflict, timeout or process death, read the household first:

- A valid committed directory is the winner even when its `attempt` differs. Adopt it. If this client created a
  different Owner library, enter `cleaning-loser`; cleanup is idempotent and continues after restart.
- Cleanup MUST compare the candidate key with every live key in the committed directory. Equal or referenced means
  `cleanup_targets_winner` and no delete. Only a distinct, unreferenced candidate may be retired. Absence counts as
  success, so a retry cannot create a storage-counting orphan.
- If the household remains legacy and the original fence still matches, resume the journal's next effect. If its
  fence changed, retire any candidate, discard only the attempt journal, and restart from `idle` on explicit intent.
- An atomic fenced rewrite failure leaves the old household authoritative; do not restore over it. An unreadable or
  partial household with no valid committed directory enters `restore-required`. Recovery first proves the backup's
  digest and source fence, opens a fresh rewrite, and restores only if den-core answers `restore-backup`. A backup
  MUST NOT restore over a valid wire-5 directory or lower its minimum.
- A valid committed directory whose Owner library cannot yet be verified remains `adopting`; it never falls back to
  household activity and never lowers the wire minimum. Repair/finish the referenced Owner library from the verified
  candidate/backup facts, or stop for recovery.

Until product activation is separately approved, `profiles_migration_next` may be implemented and tested but no UI
or background path may supply the explicit owner intent, publish the directory, commit the rewrite or change a live
minimum.

### 8.1 den-edge storage transaction

The initial split requires a storage transaction in addition to the pure den-core planner. It is not conforming to
assemble these effects from an ordinary `/changes` read, a best-effort file copy, the existing short-lived generic
rewrite stage and an ordinary `DELETE`: those operations leave gaps in which a source write, process death or a
newly committed profile directory can make the evidence stale.

This section names the required operations. The route names in the vectors are the interoperable HTTP surface;
an in-process store may expose equivalent calls, but it MUST return the same evidence and errors. Every request is
authenticated as the household library, carries `x-den-wire: 5`, and is unavailable unless the separately reviewed
profile-migration feature is enabled. Defining the routes does not enable them.

#### Source fence and durable lookup

`POST /lib/{household}/profile-migration` with `{"attempt":"<32 lowercase hex>"}` opens the transaction.
Repeating it for the same authenticated household and attempt is a lookup, not a second attempt. A successful answer
is:

```json
{
  "attempt": "0123456789abcdef0123456789abcdef",
  "state": "open",
  "fence": {
    "generation": 7,
    "base": 418,
    "head": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "rewrite": "opaque-provider-handle"
  }
}
```

`generation` is a durable monotone safe-integer counter per household in the **profile-migration fence namespace**.
Opening an attempt reads the current value; it does not allocate a random value or advance it. Every visible
household mutation (an applied ordinary batch, migration rewrite or restore) advances it atomically, so attempts
opened on the same snapshot share a generation and any later write invalidates all of their compare-and-swaps. It
is never restored backwards or reused; restoration advances from the provider-retained high-water mark, and the
vanishingly unlikely safe-integer exhaustion refuses mutation rather than wrapping. It is
not the public `x-den-generation` sync header, is not parsed from that header and need not change when that header
changes. `base` is the household sequence head frozen by this transaction. `head` is the lowercase SHA-256 digest
of RFC 8785 JCS for `{libraryId, generation, base, wireMin, rows}`, where `rows` contains every `{k, seq, v}`
through `base` ordered by raw row-key bytes. A client calls it authenticated only after opening every known row,
checking each AEAD and logical-name binding, preserving every unknown row, and recomputing this digest. `rewrite`
is an opaque handle.

The opened fence does **not** block ordinary household writes: any such write makes its later commit lose the
generation/base/head compare-and-swap. The source snapshot and staging area are durable across client process death,
den-edge restart and an arbitrarily long Owner copy. They MUST NOT disappear on an idle timer.
`GET /lib/{household}/profile-migration/{attempt}` returns the same open fence, or a durable terminal `committed`,
`restored`, `superseded` or `aborted` result. Thus a `commit-unknown` or `restore-unknown` journal can look up the
attempt before deciding anything.
Multiple attempts may open against the same legacy source; the first matching commit wins and makes the others
`superseded`. `DELETE /lib/{household}/profile-migration/{attempt}` explicitly aborts only an uncommitted attempt
and records the terminal tombstone. It is never inferred from idleness or client disconnect.

The planner's and committed directory's `source` is the durable projection `{generation, head}`. `base` and
`rewrite` remain in the local journal/storage transaction: they are needed to execute the compare-and-swap but are
not immutable directory identity. A storage adapter MUST reject a journal projection whose generation/head differs
from its reopened transaction.

#### Verified backup before destination writes

`POST /lib/{household}/profile-migration/{attempt}/backup` is idempotent and creates one immutable backup from the
frozen source, never from a later read. Its answer is `{id, digest, source}`. `source` is the complete four-field
fence above. The backup contains the household's exact row snapshot with sequences, wire minimum, authorization
credential hashes/member proof and provider metadata needed to restore the same library id and key. Pairing,
recovery and other records outside the library object are not replaced by a restore and therefore remain attached
to that same id.

`digest` is lowercase SHA-256 of RFC 8785 JCS for the immutable manifest
`{id, householdId, attempt, source, wireMin, authorization, rows}`, with rows in the same canonical order. The
authorization member is the provider's non-secret credential-hash/member-proof representation, never the household
key or raw token.

`GET /lib/{household}/profile-migration/{attempt}/backup` returns the immutable artifact and manifest. Verification
requires all of the following:

- the provider digest matches the complete artifact and manifest;
- its household id, attempt and four-field source fence equal the journal;
- every row through `base` is present exactly once with its original sequence and ciphertext;
- every known row opens and passes its logical-name binding, and every unknown row is retained byte-for-byte; and
- a dry restore can reconstruct the same id, authorization, wire minimum, base and authenticated head.

Only then is `backupVerified` true. The backup is durable independently of the rewrite handle and retained until
the client reaches `active` (or an explicit post-recovery retention policy keeps it longer). Creating an Owner
library or writing its first row before this verification is a protocol violation. An artifact id, row count,
filesystem-copy success or provider checksum alone is not verification.

#### Fenced in-place commit

Rows are staged with `POST /lib/{household}/profile-migration/{attempt}/rows`. Staging is idempotent by row key and
does not change the visible household. The server binds every staged row set to the transaction fence and verified
backup. The client separately verifies that the staged projection is configuration-only, contains the canonical
profile directory and accounts for every source row according to §§2, 6 and 8.

`POST /lib/{household}/profile-migration/{attempt}/commit` supplies the complete four-field `fence`, backup
`{id,digest}`, a digest of the complete staged row set, and `wireMin: 5`. Under the household lock den-edge MUST
atomically verify the attempt/fence/backup/staged digest, replace the rows under the **same** library id and token,
raise the minimum to 5, advance the visible library generation, and record the terminal result. A mismatch returns
`409 migration_fence_moved` and changes neither rows nor minimum. No response or a 5xx is `commit-unknown`; the
client uses attempt lookup and a fresh authenticated household read, never blindly submits another logical commit.
A committed lookup returns the resulting base/generation and staged digest, so a retry cannot create a second
winner.

Restore is not a side effect of a failed commit: an atomic commit failure leaves the legacy source authoritative.

#### Explicit backup restore

Restoring a verified backup is a **fresh**, attempt-addressed transaction. It never reopens or reuses the original
migration attempt or its generation. After an explicit owner recovery action, the client freshly reads and
authenticates the household as far as possible, proves that no den-core-valid committed directory is present, asks
den-core for `restore-backup`, and chooses a new random `recoveryAttempt`.

The idempotent operation is
`POST /lib/{household}/profile-migration/{recoveryAttempt}/restore`. Its request is:

```json
{
  "backup": {
    "attempt": "0123456789abcdef0123456789abcdef",
    "id": "backup-0123456789abcdef",
    "digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "authority": {
    "generation": 9,
    "base": 7,
    "head": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "wireMin": 5
  },
  "profilesRow": {"present": true, "seq": 7, "digest": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},
  "directoryState": "invalid",
  "plannerDecision": "restore-backup"
}
```

`backup.attempt` identifies the original attempt that created the immutable backup; it MUST differ from
`recoveryAttempt`. The provider re-verifies the artifact digest, manifest, original four-field source fence,
household id and authorization member before using it. The request contains neither the household key nor raw
credentials. `authority` is a fresh current CAS value in the profile-migration generation namespace, not
`x-den-generation`. `profilesRow` is either `{"present":false}` or the exact sequence plus ciphertext digest of the
current sealed `profiles` row. `directoryState` is the result of authenticating that exact row and running den-core:
only `absent` or `invalid` is accepted. A valid committed directory forbids restore regardless of which attempt
created it.

On the first request den-edge durably binds the complete request to `recoveryAttempt`; a retry with different bytes
returns `409 migration_attempt_mismatch`. Under the household lock it re-reads the exact generation, base,
authenticated row-set head, wire minimum and sealed `profiles` row. Any difference returns
`409 migration_fence_moved`, records durable terminal `superseded`, and changes nothing. This compare-and-swap pins
the client's den-core decision to the bytes it inspected; den-edge cannot decrypt or independently validate the
directory.

If the CAS still matches, den-edge atomically restores the backup's rows, sequences, authorization representation
and provider metadata under the **same** household id and token. Pairing/recovery records attached outside the
library object remain attached. The resulting minimum is `max(current wireMin, backup wireMin)`, so restoration can
never lower wire 5 (or any later minimum). The provider advances the migration generation exactly once from its
retained high-water mark, refusing exhaustion rather than wrapping; it never restores the backup's old generation.
It recomputes base and authenticated head from the restored visible state, advances the ordinary visible sync
generation as usual, and durably records:

```json
{
  "attempt": "fedcba9876543210fedcba9876543210",
  "state": "restored",
  "generation": 10,
  "base": 418,
  "head": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "wireMin": 5,
  "backup": {"id": "backup-0123456789abcdef", "digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
}
```

A lost response is `restore-unknown`: the client first calls the ordinary attempt lookup. `restored` is success;
`superseded` requires another fresh authenticated read and den-core decision; `open` may resubmit only the identical
request; `aborted` requires a new recovery attempt. The same request after `restored` returns the identical terminal
result without advancing generation again. An explicit abort may tombstone an open recovery attempt, but never a
terminal one.

#### Atomic loser retirement

The cleanup operation is `POST /lib/{household}/profile-migration/{attempt}/retire-candidate`. The household token
uses `x-den-library-token`; the candidate token uses `x-den-candidate-library-token`. Its JSON contains
`{source, authority, profilesRow, protectedLibraryIds, candidateLibraryId}` and contains no library key or decrypted
directory. It is one storage effect even though its implementation may read, validate and then conditionally
commit:

1. Under a fresh household read, the client authenticates every row and either den-core-validates the committed
   directory or establishes that the household is still legacy at a source fence that has genuinely changed.
2. For a committed directory it derives a library id from every live profile key. Locally, before sending any
   delete request, it proves the candidate id **and key** differ from the household and every live entry. The
   plaintext directory and profile keys are never sent to den-edge.
3. The request carries the exact fresh authority `{generation, base, head, kind}`, the sealed `profiles` row
   identity/sequence when `kind` is `committed`, the exact set of protected derived library ids, and the candidate
   id. The candidate token is carried in a separate authorization header, never a profile key in JSON.
4. den-edge locks the household and candidate in deterministic id order, rechecks that the complete authority is
   still current, that the sealed directory row is byte-identical, and that the candidate id is neither the
   household nor a protected id. It then retires the candidate or reports that it is already absent. The check and
   retirement commit atomically.

The protected-id set MUST equal the household id plus the ids derived from every live entry in the den-core-valid
directory; a subset is invalid. Deleted entries are excluded. A committed authority without a valid directory, a
same-source legacy authority, corrupt household, candidate/protected collision, missing candidate authorization or
changed authority returns a blocking/conflict result and performs no retirement. `retired` and `absent` are the
only successful results and are idempotent. The server cannot decrypt the directory, so completeness of the live
set is a client/den-core obligation; the server's exact sealed-row compare-and-swap makes that validated decision
atomic with respect to later directory changes.

The client keeps the local attempt journal until the terminal transaction result, winner verification and any
candidate retirement have all been re-read and confirmed. A provider may garbage-collect an aborted transaction
only after recording a durable terminal tombstone, so lookup never turns an unknown commit into a fresh open.

## 9. Multi-profile key reset and revocation

A household §12 reset is one fenced compound move. It generates a successor household key and one distinct random
successor key for every live persistent profile, increments each profile's `keyVersion` exactly once, and re-seals
each complete ordinary library-v4 log while preserving delivery state. Deleted profiles are not moved. No successor
profile key may equal any old or other successor key.

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
publishes that directory under the successor household, commits its fence, and only afterward retires the old
household and every old profile library. A conflict or unknown outcome is resolved by reading the committed
household move before any retirement. Missing verification, a non-incremented version, reused key, unknown/newer
row that library-v4 §12 cannot move, or any unavailable profile returns an error and leaves all old libraries live.
Revocation is complete only when every old profile library and the old household are confirmed retired; leaving one
old profile key usable is not a reset.

## 10. Vectors and operation boundary

[`vectors/profiles-v1.json`](../vectors/profiles-v1.json) pins directory schema and operations, numeric policy,
approvals, the exact settings table, sticky selection, activation/recovery decisions, loser cleanup, wire topology
and multi-profile reset. `python3 tools/validate_profiles.py` checks the canonical input boundary and expected pure
answers. The vectors specify den-core behavior; they do not authorize a production caller to activate profiles.
