"""Real stdio MCP SDK client + child server + authenticated HTTP API. No model calls."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
import pytest
import httpx
pytest.importorskip("mcp", reason="Install backend/requirements-mcp.lock.txt for MCP process tests")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_collaboration import TOKENS, document, thread, share, context, secured
from test_rewrite import job


def test_real_stdio_mcp_tools_and_permission_revalidation(tmp_path):
    root = Path(__file__).resolve().parents[2]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    environment = dict(os.environ, DATABASE_URL=f"sqlite:///{tmp_path}/mcp.db", STORAGE_DIR=str(tmp_path / "files"),
                       HWP_AUTH_TOKENS=json.dumps(TOKENS), PYTHONPATH=str(root / "backend"))
    with (tmp_path / "server.log").open("w") as logs:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                                   cwd=root, env=environment, stdout=logs, stderr=logs)
        try:
            with httpx.Client(base_url=base, trust_env=False, timeout=3,
                              headers={"Authorization": "Bearer " + TOKENS["alice"]}) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    assert process.poll() is None
                    try:
                        if client.get("/api/health").is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("API startup timed out")
                doc = document(client)
                shared = thread(client, doc)
                private = thread(client, doc, "private")
                context(client, doc, shared)
                share(client, doc)

                async def run():
                    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(root),
                              env={**environment, "HWP_API_URL": base, "HWP_API_TOKEN": TOKENS["bob"]})
                    async with stdio_client(params) as (read, write):
                        async with ClientSession(read, write) as session:
                            initialized = await session.initialize()
                            assert initialized.serverInfo.name == "hwp-assistant"
                            tools = await session.list_tools()
                            assert {t.name for t in tools.tools} == {"list_documents", "list_conversations", "read_context", "append_reply", "propose_text", "recommend_templates"}
                            assert not (await session.call_tool("list_documents", {})).isError
                            result = await session.call_tool("read_context", {"conversation_id": shared})
                            assert not result.isError and "확정 사실" in result.content[0].text
                            assert not (await session.call_tool("read_context", {"conversation_id": private})).isError
                            result = await session.call_tool("append_reply", {"conversation_id": shared, "content": "MCP 합성 응답", "request_id": "stdio-once"})
                            assert not result.isError
                            result = await session.call_tool("propose_text", {"conversation_id": shared, "context_revision": 1, "replacement": "제안문", "reason": "MCP 시험"})
                            assert not result.isError and "pending_user_approval" in result.content[0].text
                            # A reader can inspect another author's pending candidate, but cannot write.
                            client.put(f"/api/documents/{doc['id']}/access", json={"user_id": "bob", "role": "reader"})
                            result = await session.call_tool("read_context", {"conversation_id": shared})
                            assert not result.isError and "제안문" in result.content[0].text
                            assert "rewrite_tasks" in result.content[0].text and "applications" in result.content[0].text
                            assert (await session.call_tool("append_reply", {"conversation_id": shared, "content": "reader write", "request_id": "blocked"})).isError
                            client.delete(f"/api/documents/{doc['id']}/access/bob")
                            assert (await session.call_tool("read_context", {"conversation_id": shared})).isError
                            assert (await session.call_tool("propose_text", {"conversation_id": shared, "context_revision": 1, "replacement": "차단", "reason": "권한 회수"})).isError
                asyncio.run(run())
                assert len(client.get(f"/api/documents/{doc['id']}/versions").json()) == 1
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_real_task_scoped_stdio_contract_and_cancellation(job):
    """Real SDK process/HTTP; fixed Codex executor and candidates, no model invocation."""
    import threading
    import uvicorn
    client, app, doc, conversation, _, body, token = job
    root = Path(__file__).resolve().parents[2]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", loop="asyncio"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started

            async def run():
                params = StdioServerParameters(command=sys.executable, args=["-m", "app.rewrite_mcp"], cwd=str(root),
                    env={**os.environ, "PYTHONPATH": str(root / "backend"), "HWP_API_URL": base, "HWP_API_TOKEN": token})
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        assert initialized.serverInfo.name == "hwp-document-rewrite"
                        tools = (await session.list_tools()).tools
                        assert {tool.name for tool in tools} == {"read_rewrite_task", "submit_rewrite_candidates"}
                        schema = next(tool.inputSchema for tool in tools if tool.name == "submit_rewrite_candidates")
                        item = schema["properties"]["candidates"]["items"]
                        if "$ref" in item:
                            item = schema["$defs"][item["$ref"].split("/")[-1]]
                        assert item["properties"]["replacement"]["maxLength"] == 4000
                        assert item["additionalProperties"] is False
                        result = await session.call_tool("read_rewrite_task", {"task_id": body["id"]})
                        assert not result.isError
                        assert json.loads(result.content[0].text)["snapshot"] == body["selection"]
                        assert (await session.call_tool("read_rewrite_task", {"task_id": str(uuid.uuid4())})).isError
                        assert (await session.call_tool("list_documents", {})).isError
                        candidate = {"replacement": "고정 MCP 후보 하나", "reason": "실제 모델이 아닌 전송 시험"}
                        arguments = {"task_id": body["id"], "original_sha256": body["selection"]["evidence"]["textSha256"],
                                     "candidates": [candidate]}
                        assert (await session.call_tool("submit_rewrite_candidates", arguments)).isError
                        arguments["candidates"].append({"replacement": "고정 MCP 후보 둘", "reason": "전송 시험"})
                        result = await session.call_tool("submit_rewrite_candidates", arguments)
                        assert not result.isError
                        expected = json.loads(result.content[0].text)
                        assert len(expected["candidate_ids"]) == 2 and expected["document_changed"] is False
                        replay = await session.call_tool("submit_rewrite_candidates", arguments)
                        assert not replay.isError and json.loads(replay.content[0].text) == expected
                        before = client.get(f"/api/threads/{conversation}/rewrite-tasks").json()[0]
                        assert before["status"] == "running" and len(before["candidates"]) == 2
                        assert client.post(f"/api/rewrite-tasks/{body['id']}/cancel").status_code == 200
                        assert (await session.call_tool("read_rewrite_task", {"task_id": body["id"]})).isError
                        assert (await session.call_tool("submit_rewrite_candidates", arguments)).isError
                        after = client.get(f"/api/threads/{conversation}/rewrite-tasks").json()[0]
                        assert after["status"] == "cancelled" and after["candidates"] == before["candidates"]
            asyncio.run(run())
            assert len(client.get(f"/api/documents/{doc['id']}/versions").json()) == 1
            assert client.get(f"/api/documents/{doc['id']}/applications").json() == []
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            assert not worker.is_alive(), "test HTTP server must stop"
