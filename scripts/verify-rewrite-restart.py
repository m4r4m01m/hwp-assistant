"""Isolated process-crash/real-clock expiry check. Synthetic storage fixture, no real AI.

Requires --out to name a NEW directory. --real-expiry observes the real 600s task limit.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend/tests"))
from test_storage import package
from app.hwpx import digest


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--real-expiry", action="store_true")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    api_port, mock_port = free_port(), free_port()
    while api_port == mock_port:
        mock_port = free_port()
    environment = dict(os.environ, DATABASE_URL=f"sqlite:///{out / 'metadata.db'}",
                       STORAGE_DIR=str(out / "files"), HWP_AUTH_TOKENS="", HWP_REWRITE_PROVIDER="llm",
                       HWP_CODEX_USER="not-local", LLM_PROVIDER="openai-compatible",
                       LLM_BASE_URL=f"http://127.0.0.1:{mock_port}/v1", LLM_MODEL="hwp-synthetic-fixture",
                       LLM_API_KEY="unused-test-key", LLM_TIMEOUT_SECONDS="240")
    proof = {"mock": True, "actual_model_calls": 0, "actual_engine": False,
             "approval": "not performed", "observations": [], "started_at": time.time()}
    children, logs = [], []

    def record():
        (out / "proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(module, directory, port, name):
        log = (out / f"{name}.log").open("w", encoding="utf-8")
        logs.append(log)
        child = subprocess.Popen([sys.executable, "-m", "uvicorn", module, "--app-dir", directory,
                                  "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=environment,
                                 stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        children.append(child)
        with httpx.Client(trust_env=False, timeout=2) as http:
            for _ in range(100):
                assert child.poll() is None, f"{name} exited during startup"
                try:
                    if http.get(f"http://127.0.0.1:{port}/openapi.json").is_success:
                        return child
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        raise RuntimeError(f"{name} startup timed out")

    try:
        start("mock_local_provider:app", "backend/tests", mock_port, "mock")
        server = start("app.main:app", "backend", api_port, "api-before")
        proof["first_pid"] = server.pid
        with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=3) as client, \
             httpx.Client(base_url=f"http://127.0.0.1:{mock_port}", trust_env=False, timeout=3) as mock:
            mock.post("/controls", json={"delay": 300}).raise_for_status()
            original = package("원본 저장 fixture")
            response = client.post("/api/documents", params={"name": "중단 복구 합성.hwpx", "document_id": str(uuid.uuid4())},
                                   content=original, headers={"Idempotency-Key": str(uuid.uuid4())})
            response.raise_for_status()
            doc = response.json()
            tid, job_id = str(uuid.uuid4()), str(uuid.uuid4())
            client.post(f"/api/documents/{doc['id']}/threads", json={"id": tid, "title": "중단 복구 fixture"}).raise_for_status()
            lease = client.post(f"/api/documents/{doc['id']}/session").json()["token"]
            text = "아직 저장하지 않은 합성 문장"
            snapshot = {"revision": 3, "state": {"documentEpoch": 1, "changeSeq": 3, "documentSha256": "a" * 64},
                        "selection": {"editable": True, "target": {"kind": "body_paragraph", "section": 0,
                            "paragraph": 0, "charOffset": 0, "length": len(text)}},
                        "evidence": {"text": text, "textSha256": digest(text.encode()),
                                     "formatSha256": "b" * 64, "adjacentContextSha256": "c" * 64}}
            payload = {"id": job_id, "base_version": doc["current_version"], "expected_revision": 0,
                       "selection": snapshot, "instruction": "합성 중단 시험"}
            task_path = f"/api/threads/{tid}/rewrite-tasks"
            started = time.monotonic()
            client.post(task_path, json=payload, headers={"X-Edit-Session": lease}).raise_for_status()
            for _ in range(100):
                if len(mock.get("/proof").json()["calls"]) == 1:
                    break
                time.sleep(0.05)
            assert len(mock.get("/proof").json()["calls"]) == 1
            server.kill()
            server.wait(timeout=10)
            # Verify there really is no old listener, not just a terminated launcher.
            for _ in range(50):
                try:
                    client.get("/api/health")
                except httpx.HTTPError:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("Old API still answers after process termination")
            restarted = start("app.main:app", "backend", api_port, "api-after")
            proof["second_pid"] = restarted.pid
            proof["old_listener_stopped"] = True
            proof.update(task_id=job_id, document_id=doc["id"], base_version=doc["current_version"],
                         original_sha256=hashlib.sha256(original).hexdigest())
            limit = 606 if args.real_expiry else 2
            while True:
                elapsed = time.monotonic() - started
                task = client.get(task_path).json()[0]
                assert task["snapshot"] == snapshot and task["candidates"] == [] and task["application"] is None
                assert len(client.get(f"/api/documents/{doc['id']}/versions").json()) == 1
                assert len(mock.get("/proof").json()["calls"]) == 1
                client.put(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": lease}).raise_for_status()
                proof["observations"].append({"elapsed_seconds": round(elapsed, 2), "status": task["status"],
                                              "mock_http_calls": 1, "versions": 1, "lease_renewed": True})
                record()
                print(f"elapsed={elapsed:.1f}s status={task['status']} mock_http_calls=1 versions=1", flush=True)
                if task["status"] == "expired" or elapsed >= limit:
                    break
                time.sleep(min(30, max(0.1, limit - elapsed)))
            if args.real_expiry:
                assert task["status"] == "expired"
                assert client.post(task_path, json=payload, headers={"X-Edit-Session": lease}).json()["status"] == "expired"
            assert client.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == original
            assert client.post(f"/api/rewrite-tasks/{job_id}/cancel").json()["status"] == "cancelled"
            proof.update(result="passed", real_clock_expiry=args.real_expiry, final_status="cancelled",
                         elapsed_seconds=round(time.monotonic() - started, 2), mock_http_calls=1,
                         original_preserved=True, finished_at=time.time())
            record()
    except Exception as exc:
        proof.update(result="failed", error_type=type(exc).__name__)
        record()
        raise
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
