"""Offline regression checks for read-only readiness and once-only smoke writes."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parent))
import smoke

BASE = "https://localhost:24443/hwp-assistant/"
ORIGIN = "https://localhost:24443"


class Response(io.BytesIO):
    def __init__(self, status=200, body=b'{"ok": true}', headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.monotonic = patch.object(smoke.time, "monotonic", self.clock.monotonic)
        self.sleep = patch.object(smoke.time, "sleep", self.clock.sleep)
        self.monotonic.start()
        self.sleep.start()
        self.addCleanup(self.monotonic.stop)
        self.addCleanup(self.sleep.stop)

    def test_temporary_gateway_errors_recover_using_only_public_health_get(self):
        responses = [Response(502), Response(503), Response(504), Response()]
        opener = Mock()
        opener.open.side_effect = responses
        smoke.wait_for_health(opener, BASE, ORIGIN)
        self.assertEqual(opener.open.call_count, 4)
        self.assertTrue(all(response.closed for response in responses))
        for call in opener.open.call_args_list:
            request = call.args[0]
            self.assertEqual(request.full_url, BASE + "api/health")
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.data)
            self.assertEqual(dict(request.header_items()), {"Origin": ORIGIN})
            self.assertEqual(call.kwargs["timeout"], 5.0)
        self.assertEqual(self.clock.sleeps, [1.0, 1.0, 1.0])

    def test_permanent_gateway_error_reaches_deadline_without_exposing_body(self):
        secret = b"synthetic-private-response-must-not-be-logged"
        opener = Mock()
        opener.open.side_effect = lambda *args, **kwargs: HTTPError(
            BASE + "api/health", 502, "Bad Gateway", {}, io.BytesIO(secret)
        )
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            with self.assertRaisesRegex(AssertionError, r"timed out after 2.5s \(HTTP 502\)") as raised:
                smoke.wait_for_health(opener, BASE, ORIGIN, timeout=2.5)
        self.assertEqual(self.clock.now, 2.5)
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual([call.kwargs["timeout"] for call in opener.open.call_args_list], [2.5, 1.5, 0.5])
        self.assertNotIn(secret.decode(), str(raised.exception) + output.getvalue())

    def test_network_attempt_time_is_part_of_deadline(self):
        opener = Mock()

        def unavailable(request, *, timeout):
            self.clock.now += timeout
            raise TimeoutError("synthetic timeout")

        opener.open.side_effect = unavailable
        with self.assertRaisesRegex(AssertionError, "connection unavailable"):
            smoke.wait_for_health(opener, BASE, ORIGIN, timeout=2.5)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(self.clock.now, 2.5)
        self.assertEqual(self.clock.sleeps, [])

    def test_connection_refused_then_ready(self):
        opener = Mock()
        opener.open.side_effect = [URLError(ConnectionRefusedError()), Response()]
        smoke.wait_for_health(opener, BASE, ORIGIN)
        self.assertEqual(opener.open.call_count, 2)

    def test_temporary_dns_then_ready(self):
        opener = Mock()
        opener.open.side_effect = [URLError(socket.gaierror(socket.EAI_AGAIN, "temporary DNS")), Response()]
        smoke.wait_for_health(opener, BASE, ORIGIN)
        self.assertEqual(opener.open.call_count, 2)

    def test_wrong_route_auth_and_other_http_errors_fail_immediately(self):
        for status in (301, 400, 401, 403, 404, 429, 500):
            with self.subTest(status=status):
                opener = Mock()
                opener.open.side_effect = HTTPError(
                    BASE + "api/health", status, "synthetic status", {}, io.BytesIO(b"private body")
                )
                with self.assertRaisesRegex(AssertionError, f"received {status}") as raised:
                    smoke.wait_for_health(opener, BASE, ORIGIN)
                self.assertNotIn("private body", str(raised.exception))
                self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_malformed_or_unhealthy_json_fails_immediately(self):
        for body in (b"<html>private fallback page</html>", b'{"ok": false}', b'{"ok": 1}', b"[]", b"null", b"\xff"):
            with self.subTest(body=body):
                opener = Mock()
                opener.open.return_value = Response(body=body)
                with self.assertRaisesRegex(AssertionError, "GET api/health") as raised:
                    smoke.wait_for_health(opener, BASE, ORIGIN)
                self.assertNotIn("private fallback", str(raised.exception))
                self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_readiness_body_is_bounded(self):
        opener = Mock()
        opener.open.return_value = Response(body=b"x" * 65537)
        with self.assertRaisesRegex(AssertionError, "size bound"):
            smoke.wait_for_health(opener, BASE, ORIGIN)
        self.assertEqual(opener.open.call_count, 1)

    def test_tls_and_permanent_dns_errors_are_not_retried(self):
        errors = [ssl.SSLCertVerificationError("untrusted certificate"),
                  URLError(ssl.SSLCertVerificationError("untrusted certificate")),
                  URLError(socket.gaierror(socket.EAI_NONAME, "unknown host"))]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                opener = Mock()
                opener.open.side_effect = error
                with self.assertRaises(type(error)):
                    smoke.wait_for_health(opener, BASE, ORIGIN)
                self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_invalid_deadlines_fail_without_network_access(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout):
                opener = Mock()
                with self.assertRaises(ValueError):
                    smoke.wait_for_health(opener, BASE, ORIGIN, timeout=timeout)
                opener.open.assert_not_called()


class SmokeOpener:
    """Synthetic protocol fixture, never an assertion of real Docker readiness."""
    def __init__(self, fail_write=None):
        self.calls = []
        self.authenticated = False
        self.health_attempts = 0
        self.fail_write = fail_write
        self.wasm = b"synthetic WASM fixture"
        self.document = (Path(__file__).resolve().parents[2] / "samples/paragraph-and-table.hwpx").read_bytes()

    def open(self, request, *, timeout):
        path = urlsplit(request.full_url).path.removeprefix("/hwp-assistant/")
        method = request.get_method()
        self.calls.append((method, path, request))
        if path == "api/health":
            self.health_attempts += 1
            return Response(502) if self.health_attempts == 1 else Response()
        if path == "api/auth/status":
            return Response(body=b'{"authentication_required": true}')
        if path == "api/documents" and method == "GET":
            return Response(200 if self.authenticated else 401, b"[]")
        if path in ("", "rhwp/index.html", "compare-renderer.html"):
            return Response(body=b"<html></html>", headers={"Content-Type": "text/html"})
        if path == "comparison-engine.json":
            return Response(body=json.dumps({"wasm": "/hwp-assistant/assets/core-abcdefgh.wasm",
                                             "wasm_sha256": hashlib.sha256(self.wasm).hexdigest()}).encode())
        if path == "assets/core-abcdefgh.wasm":
            return Response(body=self.wasm)
        if path == "comparison-core.js":
            return Response(body=b"export {};", headers={"Content-Type": "application/javascript"})
        if path == "api/auth/login":
            if request.get_header("Origin") != ORIGIN:
                return Response(403)
            if self.fail_write == path:
                return Response(502, b"private write error")
            self.authenticated = True
            return Response(headers={"Set-Cookie": "session=synthetic; Path=/hwp-assistant/api; HttpOnly; SameSite=strict; Secure"})
        if path == "api/auth/me":
            return Response(body=b'{"user_id": "synthetic-owner"}')
        if path == "api/documents" and method == "POST":
            if self.fail_write == path:
                return Response(502, b"private write error")
            return Response(body=b'{"id": "synthetic-document", "current_version": "synthetic-version"}')
        if path == "api/documents/synthetic-document/versions/synthetic-version/file":
            return Response(body=self.document)
        if path == "api/auth/logout":
            if self.fail_write == path:
                return Response(502, b"private write error")
            self.authenticated = False
            return Response()
        raise AssertionError(f"Unexpected synthetic request: {method} {path}")


class OnceOnlySmokeTests(unittest.TestCase):
    def run_smoke(self, opener, receipt):
        clock = Clock()
        args = ["smoke.py", "--url", BASE, "--env-file", "synthetic.env",
                "--upload-sample", "--receipt", str(receipt)]
        with patch.object(sys, "argv", args), patch.object(smoke, "build_opener", return_value=opener), \
                patch.object(smoke, "read_env", return_value={"HWP_AUTH_TOKENS": '{"synthetic-owner":"synthetic-secret"}'}), \
                patch.object(smoke.time, "monotonic", clock.monotonic), patch.object(smoke.time, "sleep", clock.sleep):
            smoke.main()

    def test_readiness_retries_do_not_replay_login_upload_or_receipt_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp) / "receipt.json"
            opener = SmokeOpener()
            output = io.StringIO()
            with redirect_stdout(output):
                self.run_smoke(opener, receipt)
            self.assertEqual(opener.health_attempts, 2)
            calls = [(method, path) for method, path, _ in opener.calls]
            self.assertEqual(calls.count(("POST", "api/auth/login")), 2)  # Rejected foreign origin, then one valid login.
            self.assertEqual(calls.count(("POST", "api/documents")), 1)
            self.assertEqual(calls.count(("POST", "api/auth/logout")), 1)
            self.assertEqual(calls.count(("GET", "api/documents/synthetic-document/versions/synthetic-version/file")), 1)
            self.assertEqual(json.loads(receipt.read_text())["sha256"], hashlib.sha256(opener.document).hexdigest())
            self.assertNotIn("synthetic-secret", output.getvalue() + receipt.read_text())
            self.assertIn("stored HWPX matches the recorded bytes", output.getvalue())

    def test_transient_status_during_mutation_is_never_retried(self):
        for path in ("api/auth/login", "api/documents", "api/auth/logout"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as temp:
                opener = SmokeOpener(fail_write=path)
                with redirect_stdout(io.StringIO()), self.assertRaisesRegex(AssertionError, "received 502") as raised:
                    self.run_smoke(opener, Path(temp) / "receipt.json")
                writes = [request for method, actual, request in opener.calls if method == "POST" and actual == path
                          and request.get_header("Origin") == ORIGIN]
                self.assertEqual(len(writes), 1)
                self.assertNotIn("private write error", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
