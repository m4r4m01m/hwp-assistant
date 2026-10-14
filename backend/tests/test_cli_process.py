"""Real owned synthetic processes only; no Codex, authentication or model calls."""
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize("timeout", [True, False])
def test_runner_reaps_owned_descendants_before_return(tmp_path, timeout):
    from app.cli_process import run_process
    marker = tmp_path / "child-survived"
    child = "import time,pathlib; time.sleep(1.5); pathlib.Path(" + repr(str(marker)) + ").touch()"
    parent = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) +
              "], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print('spawned',flush=True); " +
              ("time.sleep(5)" if timeout else "time.sleep(.1)"))
    args = [sys.executable, "-c", parent]
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired):
            run_process(args, capture_output=True, encoding="utf-8", timeout=.5)
    else:
        result = run_process(args, capture_output=True, encoding="utf-8", timeout=3)
        assert result.returncode == 0 and result.stdout.strip() == "spawned"
    time.sleep(1.7)  # Bounded synthetic child lifetime, not model timing.
    assert not marker.exists(), "owned descendant outlived runner cleanup"
