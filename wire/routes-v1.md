# Routes v1

How a Den client finds the address that reaches each of the deployment's services from where it is now: on the
LAN at home, on the tailnet, or through the public names. den-edge publishes one table, and every client applies
the same rule to it (oxyc/den#16).

## 1. The table

`GET /routes` on den-edge, on every name:

```json
{
  "v": 1,
  "addons": {
    "edge":  [{"url": "http://192.168.86.193:8094"}, {"url": "https://pve.example.ts.net:8443"}, {"url": "https://d-api.example.fi"}],
    "scout": [{"url": "http://192.168.86.193:8080"}, {"url": "https://pve.example.ts.net:8443/scout"}, {"url": "https://d-scout.example.fi", "access": true}],
    "remux": [{"url": "http://192.168.86.193:8095/remux"}, {"url": "https://pve.example.ts.net:8443/remux"}, {"url": "https://d-remux.example.fi/remux", "access": true}]
  }
}
```

- **Names:** `edge`, `scout`, `play` (scout's play tickets), `subs`, `atlas`, `reel`, `remux`. A client ignores a
  name it doesn't know.
- **An entry** is `url`: `http(s)://host[:port]` and an optional path, with no trailing slash. A request for a
  service's route `/r` goes to `url + /r`.
- **`access: true`** marks a public name behind Cloudflare Access. A client holding the library's Access service
  token (`set:keys` `cfAccessId`/`cfAccessSecret`) sends it to these entries as `CF-Access-Client-Id` and
  `CF-Access-Client-Secret`, and to nothing else.
- **Order** is the order to try them: LAN, tailnet, public.
- **Every address is listed,** whether or not something answers there. A name that doesn't resolve, or a service
  that isn't published on it, fails the health check (§2), and clients move on. What a deployment publishes is its
  own decision, not the clients'.
- **The same table on every name,** LAN addresses included. A client away from home still holds install URLs
  issued on the LAN, and matches them against those entries (§4). A LAN address reaches nothing from the internet,
  and the home check (§3) keeps a client from using one on someone else's network.

## 2. The rule

For each service, a client:

1. takes its entries in order;
2. skips what it can't use: a page loaded over https skips `http://` entries, and a client without the Access
   token skips `access` entries;
3. uses a plain `http://` entry only after **the home check** (§3);
4. probes `GET <url>/health` with the credentials it would really use, and accepts a `200` with a JSON body;
   Access's login redirect is a failure;
5. takes the first entry that passes, and **remembers it** per service. It probes again when the network changes
   or a request to the remembered entry fails.

If no entry passes: trailers fall back to YouTube, and a service without a route is unavailable from here.

## 3. The home check

A plain `http://` entry is unauthenticated: on another network, `192.168.86.x` can be a stranger's device, and a
client must not hand it install URLs or library traffic.

Before a client uses one, it reads `GET /lib/{id}/changes?since=0&limit=1` from the `edge` entry on the same host.
The host counts as this deployment's only if a returned row **opens under the library key** (library-v2 §4): no
other device can produce one. Passing trusts every plain `http://` entry on that host until the network changes.
Failing, or an empty library, skips them. `https://` entries need no check: the certificate proves the name.

A client doesn't reuse the result across networks, because two networks can share an address range. The check is
the library read a client makes at launch anyway.

## 4. Install URLs

An addon's install URL keeps the address it was issued on: the LAN address or a public name. A client finds its service by
matching the URL against the table: the entry the URL starts with names the service, and the rest of the URL (the
sealed config segment) goes with it to whichever entry wins, as `winner.url + rest`.

## 5. Deployment (informative)

A deployment builds the table from four facts. oxyc/den's `deploy/render-env.sh` takes them from `.env`:

| Fact | Gives |
|---|---|
| `DEN_LAN` | `http://<lan>:<port>` for each service |
| `DEN_TAILNET` | `<tailnet origin>/<service>`, the `tailscale serve` paths (den-edge at the root) |
| `DEN_PUBLIC` | `https://d-<service>.<domain>` (den-edge's device API is `d-api`) |
| `DEN_PROXY` | nothing here: the proxies whose forwarded client address den-edge and den-remux trust |
