#!/usr/bin/env python3
"""Validate a published main-branch build before scheduling the production runner.

Standard library only. This reads GitHub metadata; it never downloads/runs images,
connects to the home server, or treats a SHA tag as a cryptographic attestation.
"""
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

REPOSITORY = "m4r4m01m/hwp-assistant"
WORKFLOW = ".github/workflows/hwp-deploy.yml"
JOBS = ("python-checks", "frontend-checks", "ui-lifecycle", "build-verify-publish")
PUBLISH_STEPS = (
    "HTTPS authentication, upload, and persisted bytes",
    "Real browser, editor and comparison under the public subpath",
    "Publish commit-SHA image tags",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_run(run, run_id):
    require(isinstance(run, dict), "Invalid workflow run response")
    require(run.get("id") == int(run_id), "Run ID mismatch")
    require(run.get("repository", {}).get("full_name") == REPOSITORY,
            "Build belongs to another repository")
    require(run.get("head_repository", {}).get("full_name") == REPOSITORY,
            "Fork builds cannot be deployed")
    require(run.get("path") == WORKFLOW, "Not the approved publishing workflow")
    require(run.get("event") == "workflow_dispatch" and run.get("head_branch") == "main",
            "Only explicitly published main-branch builds can be deployed")
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "Selected build has not completed successfully")
    sha = run.get("head_sha", "")
    require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha), "Invalid source SHA")
    attempt = run.get("run_attempt")
    require(type(attempt) is int and attempt > 0, "Invalid build attempt")
    return sha, attempt


def validate_jobs(jobs, run_id, sha):
    require(isinstance(jobs, list), "Invalid jobs response")
    for name in JOBS:
        found = [job for job in jobs if job.get("name") == name]
        require(len(found) == 1, "Missing or ambiguous required job: " + name)
        job = found[0]
        require(job.get("run_id") == int(run_id) and job.get("head_sha") == sha,
                "Job source mismatch: " + name)
        require(job.get("status") == "completed" and job.get("conclusion") == "success",
                "Required job did not succeed: " + name)
        if name == "build-verify-publish":
            for step_name in PUBLISH_STEPS:
                steps = [step for step in job.get("steps", []) if step.get("name") == step_name]
                require(len(steps) == 1 and steps[0].get("status") == "completed"
                        and steps[0].get("conclusion") == "success",
                        "Verification/publication step did not succeed: " + step_name)


def verify(run_id, workflow_sha, get):
    require(isinstance(run_id, str) and re.fullmatch(r"[1-9][0-9]{0,19}", run_id),
            "Use the numeric Actions run ID, not the displayed run number or a SHA")
    require(isinstance(workflow_sha, str) and re.fullmatch(r"[0-9a-f]{40}", workflow_sha),
            "Invalid deployment workflow SHA")
    path = f"/repos/{REPOSITORY}/actions/runs/{run_id}"
    run = get(path)
    sha, attempt = validate_run(run, run_id)
    # Attempt-specific pagination prevents mixing jobs from different reruns.
    jobs = []
    total = None
    for page in range(1, 21):
        result = get(f"{path}/attempts/{attempt}/jobs?per_page=100&page={page}")
        require(isinstance(result, dict) and type(result.get("total_count")) is int,
                "Invalid paginated jobs response")
        count = result["total_count"]
        require(0 < count <= 2000 and (total is None or total == count), "Jobs changed during validation")
        total = count
        batch = result.get("jobs")
        require(isinstance(batch, list) and 0 < len(batch) <= 100, "Incomplete jobs response")
        jobs.extend(batch)
        if len(jobs) >= total:
            break
    require(len(jobs) == total, "Incomplete jobs pagination")
    validate_jobs(jobs, run_id, sha)
    comparison = get(f"/repos/{REPOSITORY}/compare/{sha}...{workflow_sha}")
    require(comparison.get("status") in ("ahead", "identical")
            and comparison.get("merge_base_commit", {}).get("sha") == sha,
            "Published source is not an ancestor of the selected main workflow")
    require(validate_run(get(path), run_id) == (sha, attempt),
            "Build was rerun during validation; select a completed attempt again")
    return {"source_sha": sha, "build_run_id": run_id, "build_attempt": str(attempt)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected GitHub API redirect")


def main():
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Unexpected deployment repository")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
            "Dispatch this workflow on main")
    token = os.environ.get("GH_TOKEN", "")
    require(bool(token), "Missing read-only GitHub Actions token")
    opener = urllib.request.build_opener(NoRedirect())

    def get(path):
        request = urllib.request.Request("https://api.github.com" + path, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hwp-deploy-only",
        })
        with opener.open(request, timeout=30) as response:
            data = response.read(4_000_001)
        require(len(data) <= 4_000_000, "GitHub API response exceeded limit")
        return json.loads(data)

    result = verify(os.environ.get("BUILD_RUN_ID", ""), os.environ.get("GITHUB_SHA", ""), get)
    output = os.environ.get("GITHUB_OUTPUT")
    require(bool(output), "Missing GitHub output file")
    with Path(output).open("a", encoding="utf-8") as stream:
        for name, value in result.items():
            stream.write(f"{name}={value}\n")
    print(f"Verified published build {result['build_run_id']} (attempt {result['build_attempt']}): {result['source_sha']}")
    print("No image was rebuilt and no production service was changed by this validation.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Do not print response bodies, request headers or credentials.
        if isinstance(exc, urllib.error.HTTPError):
            print(f"Publication validation failed: GitHub HTTP {exc.code}", file=sys.stderr)
        elif isinstance(exc, (OSError, KeyError, TypeError)):
            print("Publication validation failed: API/environment access error", file=sys.stderr)
        else:
            print("Publication validation failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
