#!/usr/bin/env python3
"""Read-only deployment checks, with explicit optional synthetic upload checks.

A successful HTTP/DB smoke is NOT a successful Hancom/editor/real-model test.
Never prints auth tokens or complete HTTP request/response bodies.
"""
import argparse
import errno
import hashlib
from http.cookiejar import CookieJar
import json
import math
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit, quote
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPSHandler
import uuid
from config import read_env


def _connection_unavailable(error):
    if isinstance(error, ssl.SSLError):
        return False
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    return isinstance(error, (ConnectionError, TimeoutError)) or (
        isinstance(error, OSError) and error.errno in {
            errno.ECONNREFUSED, errno.ECONNRESET, errno.ECONNABORTED,
            errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ETIMEDOUT,
        }
    )


def wait_for_health(opener, base, origin, *, timeout=60.0):
    """Wait for the actual proxy route; never replay authentication or writes."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Readiness timeout must be a finite positive number")
    deadline = time.monotonic() + timeout
    last_problem = "no response"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                f"GET api/health: readiness timed out after {timeout:g}s ({last_problem})"
            )
        request = Request(base + "api/health", headers={"Origin": origin}, method="GET")
        try:
            try:
                response = opener.open(request, timeout=min(5.0, remaining))
            except HTTPError as error:
                response = error
            with response:
                if response.status in (502, 503, 504):
                    last_problem = f"HTTP {response.status}"
                elif response.status != 200:
                    raise AssertionError(f"GET api/health: expected 200, received {response.status}")
                else:
                    body = response.read(65537)
                    if len(body) > 65536:
                        raise AssertionError("GET api/health: response exceeded readiness size bound")
                    try:
                        health = json.loads(body)
                    except ValueError:
                        raise AssertionError("GET api/health: expected valid JSON") from None
                    if not isinstance(health, dict) or health.get("ok") is not True:
                        raise AssertionError("GET api/health: expected JSON object with ok=true")
                    return
        except URLError as error:
            if not _connection_unavailable(error.reason):
                raise
            last_problem = "connection unavailable"
        except OSError as error:
            if not _connection_unavailable(error):
                raise
            last_problem = "connection unavailable"
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1.0, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Public /hwp-assistant URL, or local root with --stripped")
    parser.add_argument("--stripped", action="store_true", help="Local root-only transport check; no cookie test")
    parser.add_argument("--env-file", help="Opt in to login/logout checks with one configured user")
    parser.add_argument("--upload-sample", action="store_true", help="Create one synthetic document; does not delete any document")
    parser.add_argument("--receipt", default=".deploy/smoke-receipt.json")
    parser.add_argument("--verify-receipt", action="store_true", help="Read the same synthetic version after restart")
    parser.add_argument("--insecure-localhost", action="store_true", help="Only for the self-signed CI localhost gateway")
    parser.add_argument("--ready-timeout", type=float, default=60.0,
                        help="Maximum seconds to wait for GET api/health through the actual route (default: 60)")
    args = parser.parse_args()
    base = args.url.rstrip("/") + "/"
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Use an HTTP(S) application URL without credentials/query/fragment")
    if not args.stripped and parts.path != "/hwp-assistant/":
        raise ValueError("Expected the public /hwp-assistant/ path")
    if args.stripped and args.env_file:
        raise ValueError("Cookie behavior must be tested at the actual public subpath, not --stripped")
    if args.insecure_localhost and parts.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("TLS verification can only be disabled for the localhost CI gateway")
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    context = ssl._create_unverified_context() if args.insecure_localhost else ssl.create_default_context()
    jar = CookieJar()
    opener = build_opener(HTTPSHandler(context=context), HTTPCookieProcessor(jar))

    def request(path, *, method="GET", data=None, headers=None, expected=200):
        req_headers = {"Origin": origin, **(headers or {})}
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            req_headers["Content-Type"] = "application/json"
        req = Request(base + path.lstrip("/"), data=data, headers=req_headers, method=method)
        try:
            response = opener.open(req, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            if response.status != expected:
                raise AssertionError(f"{method} {path}: expected {expected}, received {response.status}")
            body = response.read(50 * 1024 * 1024 + 1)
            if len(body) > 50 * 1024 * 1024:
                raise AssertionError("Response exceeded smoke-test size bound")
            return body, response.headers

    def public_asset(url):
        prefix = "/hwp-assistant/"
        if not url.startswith(prefix) or ".." in urlsplit(url).path.split("/"):
            raise AssertionError("Asset URL escaped /hwp-assistant/: " + url)
        return request(url[len(prefix):])

    wait_for_health(opener, base, origin, timeout=args.ready_timeout)
    assert json.loads(request("api/auth/status")[0]).get("authentication_required") is True
    request("api/documents", expected=401)
    for page in ("", "rhwp/index.html", "compare-renderer.html"):
        html, headers = request(page)
        assert "text/html" in headers.get("Content-Type", ""), page
        assets = re.findall(r'(?:src|href)="([^"#]+)"', html.decode())
        for asset in assets:
            if asset.startswith("data:"):
                continue
            body, asset_headers = public_asset(asset)
            assert body, asset
            if urlsplit(asset).path.endswith((".js", ".css", ".wasm")):
                assert "text/html" not in asset_headers.get("Content-Type", ""), asset
    manifest = json.loads(request("comparison-engine.json")[0])
    wasm, headers = public_asset(manifest["wasm"])
    assert hashlib.sha256(wasm).hexdigest() == manifest["wasm_sha256"], "Comparison WASM integrity"
    assert "javascript" in request("comparison-core.js")[1].get("Content-Type", "")
    print("PASS: routing, protected API, app/editor/comparison assets and WASM integrity")

    if not args.env_file:
        if args.upload_sample or args.verify_receipt:
            raise ValueError("Data checks require --env-file and the public HTTPS subpath")
        print("Login/cookie/data-write checks were not requested.")
        return
    users = json.loads(read_env(args.env_file)["HWP_AUTH_TOKENS"])
    user, token = next(iter(users.items()))
    request("api/auth/login", method="POST", data={"token": token},
            headers={"Origin": "https://not-this-service.invalid"}, expected=403)
    _, headers = request("api/auth/login", method="POST", data={"token": token})
    cookie = headers.get("Set-Cookie", "")
    assert "Path=/hwp-assistant/api" in cookie, "Public cookie path"
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie, "Cookie flags"
    if parts.scheme == "https":
        assert "Secure" in cookie, "HTTPS proxy scheme / secure cookie"
    assert json.loads(request("api/auth/me")[0])["user_id"] == user
    request("api/documents")
    receipt_path = Path(args.receipt)
    if args.upload_sample:
        sample = Path(__file__).resolve().parents[2] / "samples/paragraph-and-table.hwpx"
        content = sample.read_bytes()
        document_id = str(uuid.uuid4())
        name = quote("deployment-smoke-synthetic.hwpx")
        body, _ = request(f"api/documents?name={name}&document_id={document_id}",
                          method="POST", data=content, headers={"Idempotency-Key": str(uuid.uuid4())})
        document = json.loads(body)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps({"id": document["id"], "version": document["current_version"],
                                           "sha256": hashlib.sha256(content).hexdigest()}) + "\n")
        print("PASS: one synthetic HWPX was persisted; receipt contains identifiers only")
    if args.upload_sample or args.verify_receipt:
        receipt = json.loads(receipt_path.read_text())
        body, _ = request(f"api/documents/{receipt['id']}/versions/{receipt['version']}/file")
        assert hashlib.sha256(body).hexdigest() == receipt["sha256"], "Persisted HWPX hash"
        print("PASS: stored HWPX matches the recorded bytes")
    request("api/auth/logout", method="POST")
    request("api/documents", expected=401)
    print("PASS: same-origin protection, cookie path, login and logout")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Deployment smoke FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
