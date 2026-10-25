#!/usr/bin/env python3
"""Build-time install of an exact official release. Never run at API startup."""
import hashlib
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
from urllib.request import urlopen

VERSION = "0.153.0"
ASSETS = {
    "x86_64": ("x86_64", "35a82c153d83959de09c2cb84ac70ba69d05788aeeb08d4a95ca68e39f86680e"),
    "aarch64": ("aarch64", "cc2c0c365d4d51c18163bba93cce5b922836900ca867f5bcf89b0e714a952a53"),
}


def main():
    arch, expected = ASSETS[platform.machine()]
    name = f"codex-{arch}-unknown-linux-musl"
    url = f"https://github.com/openai/codex/releases/download/rust-v{VERSION}/{name}.tar.gz"
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "codex.tar.gz"
        checksum, size = hashlib.sha256(), 0
        with urlopen(url, timeout=60) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 150 * 1024 * 1024:
                    raise ValueError("Codex archive size limit")
                checksum.update(chunk)
                output.write(chunk)
        if checksum.hexdigest() != expected:
            raise ValueError("Codex archive hash mismatch")
        # Do not extract arbitrary paths, symlinks or special files.
        with tarfile.open(archive, "r:gz") as source:
            member = source.getmember(name)
            if not member.isfile() or member.size > 400 * 1024 * 1024:
                raise ValueError("Unexpected Codex binary")
            with source.extractfile(member) as binary, open("/usr/local/bin/codex", "wb") as output:
                shutil.copyfileobj(binary, output)
        Path("/usr/local/bin/codex").chmod(0o755)
    print(f"Installed hash-verified Codex {VERSION} ({arch})")


if __name__ == "__main__":
    main()
