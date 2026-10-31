"""Real uvicorn process restart; storage fixture is synthetic, not engine validation."""

import os
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
import httpx
from test_storage import package


def test_real_server_process_restart(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[2]
    environment = dict(
        os.environ,
        DATABASE_URL=f"sqlite:///{tmp_path}/metadata.db",
        STORAGE_DIR=str(tmp_path / "files"),
    )

    @contextmanager
    def running():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--app-dir",
                "backend",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2
            ) as client:
                for _ in range(100):
                    assert process.poll() is None, "uvicorn exited during startup"
                    try:
                        if client.get("/api/health").is_success:
                            break
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("server startup timed out")
                yield client
        finally:
            process.terminate()
            process.wait(timeout=10)

    data = package("서버 재시작 후에도 유지")
    with running() as client:
        response = client.post(
            "/api/documents",
            params={"name": "restart.hwpx", "document_id": str(uuid.uuid4())},
            content=data,
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 200, response.text
        doc = response.json()
        listed = client.get("/api/documents").json()[0]
        saved = client.get(f"/api/documents/{doc['id']}/versions").json()[0]
        assert listed == {**doc, "can_trash": True, "branch_source_id": None,
                          "workspace_id": None, "workspace_name": None, "shared_with_me": False,
                          "personal_document": False, "owned_by_me": True,
                          "individually_shared": False, "folder_id": None,
                          "folder_path": [], "workspace_folder_id": None, "workspace_folder_path": [],
                          "confidential": False, "confidential_revision": None, "can_set_confidential": False,
                          "modified_at": saved["created_at"]}
        tid = str(uuid.uuid4())
        assert client.post(f"/api/documents/{doc['id']}/threads", json={"id": tid, "title": "재시작 기록", "visibility": "private"}).is_success
        assert client.put(f"/api/threads/{tid}/context", json={"expected_revision": 0, "base_version": doc['current_version'], "facts": "영속 사실"}).is_success
        assert client.post(f"/api/threads/{tid}/messages", json={"request_id": "restart", "content": "보존할 대화"}).is_success
    with running() as client:
        assert client.get("/api/documents").json()[0] == listed
        persisted = client.get(f"/api/threads/{tid}").json()
        assert persisted["context"]["facts"] == "영속 사실"
        assert persisted["messages"][0]["content"] == "보존할 대화"
        assert (
            client.get(
                f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file"
            ).content
            == data
        )

