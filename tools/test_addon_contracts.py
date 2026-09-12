"""Offline runner tests: fixture responses, no bound ports, upstreams, or model files."""

import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

import addon_contracts as contracts


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "addon-contracts.json").read_text())


def response(fixture, status=200, headers=None):
    return contracts.Response(status, {"content-type": "application/json", "cache-control": "no-store",
                                       **(headers or {})}, json.dumps(FIXTURES[fixture]).encode())


class Service:
    def __init__(self, *, degraded=False, sealed=False):
        self.degraded = degraded
        self.sealed = sealed
        self.calls = []

    def __call__(self, method, path, headers=None, body=None):
        self.calls.append((method, path, headers, body))
        authorization = (headers or {}).get("Authorization", "")
        if path == "/health":
            if method == "PUT":
                return response("method_not_allowed", 405)
            result = response("degraded" if self.degraded else "health")
            if method == "HEAD":
                result.body = b""
            return result
        if path == "/metrics":
            if authorization.startswith("Bearer ") and authorization[7:].strip() == "metrics-secret":
                return contracts.Response(200, {"content-type": "text/plain; version=0.0.4",
                                                "cache-control": "no-store"}, b"# TYPE fixture gauge\nfixture 1\n")
            return response("not_found", 404)
        if path == "/config-key":
            return response("sealed_key" if self.sealed else "plaintext_key", 200 if self.sealed else 404,
                            {"cache-control": "public, max-age=300"} if self.sealed else {})
        if path.endswith("/manifest.json"):
            return response("bad_config", 400)
        if path.startswith("/lib/"):
            return response("missing_token", 401)
        if path == "/remux/login":
            if body == {"key": "browser-secret"}:
                return contracts.Response(204, {
                    "cache-control": "no-store", "x-den-browser-token": "signed-token",
                    "set-cookie": "den_remux=signed-cookie; Path=/remux; Max-Age=3600; HttpOnly; Secure; SameSite=Strict",
                }, b"")
            return response("bad_key", 401)
        if path == "/remux/releases":
            # The suite must never send a real title that could reach an addon.
            assert body == {"imdb": "not-an-imdb"}
            if (authorization == "Bearer signed-token"
                    or (not authorization and (headers or {}).get("Cookie") == "den_remux=signed-cookie")):
                return response("bad_request", 400)
            return response("not_logged_in", 401)
        return response("not_found", 404)


class ContractsTest(unittest.TestCase):
    def test_every_profile_and_credential_mode(self):
        for service, profile in contracts.PROFILES.items():
            for mode in (("plaintext", "sealed") if profile.sealed_config else ("skip",)):
                with self.subTest(service=service, mode=mode):
                    transport = Service(sealed=mode == "sealed")
                    results = contracts.run_contracts(
                        service, transport, metrics_token="metrics-secret", config_mode=mode,
                        browser_key="browser-secret" if profile.browser_auth else None)
                    self.assertFalse([row for row in results if row[1]], results)
                    self.assertIn(("GET", profile.unknown_path, None, None), transport.calls)
                    self.assertNotIn("/inbox/drain", [call[1] for call in transport.calls])

    def test_degraded_health_is_valid_unless_ok_is_required(self):
        self.assertFalse(any(failure for _, failure in contracts.run_contracts("embed", Service(degraded=True))))
        results = contracts.run_contracts("embed", Service(degraded=True), require_health_ok=True)
        self.assertEqual([label for label, error in results if error], ["health"])

    def test_browser_checks_cookie_and_tampered_bearer(self):
        transport = Service()
        results = contracts.run_contracts("remux", transport, browser_key="browser-secret")
        self.assertFalse(any(error for _, error in results), results)
        release_headers = [headers for _, path, headers, _ in transport.calls if path == "/remux/releases"]
        self.assertIn({"Authorization": "Bearer signed-token"}, release_headers)
        self.assertIn({"Cookie": "den_remux=signed-cookie"}, release_headers)
        self.assertIn({"Authorization": "Bearer Aigned-token"}, release_headers)

    def test_browser_cookie_flags_and_auth_regressions_fail(self):
        for broken in ("cookie_missing", "HttpOnly", "Secure", "SameSite=Strict", "cookie_admission", "tampering"):
            def request(method, path, headers=None, body=None):
                result = Service()(method, path, headers, body)
                if path == "/remux/login" and result.status == 204:
                    if broken == "cookie_missing":
                        result.headers.pop("set-cookie")
                    elif broken in ("HttpOnly", "Secure", "SameSite=Strict"):
                        result.headers["set-cookie"] = result.headers["set-cookie"].replace("; " + broken, "")
                elif path == "/remux/releases":
                    if broken == "cookie_admission" and (headers or {}).get("Cookie"):
                        return response("not_logged_in", 401)
                    if broken == "tampering" and (headers or {}).get("Authorization") == "Bearer Aigned-token":
                        return response("bad_request", 400)
                return result
            with self.subTest(broken=broken):
                results = contracts.run_contracts("remux", request, browser_key="browser-secret")
                self.assertEqual([label for label, error in results if error],
                                 ["browser login, cookie and bearer admission"])

    def test_missing_no_store_wrong_json_and_credentials_fail(self):
        for broken in ("cache", "html", "auth", "shape"):
            def request(method, path, headers=None, body=None):
                result = Service()(method, path, headers, body)
                if broken == "cache":
                    result.headers.pop("cache-control", None)
                elif broken == "html" and path == contracts.PROFILES["edge"].unknown_path:
                    result = contracts.Response(200, {"content-type": "text/html"}, b"app shell")
                elif broken == "auth" and path == "/metrics":
                    result.status = 200
                elif broken == "shape" and method == "GET" and path == "/health":
                    result.body = b'{"status":[]}'
                return result
            with self.subTest(broken=broken):
                self.assertTrue(any(error for _, error in contracts.run_contracts("edge", request)))

    def test_http_errors_are_inspected_and_redirects_not_followed(self):
        transport = contracts.HTTP("http://127.0.0.1:8080")
        error = urllib.error.HTTPError(transport.base + "/missing", 404, "not found",
                                      {"Content-Type": "application/json", "Cache-Control": "no-store"},
                                      io.BytesIO(b'{"error":"not_found"}'))
        with patch.object(transport.opener, "open", side_effect=error):
            contracts.error_response(transport("GET", "/missing"), 404, "not_found")
        self.assertIsNone(contracts.NoRedirects().redirect_request(None, None, 302, "", {}, "https://elsewhere"))

    def test_transport_failure_does_not_disclose_credentials(self):
        transport = contracts.HTTP("http://127.0.0.1:8080")
        with patch.object(transport.opener, "open", side_effect=urllib.error.URLError("metrics-secret")):
            with self.assertRaisesRegex(contracts.ContractError, "^request could not complete$"):
                transport("GET", "/metrics", {"Authorization": "Bearer metrics-secret"})


if __name__ == "__main__":
    unittest.main()
