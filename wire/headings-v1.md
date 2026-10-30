# Semantic headings v1

Den clients exchange shelf and section headings as presentation semantics, not as localized text and not as an
English string that another client must parse. The v1 value is:

```json
{
  "id": "den.shelf.because_watched",
  "args": { "title": "Dune" },
  "fallback": "Because you watched Dune"
}
```

`id` is an opaque, stable message identifier. It is lower-case ASCII, begins with a letter, and contains at most
128 letters, digits, `.`, `_`, or `-`. Once shipped, an id keeps the same meaning and argument names. Adding an id
does not change this wire version.

`args` is always present. It has at most 16 named arguments. Names follow the same syntax as ids and are at most
64 bytes. Values are UTF-8 strings of at most 512 Unicode scalar values or integers in JavaScript's safe integer
range (`-(2^53-1)` through `2^53-1`). Booleans and floating-point values are deliberately absent: producers name
the semantic state instead of making each renderer reproduce branching or number rounding.

`fallback` is always present and non-empty, with at most 1,024 Unicode scalar values. It is the established English
rendering with the arguments already inserted. A client that does not know `id`, cannot format its arguments, or
does not support semantic headings MUST display `fallback` verbatim. It MUST NOT expose `id`, guess meaning from
`fallback`, or partially substitute arguments. The English UI may use `fallback` directly; this keeps the default
path free of catalog lookup and formatting work.

Known clients localize the id and substitute only the named arguments required by that id. Argument strings are
content (title, person, studio, collection, service or taxonomy labels) and remain verbatim unless the id's registry
explicitly defines an argument as another controlled semantic value. Clients MUST escape arguments for their output
surface and MUST NOT interpret argument text as a message id or format string.

External/add-on headings use `den.verbatim`, `{ "value": <text> }`, and the same text as `fallback`. Clients render
the value verbatim in every language. This prevents an external heading that happens to equal product copy from
being translated accidentally.

## Compatibility

During migration a heading-bearing field MAY contain either the object above or a legacy JSON string. A reader of a
legacy string treats it as `{ "id": "den.legacy", "args": {}, "fallback": <string> }`. `den.legacy` is fallback
only: readers MUST NOT parse the English string or infer semantics from an adjacent catalog id. Writers SHOULD emit
the object once every reader of their storage or API supports it.

The object has exactly `id`, `args`, and `fallback`. A future shape that needs more fields receives a new wire
version or is carried by a versioned containing document. Unknown ids are valid and use the fallback, which lets a
new producer interoperate with an older renderer without a coordinated deployment.

Caches persist the semantic object, not a localized rendering. Transformations that replace, filter, page, or
deduplicate a heading's items preserve the object byte-for-byte in meaning. A cache migration may normalize a legacy
string to `den.legacy`; it must never classify that string by English prefixes or mutable row ids.

The schema's top-level `oneOf` is the complete migration value. The normalized object is `$defs/heading`.
