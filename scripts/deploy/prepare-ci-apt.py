#!/usr/bin/env python3
"""Use signed official Ubuntu archives on disposable GitHub-hosted CI only."""
import os
import platform
import re
from pathlib import Path

UBUNTU_URI = re.compile(r"https?://(?P<host>azure\.archive\.ubuntu\.com|archive\.ubuntu\.com|security\.ubuntu\.com)/ubuntu(?=[/\s]|$)")
# GitHub's Ubuntu image replaces its Azure URI with this exact local mirror
# reference (actions/runner-images: images/ubuntu/scripts/build/configure-apt-sources.sh).
# Bypass that Azure-first list without changing the list or any other mirror.
RUNNER_MIRROR_URI = re.compile(r"mirror\+file:/etc/apt/apt-mirrors\.txt(?=\s|$)")
APT_NETWORK = 'Acquire::Retries "2";\nAcquire::http::Timeout "20";\nAcquire::https::Timeout "20";\n'


def rewrite_sources(text):
    def official(match):
        host = "security.ubuntu.com" if match["host"] == "security.ubuntu.com" else "archive.ubuntu.com"
        return "https://" + host + "/ubuntu"
    return RUNNER_MIRROR_URI.sub("https://archive.ubuntu.com/ubuntu/", UBUNTU_URI.sub(official, text))


def prepare(root=Path("/"), environ=None):
    env = os.environ if environ is None else environ
    if any(env.get(key) != value for key, value in (
        ("GITHUB_ACTIONS", "true"), ("RUNNER_ENVIRONMENT", "github-hosted"), ("RUNNER_OS", "Linux"))):
        raise ValueError("Only a disposable GitHub-hosted Linux runner is supported")
    release = dict(line.split("=", 1) for line in (root / "etc/os-release").read_text().splitlines()
                   if "=" in line and not line.startswith("#"))
    if release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "24.04" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise ValueError("Only the pinned Ubuntu 24.04 x64 CI image is supported")
    # Keep Signed-By, suites, components, source options and unrelated archives
    # byte-for-byte. This changes transport URLs, never trust policy or keys.
    paths = [root / "etc/apt/sources.list", root / "etc/apt/sources.list.d/ubuntu.sources"]
    originals = {path: path.read_bytes().decode("utf-8") for path in paths if path.is_file()}
    if not any(UBUNTU_URI.search(text) or RUNNER_MIRROR_URI.search(text) for text in originals.values()):
        raise ValueError("Expected official Ubuntu source configuration was not found")
    config = root / "etc/apt/apt.conf.d/99hwp-ci-network"
    if not config.parent.is_dir():
        raise ValueError("Expected Ubuntu APT configuration directory was not found")
    for path, text in originals.items():
        changed = rewrite_sources(text)
        if changed != text:
            path.write_bytes(changed.encode("utf-8"))
    # APT reads this file itself, including when Playwright invokes sudo. Its
    # normal repository-signature and HTTPS certificate validation stay enabled.
    config.write_text(APT_NETWORK)
    print("Prepared official Ubuntu HTTPS sources; APT retries=2, network timeout=20s")


if __name__ == "__main__":
    try:
        prepare()
    except (OSError, ValueError) as error:
        raise SystemExit(f"prepare-ci-apt: {error}") from error
