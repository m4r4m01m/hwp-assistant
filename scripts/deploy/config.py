"""Read the generated, single-line Compose env file without executing shell code."""
import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.production import validate_environment


def read_env(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("Create the production env file with scripts/deploy/init.py first")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError("Production env file must not be group/world readable; chmod 600 it")
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in result:
            raise ValueError("Use unique KEY=value entries in the production env file")
        parts = shlex.split(value, comments=True, posix=True)
        if len(parts) > 1:
            raise ValueError("Quote values that contain spaces")
        result[key] = parts[0] if parts else ""
    return result


def check_env(path):
    values = read_env(path)
    password = values.get("POSTGRES_PASSWORD", "")
    # Validate before constructing a URL: @/: could otherwise change its meaning.
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", password):
        raise ValueError("POSTGRES_PASSWORD must be generated URL-safe text (32+ characters)")
    validate_environment({
        **values,
        "DATABASE_URL": f"postgresql+psycopg://hwp_assistant:{password}@db:5432/hwp_assistant",
        "STORAGE_DIR": "/data/files",
    })
    prefix = values.get("HWP_IMAGE_PREFIX", "ghcr.io/m4r4m01m/hwp-assistant")
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9._/-]*[a-z0-9]", prefix):
        raise ValueError("HWP_IMAGE_PREFIX must be a lowercase GHCR repository prefix")
    return values


if __name__ == "__main__":
    try:
        check_env(sys.argv[1])
    except (ValueError, OSError, IndexError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("Production configuration validated (secrets omitted).")
