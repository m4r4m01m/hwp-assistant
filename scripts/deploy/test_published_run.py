"""Offline metadata/safety contracts; not a live registry or home-server deployment."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("published_run", Path(__file__).with_name("verify-published-run.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
RUN_ID = "39952556251"
SOURCE = "324b1bfe94f0c4b3062775080cdfb577b954eb1a"
CURRENT = "c85699dc663b0915ee78475f933912def9ac0cb8"
RUN = {
    "id": int(RUN_ID), "repository": {"full_name": module.REPOSITORY},
    "head_repository": {"full_name": module.REPOSITORY}, "path": module.WORKFLOW,
    "event": "workflow_dispatch", "head_branch": "main", "head_sha": SOURCE,
    "status": "completed", "conclusion": "success", "run_attempt": 1,
}
JOBS = [{"name": name, "run_id": int(RUN_ID), "head_sha": SOURCE,
         "status": "completed", "conclusion": "success", "steps": []} for name in module.JOBS]
JOBS[-1]["steps"] = [{"name": name, "status": "completed", "conclusion": "success"}
                     for name in module.PUBLISH_STEPS]


class ReleaseValidationTests(unittest.TestCase):
    def verify(self, *, run=None, jobs=None, comparison=None, final=None, paginate=False):
        run = deepcopy(RUN if run is None else run)
        jobs = deepcopy(JOBS if jobs is None else jobs)
        compare = comparison or {"status": "ahead", "merge_base_commit": {"sha": SOURCE}}
        paths = []
        def get(path):
            paths.append(path)
            if "/compare/" in path:
                return compare
            if "/jobs?" in path:
                self.assertIn(f"/attempts/{run['run_attempt']}/jobs?", path)
                page = int(path.rsplit("page=", 1)[1])
                size = 2 if paginate else 100
                return {"total_count": len(jobs), "jobs": jobs[(page - 1) * size:page * size]}
            return run if len(paths) == 1 or final is None else final
        result = module.verify(RUN_ID, CURRENT, get)
        self.assertTrue(paths[-1].endswith("/" + RUN_ID))
        return result, paths

    def test_verified_build_returns_original_image_sha_not_current_main(self):
        result, _ = self.verify()
        self.assertEqual(result, {"source_sha": SOURCE, "build_run_id": RUN_ID, "build_attempt": "1"})

    def test_identical_source_commit_is_allowed(self):
        self.verify(comparison={"status": "identical", "merge_base_commit": {"sha": SOURCE}})

    def test_rejects_non_numeric_input_before_any_network_request(self):
        for value in ("", "#24", SOURCE, "../1", "1\nanything", "1; echo bad", "0", "01", "9" * 21):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.verify(value, CURRENT, lambda _: self.fail("Unexpected API request"))

    def test_invalid_workflow_sha_rejected_before_network(self):
        with self.assertRaises(ValueError):
            module.verify(RUN_ID, "main", lambda _: self.fail("Unexpected API request"))

    def test_rejects_failed_pending_cancelled_skipped_runs(self):
        for key, value in (("conclusion", "failure"), ("conclusion", "cancelled"),
                           ("conclusion", "skipped"), ("status", "in_progress")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.verify(run={**RUN, key: value})

    def test_rejects_foreign_repository_and_fork(self):
        for key in ("repository", "head_repository"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(run={**RUN, key: {"full_name": "someone/other"}})

    def test_rejects_pr_push_other_workflow_and_nonmain(self):
        for key, value in (("event", "pull_request"), ("event", "push"),
                           ("path", ".github/workflows/other.yml"), ("head_branch", "feature")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.verify(run={**RUN, key: value})

    def test_rejects_id_sha_or_attempt_corruption(self):
        for key, value in (("id", 1), ("head_sha", "main"), ("head_sha", SOURCE + "\n"),
                           ("run_attempt", 0), ("run_attempt", "1"), ("run_attempt", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(run={**RUN, key: value})

    def test_missing_required_job_rejected(self):
        for i in range(len(JOBS)):
            with self.subTest(i=i), self.assertRaises(ValueError):
                self.verify(jobs=JOBS[:i] + JOBS[i + 1:])

    def test_failed_or_skipped_required_job_rejected(self):
        for i in range(len(JOBS)):
            for conclusion in ("skipped", "failure"):
                jobs = deepcopy(JOBS); jobs[i]["conclusion"] = conclusion
                with self.subTest(i=i, conclusion=conclusion), self.assertRaises(ValueError):
                    self.verify(jobs=jobs)

    def test_job_with_different_run_or_sha_rejected(self):
        for key, value in (("run_id", 1), ("head_sha", CURRENT)):
            jobs = deepcopy(JOBS); jobs[-1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(jobs=jobs)

    def test_missing_or_unsuccessful_publication_steps_rejected(self):
        for i in range(len(module.PUBLISH_STEPS)):
            for remove in (False, True):
                jobs = deepcopy(JOBS)
                if remove:
                    jobs[-1]["steps"].pop(i)
                else:
                    jobs[-1]["steps"][i]["conclusion"] = "skipped"
                with self.subTest(i=i, remove=remove), self.assertRaises(ValueError):
                    self.verify(jobs=jobs)

    def test_duplicate_job_or_step_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(jobs=JOBS + [JOBS[-1]])
        jobs = deepcopy(JOBS); jobs[-1]["steps"].append(jobs[-1]["steps"][-1])
        with self.assertRaises(ValueError):
            self.verify(jobs=jobs)

    def test_deploy_home_skipped_in_original_build_is_allowed(self):
        self.verify(jobs=JOBS + [{"name": "deploy-home", "conclusion": "skipped"}])

    def test_old_source_must_be_ancestor_of_selected_main(self):
        for comparison in ({"status": "diverged", "merge_base_commit": {"sha": SOURCE}},
                           {"status": "behind", "merge_base_commit": {"sha": SOURCE}},
                           {"status": "ahead", "merge_base_commit": {"sha": CURRENT}}):
            with self.subTest(comparison=comparison), self.assertRaises(ValueError):
                self.verify(comparison=comparison)

    def test_pagination_and_attempt_are_used(self):
        result, paths = self.verify(run={**RUN, "run_attempt": 2}, paginate=True)
        self.assertEqual(result["build_attempt"], "2")
        self.assertEqual(sum("/attempts/2/jobs?" in path for path in paths), 2)

    def test_rerun_during_validation_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(final={**RUN, "run_attempt": 2})
        with self.assertRaises(ValueError):
            self.verify(final={**RUN, "status": "in_progress"})

    def test_no_redirect_with_credentials(self):
        with self.assertRaises(ValueError):
            module.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.invalid")


class WorkflowContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github/workflows/hwp-deploy-only.yml").read_text()
        cls.host = cls.text.split("  deploy-home:\n", 1)[1]

    def test_only_manual_confirmed_main_can_run(self):
        triggers = self.text.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertNotIn("pull_request:", triggers)
        self.assertNotIn("push:", triggers)
        self.assertIn("default: false", triggers)
        self.assertEqual(self.text.count("github.ref == 'refs/heads/main' && inputs.confirm_deploy"), 2)

    def test_no_build_and_existing_backup_script_is_reused(self):
        self.assertIn("bash scripts/deploy/deploy.sh pull", self.host)
        for forbidden in ("build-push-action", "setup-buildx-action", "docker build", "deploy.sh build", "init.py", "down -v"):
            self.assertNotIn(forbidden, self.text)

    def test_verified_source_workspace_and_production_lock(self):
        self.assertIn("needs: validate-published-build", self.host)
        self.assertIn("ref: ${{ needs.validate-published-build.outputs.source_sha }}", self.host)
        self.assertIn("HWP_IMAGE_TAG: ${{ needs.validate-published-build.outputs.source_sha }}", self.host)
        self.assertIn("path: release-${{ github.run_id }}-${{ github.run_attempt }}", self.host)
        self.assertIn("clean: false", self.host)
        self.assertIn('[[ ! -e "$workspace/release-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" ]]', self.host)
        self.assertIn("group: hwp-home-production", self.host)
        self.assertIn("cancel-in-progress: false", self.host)
        self.assertIn("environment: hwp-production", self.host)
        self.assertIn("runs-on: [self-hosted, linux, x64, hwp-deploy]", self.host)

    def test_both_deployments_check_the_current_public_funnel_route(self):
        for filename in ("hwp-deploy.yml", "hwp-deploy-only.yml"):
            with self.subTest(workflow=filename):
                workflow = (ROOT / ".github/workflows" / filename).read_text()
                host = workflow.split("  deploy-home:\n", 1)[1]
                self.assertEqual(re.findall(r"--url\s+(https://\S+)", host),
                                 ["https://pwsb-svr.taile7b6c4.ts.net/hwp-assistant/"])

    def test_preflight_before_login_and_replacement(self):
        self.assertLess(self.host.index("Read-only production preflight"), self.host.index("docker login"))
        self.assertLess(self.host.index("check-host.py"), self.host.index("deploy.sh pull"))
        self.assertIn("--env-file /opt/hwp-assistant/production.env", self.host)

    def test_token_is_ephemeral_and_never_interpolated_into_script(self):
        self.assertIn("packages: read", self.host)
        self.assertNotIn("packages: write", self.text)
        self.assertIn("unset GHCR_TOKEN", self.host)
        self.assertIn("--password-stdin", self.host)
        self.assertIn("trap 'docker logout", self.host)
        self.assertNotRegex(self.text, r"run:\s*[^\n]*\$\{\{\s*(inputs|secrets)\.")

    def test_local_runner_credentials_excluded_from_docker_context(self):
        ignore = (ROOT / ".dockerignore").read_text().splitlines()
        for entry in ("**/actions-runner", "**/.runner", "**/.credentials", "**/.credentials_rsaparams"):
            self.assertIn(entry, ignore)


if __name__ == "__main__":
    unittest.main(verbosity=2)
