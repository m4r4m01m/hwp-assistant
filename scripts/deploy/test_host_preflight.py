"""Read-only host diagnostic boundaries using synthetic settings and Docker replies."""
import sys
sys.dont_write_bytecode = True

from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("host_preflight", HERE / "check-host.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
SECRET = "synthetic-never-print-credential-01234567890123456789"
DB_ID, API_ID, WEB_ID = "a" * 12, "b" * 12, "c" * 12


def settings(path):
    path.write_text("POSTGRES_PASSWORD=" + "b" * 64 + "\nHWP_AUTH_TOKENS='" +
                    json.dumps({"owner": SECRET}) + "'\nHWP_ALLOWED_HOSTS=localhost,127.0.0.1\n")
    path.chmod(0o600)


def docker_reply(args, *, wrong_mount=False, missing_volume=False):
    if args[1] == "info":
        return "linux"
    if args[1:3] == ["compose", "version"]:
        return "2.24.6"
    if args[1:3] == ["volume", "inspect"]:
        return None if missing_volume else args[-1]
    if args[1] == "ps":
        return f"{DB_ID}|db|running\n{API_ID}|api|running\n{WEB_ID}|web|running"
    if args[1] == "inspect":
        return "volume|" + (SECRET if wrong_mount else checker.VOLUMES[0 if args[-1] == DB_ID else 1])
    raise AssertionError("Unexpected command")


class HostPreflightTests(unittest.TestCase):
    def test_existing_mount_and_missing_volumes_block_adoption(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "production.env"
            settings(env)
            for mismatch, missing in ((False, False), (True, False), (False, True)):
                with self.subTest(mismatch=mismatch, missing=missing):
                    calls = []
                    def command(args):
                        calls.append(args)
                        return docker_reply(args, wrong_mount=mismatch, missing_volume=missing)
                    rows = checker.diagnose(env, command=command)
                    statuses = {name: status for status, name, _ in rows}
                    self.assertEqual(statuses['metadata-volume'], 'BLOCKED' if missing else 'PASS')
                    self.assertEqual(statuses['db-storage'], 'BLOCKED' if mismatch else 'PASS')
                    self.assertNotIn(SECRET, repr(rows))
                    self.assertTrue(all(args[1] in ('info', 'compose', 'volume', 'ps', 'inspect') for args in calls))
                    self.assertTrue(all('--format' in args for args in calls if args[1] in ('info', 'volume', 'ps', 'inspect')))
                    self.assertFalse(any(word in ('up', 'start', 'stop', 'create', 'exec', 'login', 'config')
                                         for args in calls for word in args))

    def test_validation_and_command_errors_never_expose_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "production.env"
            settings(env)
            output, errors = io.StringIO(), io.StringIO()
            failure = subprocess.TimeoutExpired(['docker'], 5, output=SECRET, stderr=SECRET)
            with patch.object(checker, 'check_env', side_effect=ValueError(SECRET)), \
                 patch.object(checker.subprocess, 'run', side_effect=failure) as run, \
                 redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(checker.main(['--env-file', str(env)]), 2)
            self.assertNotIn(SECRET, output.getvalue() + errors.getvalue())
            self.assertIn('BLOCKED env-validation:', output.getvalue())
            self.assertIn('BLOCKED docker:', output.getvalue())
            self.assertEqual(run.call_args.kwargs['timeout'], 5)
            self.assertIs(run.call_args.kwargs['shell'], False)
            self.assertTrue(all(line.startswith(('PASS ', 'BLOCKED ', 'MANUAL '))
                                for line in output.getvalue().splitlines()))

    def test_missing_project_is_manual_and_does_not_probe_other_containers(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "production.env"
            settings(env)
            calls = []
            def command(args):
                calls.append(args)
                return '' if args[1] == 'ps' else docker_reply(args)
            rows = checker.diagnose(env, command=command)
            statuses = {name: status for status, name, _ in rows}
            self.assertEqual(statuses['db-service'], 'MANUAL')
            self.assertEqual(statuses['port-owner'], 'MANUAL')
            self.assertFalse(any(args[1] == 'inspect' for args in calls))

    @unittest.skipUnless(os.name == 'posix', 'Synthetic executable Docker fixture uses POSIX')
    def test_real_cli_is_stdlib_only_and_does_not_write_bytecode_or_create_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            repo = folder / 'repo'
            for relative in ('scripts/deploy/check-host.py', 'scripts/deploy/config.py',
                             'backend/app/production.py', 'backend/app/host_validation.py', 'backend/app/__init__.py'):
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            env = folder / 'production.env'
            settings(env)
            binary = folder / 'bin'
            binary.mkdir()
            docker = binary / 'docker'
            docker.write_text('#!' + sys.executable + '\nimport sys\na=sys.argv[1:]\n'
                              'if a[0]=="info": print("linux")\n'
                              'elif a[:2]==["compose","version"]: print("2.24.6")\n'
                              'elif a[:2]==["volume","inspect"]: print(a[-1])\n'
                              'elif a[0]=="ps": pass\nelse: sys.exit(1)\n')
            docker.chmod(0o700)
            result = subprocess.run([sys.executable, '-I', '-S', str(repo / 'scripts/deploy/check-host.py'),
                                     '--env-file', str(env)], capture_output=True, text=True, timeout=20,
                                    env={**os.environ, 'PATH': str(binary) + os.pathsep + os.environ.get('PATH', '')})
            self.assertIn(result.returncode, (0, 2), result.stderr)
            self.assertIn('PASS env-validation:', result.stdout)
            self.assertIn('PASS docker:', result.stdout)
            self.assertNotIn(SECRET, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertFalse(list(repo.rglob('__pycache__')))
            self.assertFalse((folder / 'state').exists())
            self.assertFalse((folder / 'backups').exists())


if __name__ == '__main__':
    unittest.main()
