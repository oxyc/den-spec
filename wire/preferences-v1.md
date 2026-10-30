# Preferences v1

This contract defines semantic operations on profile-owned members of the library-v4 `set:prefs` row. Storage,
stamps, encryption, merge, and transport remain library-v4. A client applies every member of one returned `changes`
object to one settings-row edit with one stamp; splitting that object into separate writes is non-conforming.

## Subtitle preference

Subtitle selection has three states:

- `{"mode":"automatic"}`: the player may choose for foreign audio.
- `{"mode":"off"}`: subtitles stay off until the viewer selects a track.
- `{"mode":"language","language":"sv"}`: prefer the named canonical lower-case ISO 639-1 language.

The two stored members are `den.preferredSubtitleLanguage` (string or tombstone) and
`den.hasExplicitSubtitlePreference` (boolean or tombstone). A write is the following atomic patch:

| choice | preferred language | explicit marker |
| --- | --- | --- |
| Automatic | tombstone | tombstone |
| Off | tombstone | `true` |
| language | language string | `true` |

On read, a valid explicit marker decides whether absence of a language is Automatic or Off. A language with no
marker is explicit for compatibility with old Apple/Web clients. A present language tombstone with no marker is
Off: that is the exact Off write made by Web clients before the marker existed. Only complete absence of both keys
is Automatic. Invalid types, `false` markers, empty/invalid language codes, and conflicting states fail closed.

The den-core operation is `preferences_subtitle_v1`. `operation: "read"` accepts `values`, a JSON object whose
members are the current un-stamped setting values (tagged config value or `null`); key presence is significant.
It returns `{ "version": 1, "choice": ... }`. `operation: "write"` accepts `choice` and returns
`{ "version": 1, "changes": ... }`. Unknown members are ignored and never returned, so the caller preserves them.

Edge remains ciphertext-blind: this operation runs only after a holder of the profile key authenticates and opens
the row. Hearth consumes the resulting playback choice and never stores profile preferences.

## Interface language

`den.interfaceLanguage` is the profile-owned language used for Den's interface, localized metadata and the
Automatic subtitle viewer language. Current writers support `en`, `es` and `sv`. Audio language is a separate
preference: changing interface language never writes `den.preferredAudioLanguage`.

Key presence distinguishes inheritance from an independent English decision:

| Stored member | Effective language | Explicit | Supported |
| --- | --- | --- | --- |
| absent | `en` | false | true |
| tombstone | `en` | true | true |
| `{ "string": "en" }` | `en` | true | true |
| `{ "string": "es" }` | `es` | true | true |
| `{ "string": "sv" }` | `sv` | true | true |
| canonical future language, e.g. `{ "string": "fr" }` | `en` | true | false |

An absent member is eligible for the profiles-v1 inheritance seed. A tombstone is a held legacy/reset decision
and therefore is not eligible. New writers always materialize a choice as a string, including English. An older
reader preserves a canonical unsupported two-letter string, renders English, exposes the stored language for an
unsupported-choice explanation, and never rewrites it. Invalid types and non-canonical strings fail closed.

The den-core operation is `preferences_interface_language_v1`:

- `operation: "read"` accepts `values` with the same key-preserving config-value representation as the subtitle
  operation and returns `{ "version": 1, "selection": { "language", "explicit", "supported",
  "storedLanguage"? } }`.
- `operation: "write"` accepts `language: "en" | "es" | "sv"` and returns one-member `changes` containing an
  explicit tagged string. The caller commits it with one settings-row stamp.

Profile inheritance materializes the Owner's effective language, including explicit `en`, only after both Owner
and child settings have reached authenticated heads. Existing members, including tombstones and unsupported future
strings, are decisions and are never overwritten.
