"""Fail-closed entrypoint for the single-host public deployment only.

Native development still runs app.main:app. Forwarded headers are trusted here
because compose.deploy.yaml publishes NO API port and uses dedicated networks.
Do not reuse this entrypoint for a directly internet-exposed Uvicorn listener.
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


def validate_environment(env):
    """Validate without logging credentials or importing the application."""
    try:
        users = json.loads(env.get("HWP_AUTH_TOKENS", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Production requires HWP_AUTH_TOKENS; local mode is forbidden") from exc
    if not isinstance(users, dict) or not users:
        raise ValueError("Production requires a nonempty user-to-token object")
    if any(not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", user)
           or not isinstance(token, str) or len(token) < 32
           or token.startswith("hwp-run-") for user, token in users.items()):
        raise ValueError("Use valid user IDs and unique random tokens of at least 32 characters")
    if len(set(users.values())) != len(users):
        raise ValueError("Each production user needs a distinct token")
    try:
        url = urlsplit(env.get("DATABASE_URL", ""))
        password = url.password or ""
    except ValueError as exc:
        raise ValueError("Invalid production database configuration") from exc
    if url.scheme != "postgresql+psycopg" or url.hostname != "db" or not url.path.strip("/"):
        raise ValueError("Production expects the Compose PostgreSQL service db")
    # A generated hex password is safely interpolated into the connection URL.
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", password):
        raise ValueError("Use a generated URL-safe DB password (32+ characters)")
    if env.get("STORAGE_DIR") != "/data/files":
        raise ValueError("Production requires the persistent /data/files mount")
    from .host_validation import allowed_hosts
    if not allowed_hosts(env.get("HWP_ALLOWED_HOSTS", "")):
        raise ValueError("Production requires an explicit HWP_ALLOWED_HOSTS allowlist")
    return users


def readiness():
    """HTTP readiness, DB availability and mounted storage; no data mutation."""
    import urllib.request
    from sqlalchemy import create_engine, text
    validate_environment(os.environ)
    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3) as response:
        if json.load(response).get("ok") is not True:
            raise RuntimeError("API is not ready")
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()
    folder = Path(os.environ["STORAGE_DIR"])
    if not folder.is_dir() or not os.access(folder, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError("Document storage is not writable")


def main():
    validate_environment(os.environ)
    from .auth import credentials
    credentials()  # Keep the application's own credential contract as well.
    import uvicorn
    # app.main's lifespan performs the existing additive initialization once.
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1,
                proxy_headers=True, forwarded_allow_ips="*", access_log=False)


if __name__ == "__main__":
    try:
        readiness() if sys.argv[1:] == ["--check"] else main()
    except Exception:
        # Connection URLs and token JSON must not appear in healthcheck logs.
        print("Production configuration/readiness failed; check deployment settings.", file=sys.stderr)
        sys.exit(1)
