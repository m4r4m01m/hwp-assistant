"""Bounded status check only; never print login output or start a model turn."""
import subprocess
import sys

try:
    result = subprocess.run([sys.argv[1], 'login', 'status'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=15)
except (OSError, subprocess.TimeoutExpired):
    raise SystemExit(1) from None
raise SystemExit(0 if result.returncode == 0 else 1)
