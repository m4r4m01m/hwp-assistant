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
APT_SOURCES = 'Dir::Etc::sourcelist "hwp-ci/sources.list";\nDir::Etc::sourceparts "hwp-ci/sources.list.d";\n'


def rewrite_sources(text):
    def official(match):
        host = "security.ubuntu.com" if match["host"] == "security.ubuntu.com" else "archive.ubuntu.com"
        return "https://" + host + "/ubuntu"
    return RUNNER_MIRROR_URI.sub("https://archive.ubuntu.com/ubuntu/", UBUNTU_URI.sub(official, text))


def is_ubuntu_uri(uri):
    return bool(re.fullmatch(r"https?://(?:azure\.archive\.ubuntu\.com|archive\.ubuntu\.com|security\.ubuntu\.com)/ubuntu/?", uri)
                or RUNNER_MIRROR_URI.fullmatch(uri))


def ubuntu_sources(text, deb822=False):
    """Copy official entries, retaining their trust/options; never copy vendors."""
    if not deb822:
        selected = []
        for line in text.splitlines(keepends=True):
            match = re.match(r"^\s*deb(?:-src)?\s+(?:\[[^\]]*\]\s+)?(\S+)", line)
            if match and is_ubuntu_uri(match[1]):
                selected.append(rewrite_sources(line))
        return "".join(selected)
    selected = []
    for stanza in re.split(r"\r?\n[ \t]*\r?\n", text):
        # Deb822 continuation lines belong to the preceding field. Comments
        # carry no URIs and must not turn a vendor stanza into an Ubuntu one.
        fields = {}
        current = None
        for line in stanza.splitlines():
            if line.startswith("#"):
                continue
            if line[:1].isspace() and current:
                fields[current] += " " + line.strip()
            elif ":" in line:
                current, value = line.split(":", 1)
                current = current.lower()
                fields[current] = value.strip()
        uris = fields.get("uris", "").split()
        if not any(is_ubuntu_uri(uri) for uri in uris):
            continue
        if not all(is_ubuntu_uri(uri) for uri in uris):
            raise ValueError("Mixed Ubuntu/vendor URIs in one APT source stanza")
        if fields.get("enabled", "yes").lower() == "no":
            continue
        selected.append(rewrite_sources(stanza))
    return "\n\n".join(selected)


def prepare(root=Path("/"), environ=None):
    env = os.environ if environ is None else environ
    if any(env.get(key) != value for key, value in (
        ("GITHUB_ACTIONS", "true"), ("RUNNER_ENVIRONMENT", "github-hosted"), ("RUNNER_OS", "Linux"))):
        raise ValueError("Only a disposable GitHub-hosted Linux runner is supported")
    release = dict(line.split("=", 1) for line in (root / "etc/os-release").read_text().splitlines()
                   if "=" in line and not line.startswith("#"))
    if release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "24.04" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise ValueError("Only the pinned Ubuntu 24.04 x64 CI image is supported")
    # Keep original sources (including vendor sources) byte-for-byte. Isolate
    # browser dependency installation from unrelated vendor repository outages.
    paths = [root / "etc/apt/sources.list", root / "etc/apt/sources.list.d/ubuntu.sources"]
    sources = {path.name: ubuntu_sources(path.read_bytes().decode("utf-8"), path.suffix == ".sources")
               if path.is_file() else "" for path in paths}
    if not any(sources.values()):
        raise ValueError("Expected official Ubuntu source configuration was not found")
    config = root / "etc/apt/apt.conf.d/99hwp-ci-network"
    if not config.parent.is_dir():
        raise ValueError("Expected Ubuntu APT configuration directory was not found")
    isolated = root / "etc/apt/hwp-ci"
    (isolated / "sources.list.d").mkdir(parents=True, exist_ok=True)
    (isolated / "sources.list").write_bytes(sources["sources.list"].encode("utf-8"))
    (isolated / "sources.list.d/ubuntu.sources").write_bytes(sources["ubuntu.sources"].encode("utf-8"))
    # APT reads this file itself, including when Playwright invokes sudo. Its
    # normal repository-signature and HTTPS certificate validation stay enabled.
    config.write_text(APT_NETWORK + APT_SOURCES)
    print("Isolated official Ubuntu HTTPS sources; APT retries=2, network timeout=20s")


if __name__ == "__main__":
    try:
        prepare()
    except (OSError, ValueError) as error:
        raise SystemExit(f"prepare-ci-apt: {error}") from error
