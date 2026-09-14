# Attribution v1

Where a Den client's credits come from. Each source's terms ask for its own statement wherever its data reaches
people, and the addon that uses a source is the one that knows it does — so the addon says so in its manifest, and
a client builds its credits from the addons it has installed rather than from a list compiled into it.

## The manifest field

A Den addon's manifest (a Stremio manifest; stock clients ignore unknown fields, as with `denInstallId`) carries:

```json
{
  "id": "com.den.atlas",
  "denAttribution": [
    {
      "text": "Streaming availability information is provided by Streaming Availability API by Movie of the Night.",
      "link": "Streaming Availability API by Movie of the Night",
      "url": "https://www.movieofthenight.com/about/api"
    },
    { "text": "Streaming availability by JustWatch.", "link": "JustWatch", "url": "https://www.justwatch.com" }
  ]
}
```

- `text` — the statement, whole, as the source's terms word it. Required; plain text, no markup.
- `url` — where the statement links. Optional.
- `link` — the part of `text` that is the link. Optional; when absent, or not found in `text`, a client links the
  whole statement (or none of it, without `url`).

An addon lists a source only while it uses it: an operator who hasn't turned a source on isn't credited for it. The
list's order is the order a client shows the statements in. A manifest without the field credits nothing.

## What a client does

A client shows, in its credits (Settings › About on the TV and the web):

1. Its own sources, before any addon's: TMDB always (with its logo, as TMDB's terms ask), and a source a Den client
   calls itself only while it does — OMDb while a key is set, DoesTheDogDie while a key is set, Trakt while connected.
2. Each installed addon's `denAttribution`, in the order the addons are listed, a statement repeated by two addons
   shown once.

A client reads a manifest it may read: the TV any installed addon's; the web only Den's own addons (den-spec
routes-v1), since an addon that isn't Den's is never sent anything from a browser. A statement is text: a client
never renders it as markup, and opens `url` only when it is `https`.
