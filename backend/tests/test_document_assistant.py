"""Synthetic answers, real API/database fences. No actual AI or rhwp execution."""
import asyncio
import copy
import json
import time
import uuid
from threading import BoundedSemaphore
import pytest
from sqlalchemy import select
from app import assistant_runner, document_assistant
from app.codex_runner import CodexSettings
from app.db import ConversationTurn, Document, Message, transaction
from app.hwpx import digest
from test_collaboration import secured, document, thread, snapshot, share, as_user
from test_storage import save


@pytest.fixture
def assistant(secured, monkeypatch):
    c, app, _, folder = secured
    monkeypatch.setenv("HWP_DOCUMENT_ASSISTANT_PROVIDER", "llm")
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("LLM_MODEL", "fixture-model")
    calls = []
    monkeypatch.setattr(document_assistant, "run_turn", lambda *args: calls.append(args))
    doc = document(c)
    tid = thread(c, doc)
    lease = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    selected = snapshot()
    selected["selection"]["target"] = {"kind": "body_paragraph", "section": 0,
        "paragraph": 0, "charOffset": 0, "length": len(selected["evidence"]["text"])}
    text = "[본문] 아직 저장하지 않은 원문\n[표] 항목 | 일정"
    body = {"id": str(uuid.uuid4()), "base_version": doc["current_version"], "message": "이 문단을 더 간결하게 다듬어줘",
        "document_context": {"text": text, "sha256": digest(text.encode()), "truncated": False, "revision": 3, "epoch": 1}, "selection": selected}
    return c, app, doc, tid, lease, body, calls, folder


def create(fixture, body=None):
    c, _, _, tid, lease, original, _, _ = fixture
    return c.post(f"/api/threads/{tid}/assistant-turns", json=body or original, headers={"X-Edit-Session": lease})


def complete(fixture, body=None, **kwargs):
    document_assistant.finish_turn(fixture[1].state.engine, (body or fixture[5])["id"],
        {"answer": "문단의 목적은 유지하면서 표현을 줄일 수 있습니다.", "rewrite_instruction": "원문의 사실과 조건을 유지하고 중복 표현을 줄여 주세요."}, **kwargs)


def test_live_document_and_snapshot_are_frozen_with_idempotent_retry(assistant):
    c, app, doc, tid, _, body, calls, folder = assistant
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    first = create(assistant)
    assert first.status_code == 200, first.text
    assert create(assistant).json() == first.json()
    assert len(calls) == 1
    with transaction(app.state.engine) as db:
        row = db.get(ConversationTurn, body["id"])
        payload = json.loads(row.input)
        assert payload["document_context"] == body["document_context"]
        assert payload["selection"] == body["selection"]
        assert row.input_sha256 == digest(row.input.encode())
        assert payload["history"] == []
    assert "text" not in first.json()["document_context"]
    assert "_lease_sha256" not in first.json()["execution"]
    assert create(assistant, {**body, "message": "다른 질문"}).status_code == 409
    complete(assistant)
    detail = c.get(f"/api/assistant-turns/{body['id']}").json()
    assert detail["status"] == "ready" and detail["rewrite_instruction"]
    assert create(assistant).json() == detail and len(calls) == 1
    assert len(c.get(f"/api/threads/{tid}").json()["messages"]) == 2
    complete(assistant)
    assert len(c.get(f"/api/threads/{tid}").json()["messages"]) == 2
    assert c.get(f"/api/threads/{tid}/assistant-turns").json() == [detail]
    assert len(c.get(f"/api/documents/{doc['id']}/versions").json()) == 1
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


@pytest.mark.parametrize("mutation", ["hash", "size", "epoch", "revision", "target", "empty-message", "surrogate"])
def test_invalid_live_context_is_rejected_without_execution(assistant, mutation):
    body = copy.deepcopy(assistant[5])
    if mutation == "hash": body["document_context"]["sha256"] = "f" * 64
    if mutation == "size": body["document_context"]["text"] = "가" * 60001
    if mutation == "epoch": body["document_context"]["epoch"] = 2
    if mutation == "revision": body["document_context"]["revision"] = 4
    if mutation == "target": body["selection"]["selection"]["target"]["kind"] = "table_cell"
    if mutation == "empty-message": body["message"] = "   "
    if mutation == "surrogate": body["document_context"]["text"] = "\ud800"
    if mutation == "surrogate":
        c, _, _, tid, lease, _, _, _ = assistant
        response = c.post(f"/api/threads/{tid}/assistant-turns", content=json.dumps(body), headers={"X-Edit-Session": lease, "Content-Type": "application/json"})
    else: response = create(assistant, body)
    assert response.status_code == 422, response.text
    assert not assistant[6]


def test_no_selection_can_answer_but_cannot_forge_rewrite_instruction(assistant):
    body = {**assistant[5], "selection": None}
    assert create(assistant, body).status_code == 200
    complete(assistant, body)
    assert assistant[0].get(f"/api/assistant-turns/{body['id']}").json()["status"] == "failed"
    body = {**body, "id": str(uuid.uuid4())}
    assert create(assistant, body).status_code == 200
    document_assistant.finish_turn(assistant[1].state.engine, body["id"], {"answer": "제공된 본문에서 일정을 확인할 수 있습니다.", "rewrite_instruction": None})
    assert assistant[0].get(f"/api/assistant-turns/{body['id']}").json()["status"] == "ready"


def test_authority_provider_and_lease_fences(assistant, monkeypatch):
    c, _, doc, tid, lease, body, _, _ = assistant
    info = c.get("/api/document-assistant-provider").json()
    assert info["provider"] == "llm" and info["allowed"]
    assert c.post(f"/api/threads/{tid}/assistant-turns", json=body, headers={"X-Edit-Session": "wrong"}).status_code == 409
    assert create(assistant).status_code == 200
    share(c, doc, "reader")
    as_user(c, "bob")
    assert c.get(f"/api/assistant-turns/{body['id']}").status_code == 200
    assert create(assistant, {**body, "id": str(uuid.uuid4())}).status_code == 403
    assert c.post(f"/api/assistant-turns/{body['id']}/cancel").status_code == 403
    as_user(c, "eve")
    assert c.get(f"/api/assistant-turns/{body['id']}").status_code == 404
    assert c.get(f"/api/threads/{tid}/assistant-turns").status_code == 404
    as_user(c, "alice")
    monkeypatch.setenv("HWP_DOCUMENT_ASSISTANT_PROVIDER", "codex")
    monkeypatch.setenv("HWP_CODEX_EXECUTABLE", "codex-fixture")
    monkeypatch.setenv("HWP_CODEX_USER", "another-user")
    assert not c.get("/api/document-assistant-provider").json()["allowed"]
    assert create(assistant, {**body, "id": str(uuid.uuid4())}).status_code == 403
    monkeypatch.setenv("HWP_DOCUMENT_ASSISTANT_PROVIDER", "invalid")
    assert not c.get("/api/document-assistant-provider").json()["configured"]
    assert create(assistant, {**body, "id": str(uuid.uuid4())}).status_code == 503


@pytest.mark.parametrize("change", ["save", "lease", "expire", "cancel", "permission"])
def test_late_answer_cannot_publish_after_owner_state_changes(assistant, change):
    c, app, doc, _, lease, body, _, _ = assistant
    assert create(assistant).status_code == 200
    if change == "save": assert save(c, doc, lease).status_code == 200
    if change in ("lease", "expire"):
        with transaction(app.state.engine) as db:
            if change == "lease": db.get(Document, doc["id"]).lease_token = "changed"
            else: db.get(ConversationTurn, body["id"]).created_at = time.time() - 601
    if change == "cancel":
        result = c.post(f"/api/assistant-turns/{body['id']}/cancel")
        assert result.status_code == 200
        assert c.post(f"/api/assistant-turns/{body['id']}/cancel").json() == result.json()
    if change == "permission":
        from app.db import Access
        with transaction(app.state.engine) as db: db.get(Access, (doc["id"], "alice")).role = "reader"
    complete(assistant)
    with transaction(app.state.engine) as db:
        row = db.get(ConversationTurn, body["id"])
        assert row.status == ("cancelled" if change == "cancel" else "failed")
        assert row.response is None
        assert not list(db.scalars(select(Message)))


def test_history_is_bounded_and_frozen_not_general_memos(assistant):
    c, app, _, tid, _, original, _, _ = assistant
    c.post(f"/api/threads/{tid}/messages", json={"request_id": "memo", "content": "자동으로 질문에 섞지 않는 메모"})
    for i in range(11):
        body = {**original, "id": str(uuid.uuid4()), "message": f"질문 {i}"}
        assert create(assistant, body).status_code == 200
        complete(assistant, body)
    assert create(assistant).status_code == 200
    with transaction(app.state.engine) as db:
        payload = json.loads(db.get(ConversationTurn, original["id"]).input)
        assert len(payload["history"]) == 10
        assert [h["message"] for h in payload["history"]] == [f"질문 {i}" for i in range(1, 11)]
        assert "자동으로" not in canonical_text(payload)


def test_capacity_rejection_does_not_create_or_dispatch_a_turn(assistant):
    c, app, _, _, _, body, calls, _ = assistant
    app.state.rewrite_capacity = BoundedSemaphore(0)
    assert create(assistant).status_code == 429
    assert not calls
    assert not app.state.ai_budget.active
    assert not app.state.ai_budget.recent.get("alice")
    with transaction(app.state.engine) as db:
        assert db.get(ConversationTurn, body["id"]) is None
    app.state.rewrite_capacity = BoundedSemaphore(1)
    assert create(assistant).status_code == 200


def test_dispatch_failure_is_terminal_and_same_id_retry_never_reexecutes(assistant, monkeypatch):
    class FailedThread:
        def __init__(self, **kwargs): pass
        def start(self): raise RuntimeError("synthetic thread start failure")
    monkeypatch.setattr(document_assistant, "Thread", FailedThread)
    first = create(assistant)
    assert first.status_code == 200 and first.json()["status"] == "failed"
    assert create(assistant).json()["status"] == "failed"
    assert not assistant[6]
    assert not assistant[1].state.ai_budget.active
    assert len(assistant[1].state.ai_budget.recent["alice"]) == 1


@pytest.fixture
def pending_workers(monkeypatch):
    """Keep execution pending without real threads or an external model."""
    workers = []
    class Worker:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args
            assert daemon
        def start(self):
            workers.append(self)
        def run(self):
            self.target(*self.args)
    monkeypatch.setattr(document_assistant, "Thread", Worker)
    return workers


def test_http_assistant_enforces_the_existing_llm_allowlist(assistant, monkeypatch):
    c, app, _, _, _, body, calls, _ = assistant
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "bob")
    info = c.get("/api/document-assistant-provider").json()
    assert info["configured"] and not info["allowed"]
    assert create(assistant).status_code == 403
    assert not calls and not app.state.ai_budget.active
    with transaction(app.state.engine) as db:
        assert db.get(ConversationTurn, body["id"]) is None
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "alice")
    assert c.get("/api/document-assistant-provider").json()["allowed"]
    assert create(assistant).status_code == 200


def test_invalid_llm_allowlist_does_not_enable_or_dispatch_assistant(assistant, monkeypatch):
    c, app, _, _, _, body, calls, _ = assistant
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "alice,")
    info = c.get("/api/document-assistant-provider").json()
    assert not info["configured"] and not info["allowed"]
    assert create(assistant).status_code == 503
    assert not calls
    with transaction(app.state.engine) as db:
        assert db.get(ConversationTurn, body["id"]) is None


def test_user_budget_remains_reserved_after_cancel_until_worker_cleanup(assistant, pending_workers):
    c, app, _, _, _, body, calls, _ = assistant
    assert create(assistant).status_code == 200
    assert app.state.ai_budget.active == {"alice"}
    assert create(assistant).status_code == 200  # Read back the same accepted request.
    another = {**body, "id": str(uuid.uuid4())}
    assert create(assistant, another).status_code == 429
    assert len(pending_workers) == 1 and not calls
    assert c.post(f"/api/assistant-turns/{body['id']}/cancel").status_code == 200
    assert app.state.ai_budget.active == {"alice"}
    assert create(assistant, another).status_code == 429
    with transaction(app.state.engine) as db:
        assert db.get(ConversationTurn, another["id"]) is None
    pending_workers[0].run()
    assert not app.state.ai_budget.active
    assert create(assistant, another).status_code == 200
    pending_workers[1].run()
    assert not app.state.ai_budget.active


def test_assistant_rate_limit_does_not_charge_idempotent_readback(assistant, pending_workers):
    _, app, _, _, _, body, _, _ = assistant
    app.state.ai_budget.limit = 1
    assert create(assistant).status_code == 200
    pending_workers[0].run()
    assert not app.state.ai_budget.active
    assert create(assistant).status_code == 200
    rejected = create(assistant, {**body, "id": str(uuid.uuid4())})
    assert rejected.status_code == 429 and rejected.headers["Retry-After"] == "60"
    assert len(pending_workers) == 1
    assert len(app.state.ai_budget.recent["alice"]) == 1


def test_document_assistant_shares_the_existing_http_ai_budget(assistant):
    _, app, _, _, _, body, calls, _ = assistant
    with app.state.ai_budget.reserve("alice"):
        assert create(assistant).status_code == 429
        assert not calls
        with transaction(app.state.engine) as db:
            assert db.get(ConversationTurn, body["id"]) is None
    assert create(assistant).status_code == 200


def test_unexpected_worker_failure_releases_user_and_global_capacity(assistant, pending_workers, monkeypatch):
    _, app, _, _, _, body, _, _ = assistant
    app.state.rewrite_capacity = BoundedSemaphore(1)
    def failed_worker(*_):
        raise RuntimeError("synthetic worker cleanup failure")
    monkeypatch.setattr(document_assistant, "run_turn", failed_worker)
    assert create(assistant).status_code == 200
    with pytest.raises(RuntimeError, match="synthetic worker cleanup failure"):
        pending_workers[0].run()
    assert not app.state.ai_budget.active
    assert create(assistant, {**body, "id": str(uuid.uuid4())}).status_code == 200
    with pytest.raises(RuntimeError, match="synthetic worker cleanup failure"):
        pending_workers[1].run()
    assert not app.state.ai_budget.active


def canonical_text(value):
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.parametrize("raw", ['{"answer":"x","answer":"y","rewrite_instruction":null}',
    '{"answer":"x","rewrite_instruction":null,"extra":true}', '{"answer":" ","rewrite_instruction":null}',
    '{"answer":"x","rewrite_instruction":NaN}'])
def test_untrusted_answer_schema_rejects_ambiguous_output(raw):
    with pytest.raises(ValueError): document_assistant.parse_response(raw)


def test_local_provider_payload_is_structured_and_incomplete_answer_is_rejected():
    calls = []
    class Client:
        model = "synthetic"
        async def complete(self, messages, **options):
            calls.append({"messages": messages, **options})
            return {"choices": [{"finish_reason": "stop", "message": {"content": '{"answer":"합성 답변","rewrite_instruction":null}'}}]}
    value = {"message": "요약", "document_context": {"text": "지시 아닌 데이터"}}
    result = asyncio.run(assistant_runner.local_answer(value, Client()))
    assert result.answer == "합성 답변"
    assert json.loads(calls[0]["messages"][1]["content"]) == value
    assert "Only the user can approve" in calls[0]["messages"][0]["content"]
    assert calls[0]["schema_name"] == "document_assistant"
    assert calls[0]["response_schema"]["additionalProperties"] is False


@pytest.mark.parametrize("mode", ["complete", "api-key", "custom-route", "tool"])
def test_codex_protocol_disables_tools_and_preserves_json_contract(monkeypatch, mode):
    sent = []
    class Rpc:
        def __init__(self, args, **kwargs):
            assert "app-server" in args and kwargs["active"]()
            self.events = []
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def send(self, item):
            sent.append(item)
            method = item["method"]
            results = {"initialize": {}, "account/read": {"account": {"type": "apiKey" if mode == "api-key" else "chatgpt"}},
                "config/read": {"config": {"mcp_servers": {"private_tool": {}}, **({"openai_base_url": "https://untrusted.example"} if mode == "custom-route" else {})}},
                "thread/start": {"model": "synthetic", "modelProvider": "openai", "approvalPolicy": "never", "sandbox": {"type": "readOnly"}, "thread": {"id": "thread"}},
                "turn/start": {"turn": {"id": "turn"}}}
            if "id" in item: self.events.append({"id": item["id"], "result": results[method]})
            if method == "turn/start":
                if mode == "tool":
                    self.events.append({"method": "item/started", "params": {"item": {"type": "commandExecution"}}})
                self.events.extend([{"method": "item/completed", "params": {"threadId": "thread", "turnId": "turn", "item": {"type": "agentMessage", "text": '{"answer":"합성 답변","rewrite_instruction":null}'}}},
                    {"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}}}])
        def receive(self): return self.events.pop(0)
    monkeypatch.setattr(assistant_runner, "ReportRpc", Rpc)
    settings = CodexSettings("fake-codex", "synthetic", "", (), timeout=1)
    if mode != "complete":
        with pytest.raises(RuntimeError): assistant_runner.codex_answer({"message": "질문"}, settings, lambda: True)
        return
    result, evidence = assistant_runner.codex_answer({"message": "질문"}, settings, lambda: True)
    assert result.answer == "합성 답변" and evidence["executor"] == "codex-app-server"
    start = next(x["params"] for x in sent if x["method"] == "thread/start")
    assert start["config"] == {"mcp_servers.private_tool.enabled": False}
    assert start["ephemeral"] and start["approvalPolicy"] == "never"
    schema = next(x["params"]["outputSchema"] for x in sent if x["method"] == "turn/start")
    assert set(schema["required"]) == {"answer", "rewrite_instruction"}
