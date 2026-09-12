#!/usr/bin/env python3
"""Check Den's operational HTTP contracts against an already running service.

Standard library only. No content lookup, inference, pairing, drain, or playback is requested.
Credentials and response bodies are never included in diagnostics.
"""

import argparse
import base64
from dataclasses import dataclass
import http.client
from http.cookies import CookieError, SimpleCookie
import json
import os
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class Profile:
    unknown_path: str = "/__den_contract_unknown__"
    sealed_config: bool = False
    browser_auth: bool = False
    library_auth: bool = False


PROFILES = {
    "atlas": Profile(),
    "scout": Profile(sealed_config=True),
    "reel": Profile(sealed_config=True),
    "remux": Profile(unknown_path="/remux/__den_contract_unknown__", browser_auth=True),
    "subtitles": Profile(sealed_config=True),
    "embed": Profile(),
    # Unknown extensionless page URLs intentionally return the SPA shell.
    "edge": Profile(unknown_path="/inbox/__den_contract_unknown__", library_auth=True),
}


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class ContractError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self, base, timeout=5, host=None):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.host = host
        # Service profiles name direct service origins. An ambient proxy must not receive credentials.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())

    def __call__(self, method, path, headers=None, body=None):
        headers = dict(headers or {})
        if self.host:
            headers["Host"] = self.host
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            answer = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            answer = error  # 4xx/5xx bodies are part of the contract, not transport failures.
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
            raise ContractError("request could not complete") from None
        with answer:
            try:
                content = answer.read(1024 * 1024 + 1)
            except (OSError, http.client.HTTPException, ValueError):
                raise ContractError("response body could not be read") from None
            require(len(content) <= 1024 * 1024, "operational response exceeded 1 MiB")
            return Response(answer.code, {k.lower(): v for k, v in answer.headers.items()}, content)


def no_store(response):
    directives = {p.strip().lower() for p in response.headers.get("cache-control", "").split(",")}
    require("no-store" in directives, "missing Cache-Control: no-store")


def json_response(response, status, *, uncached=True):
    require(response.status == status, f"expected HTTP {status}, got {response.status}")
    require(response.headers.get("content-type", "").lower().split(";")[0].strip() == "application/json",
            "response is not application/json")
    if uncached:
        no_store(response)
    try:
        value = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        raise ContractError("body is not valid JSON") from None
    require(isinstance(value, dict), "JSON body is not an object")
    return value


def error_response(response, status, code):
    require(json_response(response, status).get("error") == code, f"expected error code {code}")


def run_contracts(service, request, *, metrics_token=None, browser_key=None,
                  config_mode="skip", require_health_ok=False):
    """Return (label, failure-or-None) checks. `request` is injectable for offline fixtures."""
    profile = PROFILES[service]
    results = []

    def check(label, action):
        try:
            action()
            results.append((label, None))
        except ContractError as error:
            results.append((label, str(error)))

    def health():
        body = json_response(request("GET", "/health"), 200)
        require(body.get("status") in ("ok", "degraded"), "health status is neither ok nor degraded")
        if body["status"] == "degraded":
            for field in ("reason", "detail"):
                require(isinstance(body.get(field), str) and bool(body[field]), f"degraded health lacks {field}")
        if require_health_ok:
            require(body["status"] == "ok", "health reports degraded")

    def head_health():
        response = request("HEAD", "/health")
        require(response.status == 200, f"expected HTTP 200, got {response.status}")
        no_store(response)
        require(not response.body, "HEAD returned a body")

    check("health", health)
    check("health HEAD", head_health)
    check("unknown API route", lambda: error_response(request("GET", profile.unknown_path), 404, "not_found"))
    check("unsupported method", lambda: error_response(request("PUT", "/health"), 405, "method_not_allowed"))
    check("metrics without authorization", lambda: error_response(request("GET", "/metrics"), 404, "not_found"))
    wrong = "den-contract-wrong-token"
    if wrong == metrics_token:
        wrong += "-different"
    check("metrics wrong token", lambda: error_response(
        request("GET", "/metrics", {"Authorization": "Bearer " + wrong}), 404, "not_found"))
    check("metrics requires Bearer", lambda: error_response(
        request("GET", "/metrics", {"Authorization": metrics_token or wrong}), 404, "not_found"))

    if metrics_token:
        def authorized_metrics():
            response = request("GET", "/metrics", {"Authorization": "Bearer   " + metrics_token + "  "})
            require(response.status == 200, f"expected HTTP 200, got {response.status}")
            no_store(response)
            require(response.headers.get("content-type", "").lower().startswith("text/plain"),
                    "metrics content type is not text/plain")
            require(b"# TYPE " in response.body, "metrics has no Prometheus type declarations")
        check("metrics correct token", authorized_metrics)

    if profile.sealed_config and config_mode != "skip":
        def config_key():
            response = request("GET", "/config-key")
            body = json_response(response, 200 if config_mode == "sealed" else 404,
                                 uncached=config_mode != "sealed")
            require(type(body.get("epoch")) is int and body["epoch"] >= 0, "config epoch is not nonnegative")
            if config_mode == "sealed":
                try:
                    key = base64.b64decode(body.get("key", ""), validate=True)
                except (ValueError, TypeError):
                    key = b""
                require(len(key) == 32, "config key is not a base64 32-byte public key")
            else:
                require(body.get("error") == "no_key", "plaintext mode must report no_key")
        check("configured sealing mode", config_key)
        check("malformed config", lambda: error_response(
            request("GET", "/__den_contract_bad_config__/manifest.json"), 400, "bad_config"))

    if profile.library_auth:
        check("library requires token", lambda: error_response(
            request("GET", "/lib/0000000000000000/changes"), 401, "missing_token"))

    if profile.browser_auth:
        # This intentionally invalid title can never reach Scout, even after successful authentication.
        title = {"imdb": "not-an-imdb"}
        check("releases requires credential", lambda: error_response(
            request("POST", "/remux/releases", body=title), 401, "not_logged_in"))
        wrong_key = "den-contract-wrong-browser-key"
        if wrong_key == browser_key:
            wrong_key += "-different"
        check("browser wrong key", lambda: error_response(
            request("POST", "/remux/login", body={"key": wrong_key}), 401, "bad_key"))
        if browser_key:
            def browser_login():
                response = request("POST", "/remux/login", body={"key": browser_key})
                require(response.status == 204, f"expected HTTP 204, got {response.status}")
                no_store(response)
                require(not response.body, "login 204 returned a body")
                token = response.headers.get("x-den-browser-token")
                require(bool(token), "login did not return X-Den-Browser-Token")
                cookies = SimpleCookie()
                try:
                    cookies.load(response.headers.get("set-cookie", ""))
                except CookieError:
                    raise ContractError("login returned an invalid cookie") from None
                cookie = cookies.get("den_remux")
                require(cookie is not None and bool(cookie.value), "login did not return den_remux cookie")
                require(bool(cookie["httponly"]), "login cookie lacks HttpOnly")
                require(bool(cookie["secure"]), "login cookie lacks Secure")
                require(cookie["samesite"].lower() == "strict", "login cookie lacks SameSite=Strict")
                error_response(request("POST", "/remux/releases", {"Authorization": "Bearer " + token}, title),
                               400, "bad_request")
                error_response(request("POST", "/remux/releases", {"Cookie": cookie.OutputString(attrs=[])}, title),
                               400, "bad_request")
                # Mutate the first byte, avoiding insignificant trailing base64 padding bits.
                tampered = ("A" if token[0] != "A" else "B") + token[1:]
                error_response(request("POST", "/remux/releases", {"Authorization": "Bearer " + tampered}, title),
                               401, "not_logged_in")
            check("browser login, cookie and bearer admission", browser_login)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, choices=PROFILES)
    parser.add_argument("--base", required=True, help="direct service origin, e.g. http://127.0.0.1:8080")
    parser.add_argument("--metrics-token", default=os.environ.get("DEN_CONTRACT_METRICS_TOKEN"))
    parser.add_argument("--browser-key", default=os.environ.get("DEN_CONTRACT_BROWSER_KEY"))
    parser.add_argument("--config-mode", choices=("skip", "plaintext", "sealed"), default="skip")
    parser.add_argument("--require-health-ok", action="store_true")
    parser.add_argument("--host", help="Host header for a locally running Edge with configured API_HOSTS")
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args(argv)
    base = urllib.parse.urlsplit(args.base)
    if (base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password
            or base.query or base.fragment or base.path not in {"", "/"}):
        parser.error("--base must be an HTTP(S) service origin without credentials, path, query, or fragment")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.config_mode != "skip" and not PROFILES[args.service].sealed_config:
        parser.error("--config-mode applies only to scout, reel, and subtitles")
    if args.browser_key and not PROFILES[args.service].browser_auth:
        parser.error("--browser-key applies only to remux")
    results = run_contracts(args.service, HTTP(args.base, args.timeout, args.host),
                            metrics_token=args.metrics_token, browser_key=args.browser_key,
                            config_mode=args.config_mode, require_health_ok=args.require_health_ok)
    for label, failure in results:
        print(f"{'FAIL' if failure else 'PASS'} {args.service}: {label}" + (f" — {failure}" if failure else ""))
    if not args.metrics_token:
        print("SKIP metrics correct token: no token supplied")
    if PROFILES[args.service].browser_auth and not args.browser_key:
        print("SKIP browser login: no browser key supplied")
    return int(any(failure for _, failure in results))


if __name__ == "__main__":
    raise SystemExit(main())
