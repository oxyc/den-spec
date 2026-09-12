# Operational HTTP contracts

`addon_contracts.py` checks an already running Den service using Python's standard library:

```sh
python3 tools/addon_contracts.py --service scout --base http://127.0.0.1:8080 --config-mode plaintext
python3 tools/addon_contracts.py --service edge --base http://127.0.0.1:8094 --host d-api.example
DEN_CONTRACT_METRICS_TOKEN=test-token python3 tools/addon_contracts.py --service embed --base http://127.0.0.1:8082
DEN_CONTRACT_BROWSER_KEY=test-key python3 tools/addon_contracts.py --service remux --base http://127.0.0.1:8095
python3 -B -m unittest discover -s tools -p 'test_addon_contracts.py'
```

Start local binaries with isolated temporary state and their test credentials. The runner does not start or
stop services. `--base` is the service's direct origin, without a path prefix; it does not follow redirects or
use ambient HTTP proxies. Use `--host` to exercise Edge's API hostname against a local listener. Run it once for
each desired configuration, including metrics disabled and enabled.

Every profile checks health JSON and no-store, HEAD health without a body, an unknown API route's JSON 404,
PUT health's JSON 405, and refused metrics credentials. Supply `DEN_CONTRACT_METRICS_TOKEN` (or
`--metrics-token`) to also test the correct Bearer credential and Prometheus response. Without it, the success
case is explicitly skipped: the runner cannot tell disabled metrics from metrics protected by an unknown key.
Degraded health is a valid liveness response when it includes reason/detail; `--require-health-ok` additionally
requires readiness.

Profiles preserve service differences:

| Profile | Additional checks |
| --- | --- |
| atlas, embed | Operational routes; no model inference or catalog lookup |
| scout, reel, subtitles | `--config-mode plaintext` checks the no-key response; `sealed` checks a 32-byte public key and epoch. Both check malformed install rejection. Default `skip` leaves deployment-specific sealing unchecked |
| remux | Unknown path stays under `/remux`; checks missing browser/install credentials and a wrong browser key. Supplying `DEN_CONTRACT_BROWSER_KEY` (or `--browser-key`) checks login, returned Bearer token and legacy cookie admission, cookie HttpOnly/Secure/SameSite=Strict flags, and tampered Bearer rejection using an invalid IMDb id that cannot reach Scout |
| edge | Unknown path stays under `/inbox` so SPA fallback cannot satisfy it; a library read must refuse a missing token |

The runner never drains an inbox, creates a pairing/library/session, starts inference, or resolves a title. Remux
login is the sole successful credential operation; it mints stateless short-lived credentials, retained only for
the validation requests. Credentials and response bodies are omitted from diagnostics. Environment
variables keep supplied credentials out of command arguments.

Exit code 0 means all performed checks passed; 1 means a contract failed; 2 means invalid CLI options. Tests use
`fixtures/addon-contracts.json` and an injected transport, so they need neither listening ports nor upstream
services. Per-repository unit tests remain independent: no CI job downloads an uncommitted copy of this runner.
Valid sealed installs, scoped tickets (including epoch and revocation enforcement), and owner library behavior
are covered by the native service suites. This runner checks operational HTTP behavior without provisioning
those credentials or changing library state.
