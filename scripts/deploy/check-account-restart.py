#!/usr/bin/env python3
"""Real disposable HTTPS restart check; private synthetic receipt, never a model call."""
import hashlib
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import ssl
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

BASE = "https://localhost:24443/hwp-assistant/"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    assert os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    path = Path(os.environ["HWP_ACCOUNT_RECEIPT"])
    try:
        assert path.stat().st_mode & 0o077 == 0
        receipt = json.loads(path.read_text())
        assert receipt.get("approval_required") is True and receipt.get("approved") is True
        context = ssl.create_default_context(cafile=os.environ["HWP_CI_CERT_DIR"] + "/cert.pem")
        opener = build_opener(HTTPSHandler(context=context), NoRedirect())

        def request(relative, cookie="", body=None):
            url = urljoin(BASE, relative)
            parsed = urlsplit(url)
            assert (parsed.scheme, parsed.netloc) == ("https", "localhost:24443")
            assert parsed.path.startswith("/hwp-assistant/api/")
            headers = {"Origin": "https://localhost:24443"}
            if cookie:
                headers["Cookie"] = "hwp_session=" + cookie
            if body is not None:
                headers["Content-Type"] = "application/json"
            req = Request(url, headers=headers, data=None if body is None else json.dumps(body).encode())
            try:
                with opener.open(req, timeout=15) as response:
                    return response.status, response.read(), response.headers
            except HTTPError as exc:
                with exc:
                    return exc.code, exc.read(), exc.headers

        def me(cookie, user):
            status, raw, _ = request("api/auth/me", cookie)
            assert status == 200 and json.loads(raw)["user_id"] == user
            identity = json.loads(raw)
            assert identity["library_access_mode"] == "private" and identity["is_admin"] is False
            assert request("api/admin/accounts", cookie)[0] == 403

        def file_matches(cookie, document):
            status, raw, _ = request(document["href"], cookie)
            assert status == 200 and hashlib.sha256(raw).hexdigest() == document["sha256"]

        def isolated(cookie, forbidden):
            assert request(forbidden["href"], cookie)[0] == 404

        # The approved member's pre-restart session must still work, and its actual saved
        # bytes (not merely an identifier or a 200 response) must be unchanged.
        me(receipt["cookie"], receipt["user"])
        file_matches(receipt["cookie"], receipt["member"])
        isolated(receipt["cookie"], receipt["guest"])

        # New password logins exercise persisted guest and member hashes too.
        # These are CI-generated identities, never production credentials.
        from config import read_env
        password = read_env(os.environ["HWP_ENV_FILE"])["HWP_GUEST_PASSWORD"]
        for user, own, other in (("guest", receipt["guest"], receipt["member"]),
                                 (receipt["user"], receipt["member"], receipt["guest"])):
            status, raw, headers = request("api/auth/password-login", body={"username": user, "password": password})
            assert status == 200 and json.loads(raw)["user_id"] == user
            cookies = SimpleCookie()
            cookies.load(headers.get("Set-Cookie", ""))
            cookie = cookies["hwp_session"].value
            me(cookie, user)
            file_matches(cookie, own)
            isolated(cookie, other)
            assert request("api/auth/logout", cookie, {})[0] == 200
            assert request("api/auth/me", cookie)[0] == 401
    finally:
        path.unlink(missing_ok=True)
    print("PASS: restart preserves approved member and guest login, member session and HWPX hashes; non-admin boundary, bidirectional isolation and logout verified")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # No credential, response body or SQL value is printed even on failure.
        raise SystemExit("FAIL: account restart, stored bytes or isolation check; private receipt removed") from None
