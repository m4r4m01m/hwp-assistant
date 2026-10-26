"""Offline build/TLS contracts and mocked shell orchestration, not Docker/browser execution."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/deploy/run-browser-checks.sh"


def instructions(text):
    return [line.strip() for line in text.replace("\\\n", " ").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


class BuildCacheContracts(unittest.TestCase):
    def setUp(self):
        self.front = (ROOT / "frontend/Dockerfile").read_text()
        self.core = self.front.split(" AS core-build\n", 1)[1].split("FROM scratch AS core-export", 1)[0]
        self.commands = instructions(self.core)

    def test_only_explicit_native_inputs_are_copied(self):
        sources = []
        for line in self.commands:
            if line.startswith("COPY ") and "--from=" not in line:
                sources.extend(line.split()[1:-1])
        self.assertEqual(set(sources), {
            "scripts/build-release-core.mjs", "scripts/engine-build-lib.mjs", "scripts/engine-lock.json",
            "scripts/prepare-patched-core.py", "scripts/record-patched-core.mjs",
            "scripts/test-engine-fill.mjs", "scripts/test-engine-selection.mjs", "scripts/patches",
        })

    def test_deploy_ui_and_bridge_changes_are_not_native_inputs(self):
        sources = [source for line in self.commands if line.startswith("COPY ") and "--from=" not in line
                   for source in line.split()[1:-1]]
        for changed in ("scripts/deploy/smoke.py", "scripts/deploy/run-browser-checks.sh",
                        "scripts/assistant-bridge.ts.txt", "frontend/src/main.tsx",
                        "frontend/deployment-tests/subpath.spec.ts", ".github/workflows/hwp-deploy.yml"):
            self.assertFalse(any(changed == src or changed.startswith(src.rstrip("/") + "/") for src in sources), changed)
        self.assertIn("COPY --from=dependencies /app/frontend/node_modules/fflate ./frontend/node_modules/fflate", self.commands)

    def test_cleanup_is_in_same_layer_after_verified_output_copy(self):
        command = next(line for line in self.commands if line.startswith("RUN node scripts/build-release-core.mjs"))
        self.assertLess(command.index("node scripts/build-release-core.mjs"), command.index("cp -a"))
        self.assertLess(command.index("cp -a"), command.index("rm -rf /build-target /app/.cache"))
        self.assertIn("cp -a /app/.cache/rhwp-core-release/pkg/. /out/", command)
        self.assertNotIn("rm -rf /out", command)

    def test_both_consumers_use_preserved_package(self):
        self.assertIn("COPY --from=core-build /out/ /\n", self.front)
        self.assertIn("COPY --from=core-build /out/ /app/.cache/rhwp-core-release/pkg/", self.front)
        self.assertIn("RUN node --test /app/scripts/public-base.test.mjs && npm run engine", self.front)

    def test_revision_arg_follows_all_api_run_steps(self):
        lines = instructions((ROOT / "backend/Dockerfile").read_text())
        revision = lines.index("ARG HWP_REVISION=development")
        self.assertTrue(all(index < revision for index, line in enumerate(lines) if line.startswith("RUN ")))
        self.assertLess(lines.index("RUN pip install --no-cache-dir -r requirements.txt"), lines.index("COPY backend/app ./app"))
        self.assertIn("LABEL org.opencontainers.image.revision=${HWP_REVISION}", lines)

    def test_job_env_uses_pre_runner_contexts_only(self):
        workflow = (ROOT / ".github/workflows/hwp-deploy.yml").read_text()
        allowed = {"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"}
        for block in re.findall(r"^    env:\n((?:      [^\n]*\n)+)", workflow, re.MULTILINE):
            for context in re.findall(r"\$\{\{\s*(\w+)\.", block):
                self.assertIn(context, allowed)

    def test_publication_and_production_gates_are_preserved(self):
        workflow = (ROOT / ".github/workflows/hwp-deploy.yml").read_text()
        self.assertIn("needs: [python-checks, frontend-checks, ui-lifecycle]", workflow)
        self.assertIn("github.ref == 'refs/heads/main' && inputs.deploy", workflow)
        self.assertIn("runs-on: [self-hosted, linux, x64, hwp-deploy]", workflow)
        for scope in ("hwp-api", "hwp-web"):
            self.assertIn("cache-from: type=gha,scope=" + scope, workflow)
            self.assertIn("cache-to: type=gha,mode=max,scope=" + scope, workflow)
        self.assertLess(workflow.index("bash ../scripts/deploy/run-browser-checks.sh"), workflow.index("Authenticate to GHCR only after verification"))
        self.assertLess(workflow.index("--grep 'CI TLS:'"), workflow.index("  build-verify-publish:"))

    def test_both_container_workflows_prepare_the_webhook_network_before_startup(self):
        for filename, job in (("hwp-deploy.yml", "build-verify-publish"),
                              ("poc-integration.yml", "container-regression")):
            with self.subTest(workflow=filename):
                workflow = (ROOT / ".github/workflows" / filename).read_text()
                body = workflow.split(f"\n  {job}:\n", 1)[1]
                body = re.split(r"(?m)^  [\w-]+:\s*$", body, maxsplit=1)[0]
                self.assertIn("runs-on: ubuntu-24.04", body)
                create = "run: docker network create --driver bridge --internal hwp-mattermost-hook"
                self.assertEqual(body.count(create), 1)
                startup = re.search(r"(?m)^ +docker compose [^\n]* up -d\b", body)
                self.assertIsNotNone(startup, "Missing Compose startup")
                self.assertLess(body.index(create), startup.start())


class RestartSmokeOrchestration(unittest.TestCase):
    def run_restart_step(self, fail_operation=""):
        # Execute the actual workflow shell with recording tools. This checks
        # orchestration only; the manual workflow validates real Docker/TLS.
        workflow = (ROOT / ".github/workflows/hwp-deploy.yml").read_text()
        step = workflow.split("      - name: HTTPS authentication, upload, and persisted bytes\n", 1)[1]
        step = step.split("      - ", 1)[0]
        script = "\n".join(line[10:] for line in step.splitlines() if line.startswith("          "))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            record = directory / "calls.jsonl"
            shim = (f"#!{sys.executable}\n"
                    "import json, os, pathlib, sys\n"
                    "name = pathlib.Path(sys.argv[0]).name\n"
                    "with open(os.environ['RECORD'], 'a') as out:\n"
                    "    out.write(json.dumps([name, *sys.argv[1:]]) + '\\n')\n"
                    "failed = name == 'docker' and os.environ.get('FAIL_OPERATION') in sys.argv[1:]\n"
                    "sys.exit(7 if failed else 0)\n")
            for name in ("docker", "python3"):
                executable = directory / name
                executable.write_text(shim)
                executable.chmod(0o755)
            env = {**os.environ, "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                   "HWP_ENV_FILE": str(directory / "synthetic.env"), "RECORD": str(record),
                   "FAIL_OPERATION": fail_operation}
            result = subprocess.run(["bash", "-e", "-c", script], env=env,
                                    capture_output=True, text=True, timeout=10)
            calls = [json.loads(line) for line in record.read_text().splitlines()]
        return result, calls

    def test_restart_retains_gateway_and_uploads_only_once(self):
        result, calls = self.run_restart_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in calls], ["python3", "docker", "docker", "python3"])
        self.assertIn("--upload-sample", calls[0])
        self.assertIn("--verify-receipt", calls[3])
        self.assertNotIn("--upload-sample", calls[3])
        for command in calls[1:3]:
            files = [command[index + 1] for index, argument in enumerate(command) if argument == "-f"]
            self.assertEqual(files, ["compose.deploy.yaml", "compose.ci-gateway.yaml"])
            self.assertNotIn("--remove-orphans", command)
        self.assertEqual(calls[1][-3:], ["restart", "api", "web"])
        self.assertIn("--wait", calls[2])
        self.assertIn("--no-build", calls[2])

    def test_failed_restart_does_not_verify_receipt_or_retry_upload(self):
        for operation, call_count in (("restart", 2), ("up", 3)):
            with self.subTest(operation=operation):
                result, calls = self.run_restart_step(operation)
                self.assertEqual(result.returncode, 7)
                self.assertEqual(len(calls), call_count)
                self.assertEqual(sum("--upload-sample" in call for call in calls), 1)
                self.assertFalse(any("--verify-receipt" in call for call in calls))


class BrowserTLSContracts(unittest.TestCase):
    def test_tls_validation_and_workers_remain_enabled(self):
        config = (ROOT / "frontend/playwright.deploy.config.ts").read_text()
        self.assertIn("ignoreHTTPSErrors: false", config)
        self.assertIn('serviceWorkers: "allow"', config)
        script = SCRIPT.read_text()
        for forbidden in ("--ignore-certificate-errors", "--allow-insecure-localhost", "NODE_TLS_REJECT_UNAUTHORIZED", "update-ca-certificates"):
            self.assertNotIn(forbidden, script)
        self.assertIn("-t 'P,,'", script)
        self.assertIn('NODE_EXTRA_CA_CERTS="$cert"', script)

    def test_preflight_tests_real_registration_and_negative_cert(self):
        text = (ROOT / "frontend/deployment-tests/ci-tls.spec.ts").read_text()
        self.assertIn('navigator.serviceWorker.register("/sw.js")', text)
        self.assertIn('getRegistration())?.active?.state)).toBe("activated")', text)
        self.assertIn('context.request.get(trusted.url)', text)
        self.assertIn('rejects.toThrow(/ERR_CERT_/)', text)
        self.assertIn('rejects.toThrow(/certificate/i)', text)

    def run_guard(self, **overrides):
        env = {**os.environ, "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", **overrides}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)

    def test_non_actions_environment_is_rejected(self):
        result = self.run_guard(GITHUB_ACTIONS="false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only for a disposable", result.stderr)

    def test_self_hosted_environment_is_rejected(self):
        result = self.run_guard(RUNNER_ENVIRONMENT="self-hosted")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only for a disposable", result.stderr)

    def test_external_url_is_rejected(self):
        result = self.run_guard(HWP_DEPLOY_TEST_URL="https://example.com/hwp-assistant/")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("restricted to the localhost", result.stderr)

    def test_wrapper_forwards_exit_code_and_removes_temporary_home(self):
        # Fake tools test orchestration ONLY. Real NSS/Chromium TLS is checked in CI.
        for code in (0, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                target = root / "scripts/deploy/run-browser-checks.sh"
                target.parent.mkdir(parents=True)
                shutil.copyfile(SCRIPT, target)
                (root / "frontend").mkdir()
                bins = root / "bin"; bins.mkdir()
                certs = root / "certs"; certs.mkdir(); (certs / "cert.pem").write_text("synthetic orchestration-only input")
                for name in ("openssl", "certutil"):
                    path = bins / name; path.write_text("#!/bin/sh\nexit 0\n"); path.chmod(0o755)
                node = bins / "node"
                node.write_text('#!/bin/sh\nprintf "%s\\n" "$HOME" > "$RECORD"\n'
                                '[ "$NODE_EXTRA_CA_CERTS" = "$HWP_CI_CERT_DIR/cert.pem" ] || exit 91\n'
                                '[ "$HWP_CI_TLS" = 1 ] || exit 92\n'
                                '[ "$6" = "CI TLS:" ] || exit 93\nexit "$CHILD_EXIT"\n')
                node.chmod(0o755)
                env = {**os.environ, "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                       "HWP_DEPLOY_TEST_URL": "https://localhost:24443/hwp-assistant/",
                       "RUNNER_TEMP": temp, "HWP_CI_CERT_DIR": str(certs), "PLAYWRIGHT_BROWSERS_PATH": str(root / "browsers"),
                       "PATH": str(bins) + os.pathsep + os.environ["PATH"], "RECORD": str(root / "record"), "CHILD_EXIT": str(code)}
                result = subprocess.run(["bash", str(target), "--grep", "CI TLS:"], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, code, result.stderr)
                home = Path((root / "record").read_text().strip())
                self.assertEqual(home.parent, root)
                self.assertTrue(home.name.startswith("hwp-browser-home."))
                self.assertFalse(home.exists())
                self.assertTrue((certs / "cert.pem").exists())

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


if __name__ == "__main__":
    unittest.main()
