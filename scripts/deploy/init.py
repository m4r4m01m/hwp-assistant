#!/usr/bin/env python3
"""Explicit first-time setup. Never overwrite credentials or silently reuse data."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from config import DEFAULT_ALLOWED_HOSTS

VOLUMES = ("hwp-assistant-prod-metadata", "hwp-assistant-prod-documents", "hwp-assistant-prod-codex-state")


def settings(owner):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,80}", owner):
        raise ValueError("Owner ID must contain 1-80 letters, digits, _, ., @ or -")
    return "\n".join([
        "# PRIVATE. Keep mode 600; never commit or paste this file into chat/logs.",
        "POSTGRES_PASSWORD=" + secrets.token_hex(32),
        "HWP_AUTH_TOKENS='" + json.dumps({owner: secrets.token_urlsafe(36)}) + "'",
        "HWP_LEGACY_OWNER=",
        "HWP_ALLOWED_HOSTS=" + DEFAULT_ALLOWED_HOSTS,
        "HWP_AUTH_SESSION_TTL_SECONDS=28800",
        "HWP_AUTH_SESSION_IDLE_SECONDS=1800",
        "HWP_AUTH_SESSION_MAX_PER_USER=20",
        "HWP_AI_REQUESTS_PER_MINUTE=20",
        "HWP_LLM_ALLOWED_USERS=" + owner,
        "HWP_IMAGE_PREFIX=ghcr.io/m4r4m01m/hwp-assistant",
        "HWP_IMAGE_TAG=",
        "LLM_PROVIDER=", "LLM_BASE_URL=", "LLM_API_KEY=", "LLM_MODEL=",
        "LLM_TIMEOUT_SECONDS=60", "HWP_REWRITE_MAX_ACTIVE=1",
        "HWP_CODEX_USER=" + owner, "HWP_CODEX_ALLOWED_USERS=", "HWP_CODEX_MODEL=",
        "HWP_CODEX_DAILY_REQUEST_LIMIT=60", "HWP_REWRITE_PROVIDER=codex",
        "HWP_DOCUMENT_ASSISTANT_PROVIDER=codex", "HWP_ACCOUNTS_ENABLED=0",
        "HWP_REGISTRATION_ENABLED=0", "HWP_REGISTRATION_MAX_USERS=100", "HWP_GUEST_PASSWORD=", "",
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/opt/hwp-assistant/production.env")
    parser.add_argument("--owner", default="owner")
    args = parser.parse_args()
    path = Path(args.env_file).expanduser().resolve()
    content = settings(args.owner)
    if path.exists():
        raise SystemExit("Refusing to overwrite existing credentials. Use the existing file.")
    subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)
    for volume in VOLUMES:
        result = subprocess.run(["docker", "volume", "inspect", volume],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            raise SystemExit("Existing production volumes found. Recover the original env file; do not generate new DB credentials.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # O_EXCL prevents a concurrent initializer from replacing secrets.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
    for volume in VOLUMES:
        subprocess.run(["docker", "volume", "create", "--label", "app=hwp-assistant-prod", volume],
                       check=True, stdout=subprocess.DEVNULL)
    print(f"Created {path} (mode 600) and the three production volumes.")
    print(f"Login user: {args.owner}. Read that user's token locally from the private env file.")
    print("No application or model has been started. Keep a protected copy of the env file.")


if __name__ == "__main__":
    main()
