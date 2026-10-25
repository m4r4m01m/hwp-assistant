"""Bounded status check only; never print login output or start a model turn."""
import os
import shutil
import subprocess
import sys


def check_login(executable=None):
    # Match the backend resolver. PowerShell Get-Command may prefer npm's .ps1
    # shim, which subprocess cannot execute; Python resolves its .cmd instead.
    executable = executable or os.getenv("HWP_CODEX_EXECUTABLE") or shutil.which("codex")
    if not executable:
        return 2
    try:
        result = subprocess.run([executable, "login", "status"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(check_login(sys.argv[1] if len(sys.argv) > 1 else None))
