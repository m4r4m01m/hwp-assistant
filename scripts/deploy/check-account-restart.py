#!/usr/bin/env python3
"""Private synthetic CI receipt; no credentials in output or uploaded evidence."""
import hashlib
import json
import os
import ssl
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pathlib import Path

base = "https://localhost:24443/hwp-assistant/"
assert os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
path = Path(os.environ["HWP_ACCOUNT_RECEIPT"])
receipt = json.loads(path.read_text())
context = ssl.create_default_context(cafile=os.environ["HWP_CI_CERT_DIR"] + "/cert.pem")
request = Request(base + "api/auth/me", headers={"Cookie": "hwp_session=" + receipt["cookie"]})
with urlopen(request, context=context, timeout=10) as response:
    assert json.load(response)["user_id"] == receipt["user"]
# A cookie remains private even through restart; no cross-account document read.
try:
    urlopen(Request("https://localhost:24443" + receipt["href"], headers={"Cookie": "hwp_session=" + receipt["cookie"]}), context=context, timeout=10)
    raise AssertionError("unexpected cross-account read")
except HTTPError as exc:
    assert exc.code == 404
path.unlink()
print("PASS: password session and document isolation survive API restart; no receipt secrets retained")
