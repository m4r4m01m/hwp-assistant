"""CLI precheck regression tests; no real Codex process or credentials are accessed."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("codex_login_check", Path(__file__).with_name("check-codex-login.py"))
login_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login_check)


class LoginCheckTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def assert_status_command(self, process, executable):
        process.assert_called_once_with([executable, "login", "status"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)

    def test_npm_cmd_is_executed_without_a_powershell_shim(self):
        with patch.object(login_check.shutil, "which", return_value="C:/npm/codex.cmd") as resolve, \
             patch.object(login_check.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as process:
            self.assertEqual(login_check.check_login(), 0)
            resolve.assert_called_once_with("codex")
            self.assert_status_command(process, "C:/npm/codex.cmd")

    def test_native_executable_uses_the_same_status_check(self):
        with patch.object(login_check.shutil, "which", return_value="C:/tools/codex.exe"), \
             patch.object(login_check.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as process:
            self.assertEqual(login_check.check_login(), 0)
            self.assert_status_command(process, "C:/tools/codex.exe")

    def test_explicit_environment_override_is_preserved(self):
        with patch.dict(os.environ, {"HWP_CODEX_EXECUTABLE": "C:/chosen/codex.exe"}), \
             patch.object(login_check.shutil, "which") as resolve, \
             patch.object(login_check.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as process:
            self.assertEqual(login_check.check_login(), 0)
            resolve.assert_not_called()
            self.assert_status_command(process, "C:/chosen/codex.exe")

    def test_invalid_override_does_not_fall_back_to_another_installation(self):
        with patch.dict(os.environ, {"HWP_CODEX_EXECUTABLE": "C:/missing/codex.exe"}), \
             patch.object(login_check.shutil, "which") as resolve, \
             patch.object(login_check.subprocess, "run", side_effect=OSError("missing")) as process:
            self.assertEqual(login_check.check_login(), 1)
            resolve.assert_not_called()
            self.assert_status_command(process, "C:/missing/codex.exe")

    def test_missing_cli_is_distinct_from_login_failure(self):
        with patch.object(login_check.shutil, "which", return_value=None), \
             patch.object(login_check.subprocess, "run") as process:
            self.assertEqual(login_check.check_login(), 2)
            process.assert_not_called()

    def test_failed_or_timed_out_status_is_not_ready(self):
        for result in (subprocess.CompletedProcess([], 1), subprocess.TimeoutExpired("synthetic", 15)):
            with self.subTest(result=type(result).__name__), \
                 patch.object(login_check.subprocess, "run") as process:
                if isinstance(result, Exception):
                    process.side_effect = result
                else:
                    process.return_value = result
                self.assertEqual(login_check.check_login("C:/test/codex.exe"), 1)

    @unittest.skipUnless(os.name == "nt", "Windows PATH/PATHEXT resolution")
    def test_real_windows_resolver_ignores_adjacent_npm_powershell_shim(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for name in ("codex.ps1", "codex.cmd"):
                (directory / name).write_text("synthetic; never executed", encoding="utf-8")
            with patch.dict(os.environ, {"PATH": folder, "PATHEXT": ".COM;.EXE;.BAT;.CMD"}), \
                 patch.object(login_check.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as process:
                self.assertEqual(login_check.check_login(), 0)
                self.assertEqual(Path(process.call_args.args[0][0]), directory / "codex.cmd")


if __name__ == "__main__":
    unittest.main()
