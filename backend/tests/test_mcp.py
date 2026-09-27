"""Real stdio MCP SDK client + child server + authenticated HTTP API. No model calls."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import pytest
import httpx
pytest.importorskip("mcp", reason="Install backend/requirements-mcp.lock.txt for MCP process tests")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_collaboration import TOKENS, document, thread, share, context


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
