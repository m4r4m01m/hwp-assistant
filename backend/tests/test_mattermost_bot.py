"""Synthetic bot/server identities only. Never calls Mattermost or a real model."""
import asyncio
import copy
import json
import re
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import mattermost as bot, mattermost_worker as worker, reports
from app.account_admin import maintain
from app.accounts import UserAccount
from app.db import Document, ReportTask, transaction
from app.main import create_app
from app.mattermost_store import MattermostChallenge, MattermostLink, MattermostRequest
from test_reports import DRAFT

MM_USER, MM_OTHER, TEAM, CHANNEL = "u" * 26, "v" * 26, "t" * 26, "c" * 26
COMMAND_TOKEN = "k" * 26
PASSWORD = "synthetic-bot-fixture-password"
REAL_BOT_LOOP = worker.bot_loop


class FakeBot:
    messages = []
    dialogs = []
    def __init__(self, *_): pass
    async def send_dm(self, user, message, *, before_send=None):
        if before_send:
            message = await before_send()
            if message is None:
                return None
        self.messages.append((user, message))
        return "p" * 26
    async def open_dialog(self, trigger, callback, state, elements):
        self.dialogs.append(dict(trigger=trigger, callback=callback, state=state, elements=elements))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for key, value in {
        "HWP_ACCOUNTS_ENABLED": "1", "HWP_AUTH_TOKENS": "", "HWP_CODEX_ACCOUNT_ACCESS": "approved_accounts",
        "HWP_CODEX_EXECUTABLE": "synthetic-not-executed", "HWP_CODEX_CHECK_AUTH": "0",
        "HWP_MATTERMOST_BOT_ENABLED": "1", "HWP_MATTERMOST_URL": "https://mattermost.example.invalid/mattermost",
        "HWP_MATTERMOST_PUBLIC_APP_URL": "https://hwp.example.invalid/hwp-assistant/",
        "HWP_MATTERMOST_BOT_TOKEN": "b" * 26, "HWP_MATTERMOST_COMMAND_TOKEN": COMMAND_TOKEN,
        "HWP_MATTERMOST_TEAM_ID": TEAM, "HWP_LIBRARY_ACCESS_MODE": "private",
        "HWP_CODEX_DAILY_REQUEST_LIMIT": "10", "HWP_REGISTRATION_ENABLED": "0",
    }.items(): monkeypatch.setenv(key, value)
    monkeypatch.delenv("HWP_GUEST_PASSWORD", raising=False)
    monkeypatch.delenv("HWP_SEARCH_EMBEDDING_URL", raising=False)
    monkeypatch.delenv("HWP_MATTERMOST_CALLBACK_URL", raising=False)
    monkeypatch.delenv("HWP_MATTERMOST_BOT_ALLOW_HTTP", raising=False)
    async def idle(_app, stop):
        await stop.wait()
    async def search_idle(_engine, _folder, stop):
        await stop.wait()
    monkeypatch.setattr(worker, "bot_loop", idle)
    from app import document_search
    monkeypatch.setattr(document_search, "search_index_loop", search_idle)
    FakeBot.messages, FakeBot.dialogs = [], []
    monkeypatch.setattr(bot, "BotClient", FakeBot)
    monkeypatch.setattr(worker, "BotClient", FakeBot)
    monkeypatch.setattr(reports, "generate_report", lambda *_: (copy.deepcopy({**DRAFT, "notes": []}), {"completed": True, "executor": "synthetic"}))
    app = create_app("sqlite:///" + str(tmp_path / "bot.db"), tmp_path / "files")
    with TestClient(app) as client:
        for user in ("alice", "bob", "guest"):
            maintain(app.state.engine, "create", user, password=PASSWORD, name="합성 사용자", employee="SYNTHETIC-" + user)
        yield app, client


def command(client, content, *, mm_user=MM_USER, trigger=None, **extra):
    value = dict(token=COMMAND_TOKEN, team_id=TEAM, channel_id=CHANNEL, user_id=mm_user,
                 command="/hwp", text=content, trigger_id=trigger or "synthetic-trigger-" + str(uuid.uuid4()))
    return client.post(bot.COMMAND_PATH, data={**value, **extra})


def login(client, user="alice"):
    assert client.post("/api/auth/password-login", json={"username": user, "password": PASSWORD}).status_code == 200


def link(client, user="alice", mm_user=MM_USER):
    response = command(client, "연결", mm_user=mm_user)
    assert response.status_code == 200
    token = re.search(r"mattermost-link=([\w-]+)", response.json()["text"])[1]
    login(client, user)
    assert client.post("/api/mattermost/link", json={"token": token}).json() == {"linked": True}
    return token


def request_id(response):
    assert response.status_code == 200, response.text
    found = re.search(r"`([a-f0-9-]{36})`", response.json()["text"])
    assert found, response.text
    return found[1]


def process(app, request_id):
    with transaction(app.state.engine) as db:
        db.get(MattermostRequest, request_id).next_attempt_at = 0
    asyncio.run(worker.process_request(app, request_id))


def wait_report(app, task_id):
    for _ in range(200):
        with transaction(app.state.engine) as db:
            row = db.get(ReportTask, task_id)
            if row and row.status != "running":
                return row.status
        time.sleep(.01)
    raise AssertionError("synthetic report did not finish")


def test_disabled_bot_does_not_exempt_other_api(setup, monkeypatch):
    _, client = setup
    monkeypatch.setenv("HWP_MATTERMOST_BOT_ENABLED", "0")
    assert command(client, "검색 자료").status_code == 404
    assert client.get(bot.COMMAND_PATH).status_code == 401
    assert client.post("/api/report-tasks", data={"token": COMMAND_TOKEN}).status_code == 401
    assert client.get("/api/documents").status_code == 401
    login(client)
    assert client.get("/api/mattermost/link").json() == {"enabled": False, "linked": False}


@pytest.mark.parametrize("extra", [dict(token="incorrect"), dict(token="잘못된 토큰"), dict(team_id="z" * 26), dict(user_id="../alice"), dict(command="/other")])
def test_untrusted_ingress_never_queues(setup, extra):
    app, client = setup
    assert command(client, "検索 data", **extra).status_code in {401, 403}
    with transaction(app.state.engine) as db:
        assert list(db.scalars(select(MattermostRequest))) == []


def test_bounded_input_and_no_validation_secret_echo(setup):
    _, client = setup
    response = client.post(bot.COMMAND_PATH, content="token=" + "x" * 33000, headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 413 and "xxxxx" not in response.text
    assert client.post(bot.COMMAND_PATH, content="token=a&token=b", headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 422
    secret = "private-link-token-" + "s" * 40
    login(client)
    response = client.post("/api/mattermost/link", json={"token": secret, "extra": "hidden"})
    assert response.status_code == 422 and secret not in response.text and "hidden" not in response.text


def test_link_requires_confirmation_and_is_one_to_one(setup):
    app, client = setup
    token = re.search(r"mattermost-link=([\w-]+)", command(client, "연결").json()["text"])[1]
    with transaction(app.state.engine) as db:
        assert db.scalar(select(MattermostLink)) is None
        assert db.get(MattermostChallenge, bot.fingerprint(token)) is not None
    assert client.post("/api/mattermost/link", json={"token": token}).status_code == 401
    login(client)
    assert client.post("/api/mattermost/link", json={"token": token}).json() == {"linked": True}
    assert client.post("/api/mattermost/link", json={"token": token}).json() == {"linked": True}
    assert client.get("/api/mattermost/link").json()["linked"] is True
    login(client, "bob")
    assert client.post("/api/mattermost/link", json={"token": token}).status_code == 409
    login(client)
    assert client.delete("/api/mattermost/link").json() == {"linked": False}
    assert client.post("/api/mattermost/link", json={"token": token}).status_code == 409


def test_expired_pending_and_guest_account_cannot_link(setup):
    app, client = setup
    token = re.search(r"mattermost-link=([\w-]+)", command(client, "연결").json()["text"])[1]
    login(client, "guest")
    assert client.post("/api/mattermost/link", json={"token": token}).status_code == 403
    login(client)
    with transaction(app.state.engine) as db:
        db.get(MattermostChallenge, bot.fingerprint(token)).expires_at = 0
    assert client.post("/api/mattermost/link", json={"token": token}).status_code == 409
    with transaction(app.state.engine) as db:
        db.get(UserAccount, "alice").approval_status = "pending"
    assert client.get("/api/mattermost/link").status_code == 401


def test_search_request_is_scoped_idempotent_and_ignores_callback_url(setup, monkeypatch):
    app, client = setup
    assert "먼저" in command(client, "검색 장애 대응").json()["text"]
    link(client)
    response = command(client, "검색 장애 대응", trigger="synthetic-stable-trigger", response_url="https://evil.invalid/secret")
    rid = request_id(response)
    assert request_id(command(client, "검색 장애 대응", trigger="synthetic-stable-trigger")) == rid
    assert "기존 요청과 다릅니다" in command(client, "검색 다른 내용", trigger="synthetic-stable-trigger").json()["text"]
    from app import document_search
    observed = []
    def search(engine, folder, user, query, **kwargs):
        observed.append((user, query)); return {"results": [], "semantic_available": False}
    monkeypatch.setattr(document_search, "search_documents", search)
    process(app, rid)
    assert observed == [("alice", "장애 대응")]
    assert FakeBot.messages[0][0] == MM_USER and "단어 검색" in FakeBot.messages[0][1]
    process(app, rid)
    assert len(FakeBot.messages) == 1
    login(client, "bob")
    assert "먼저" in command(client, "내요청", mm_user=MM_OTHER).json()["text"]


def test_search_actual_index_excerpts_and_trash_fence(setup):
    app, client = setup
    link(client)
    from test_document_search import document as add_document
    from app.document_search import refresh_search_index
    from app.db import DocumentTrash
    corpus = (app.state.engine, app.state.folder)
    doc, version = add_document(corpus, "운영 절차", "장애 발생 시 담당자가 복구 절차를 수행함.", "alice")
    private, _ = add_document(corpus, "타인 비밀", "장애 시 비밀 연락처 사용", "bob")
    refresh_search_index(*corpus, max_documents=10)
    rid = request_id(command(client, "검색 장애"))
    process(app, rid)
    message = FakeBot.messages[-1][1]
    assert doc in message and version in message and "복구 절차" in message
    assert private not in message and "비밀 연락처" not in message
    with transaction(app.state.engine) as db:
        db.add(DocumentTrash(id=str(uuid.uuid4()), document_id=doc, deleted_by="alice", deleted_at=time.time()))
    rid = request_id(command(client, "검색 장애"))
    process(app, rid)
    assert doc not in FakeBot.messages[-1][1]


def test_new_report_uses_v6_standard_and_never_saves_document(setup):
    app, client = setup
    link(client)
    rid = request_id(command(client, "초안 다음 달 점검 계획을 작성해 주세요.", trigger="synthetic-report-trigger"))
    process(app, rid)
    assert wait_report(app, rid) == "ready"
    process(app, rid)
    assert "내용이 준비" in FakeBot.messages[-1][1] and "?report=" + rid in FakeBot.messages[-1][1]
    with transaction(app.state.engine) as db:
        task = db.get(ReportTask, rid)
        value = json.loads(task.input)
        assert value["visual_standard"]["revision"] == 3 and value["template"]["id"]
        assert value["research"] is False
        assert json.loads(task.execution)["contract"] == "korean-report-v6-explanatory-notes"
        assert json.loads(task.draft)["notes"] == []
        assert db.scalar(select(Document)) is None
        from app.codex_runtime import CodexUsage
        assert len(db.scalars(select(CodexUsage)).all()) == 1
    assert request_id(command(client, "초안 다음 달 점검 계획을 작성해 주세요.", trigger="synthetic-report-trigger")) == rid
    login(client, "bob")
    assert client.get("/api/report-tasks/" + rid).status_code == 404


def test_dialog_nonce_scopes_user_and_duplicate_submission(setup):
    app, client = setup
    link(client)
    assert "입력창" in command(client, "초안").json()["text"]
    dialog = FakeBot.dialogs[-1]
    assert dialog["elements"][0]["max_length"] == 3000
    assert dialog["callback"] == "https://hwp.example.invalid/hwp-assistant/api/integrations/mattermost/dialog"
    payload = dict(state=dialog["state"], user_id=MM_USER, team_id=TEAM, channel_id=CHANNEL, callback_id="hwp-report",
                   submission={"prompt": "합성 자료를 바탕으로 점검 보고서 작성", "preset": "appendix-2", "research": "off"})
    assert client.post(bot.DIALOG_PATH, json={**payload, "user_id": MM_OTHER}).status_code == 401
    for field in ("preset", "research"):
        malformed = {**payload, "submission": {**payload["submission"], field: []}}
        assert "errors" in client.post(bot.DIALOG_PATH, json=malformed).json()
    too_long = {**payload, "submission": {**payload["submission"], "prompt": "가" * 3001}}
    assert "errors" in client.post(bot.DIALOG_PATH, json=too_long).json()
    assert client.post(bot.DIALOG_PATH, json=payload).json() == {}
    assert client.post(bot.DIALOG_PATH, json=payload).json() == {}
    with transaction(app.state.engine) as db:
        rows = db.scalars(select(MattermostRequest)).all()
        assert len(rows) == 1 and json.loads(rows[0].payload)["visual_standard"]["settings"]["preset_id"] == "appendix-2"
    assert client.delete("/api/mattermost/link").status_code == 200
    link(client)
    assert client.post(bot.DIALOG_PATH, json=payload).status_code == 409


def test_internal_dialog_callback_preserves_public_links_and_identity_fences(setup, monkeypatch):
    app, client = setup
    callback = "http://hwp-api:8000/api/integrations/mattermost/dialog"
    monkeypatch.setenv("HWP_MATTERMOST_BOT_ALLOW_HTTP", "1")
    monkeypatch.setenv("HWP_MATTERMOST_CALLBACK_URL", callback)
    response = command(client, "연결")
    assert response.status_code == 200
    assert "https://hwp.example.invalid/hwp-assistant/#mattermost-link=" in response.json()["text"]
    assert "hwp-api" not in response.json()["text"]
    token = re.search(r"mattermost-link=([\w-]+)", response.json()["text"])[1]
    login(client)
    assert client.post("/api/mattermost/link", json={"token": token}).json() == {"linked": True}
    assert "입력창" in command(client, "초안").json()["text"]
    dialog = FakeBot.dialogs[-1]
    assert dialog["callback"] == callback
    payload = dict(state=dialog["state"], user_id=MM_USER, team_id=TEAM, channel_id=CHANNEL, callback_id="hwp-report",
                   submission={"prompt": "합성 내부 연결 점검 보고서를 작성해 주세요.", "preset": "appendix-1", "research": "off"})
    for invalid in (
        {"state": "s" * 43}, {"user_id": MM_OTHER}, {"team_id": "x" * 26},
        {"channel_id": "d" * 26}, {"callback_id": "other-report"},
    ):
        assert client.post(bot.DIALOG_PATH, json={**payload, **invalid}).status_code == 401
    with transaction(app.state.engine) as db:
        assert list(db.scalars(select(MattermostRequest))) == []
    assert client.post(bot.DIALOG_PATH, json=payload).json() == {}
    assert client.post(bot.DIALOG_PATH, json=payload).json() == {}
    with transaction(app.state.engine) as db:
        rows = db.scalars(select(MattermostRequest)).all()
        assert len(rows) == 1 and rows[0].user_id == "alice"
        rid = rows[0].id
    process(app, rid)
    assert wait_report(app, rid) == "ready"
    process(app, rid)
    message = FakeBot.messages[-1][1]
    assert "https://hwp.example.invalid/hwp-assistant/?report=" + rid in message
    assert "hwp-api" not in message
    assert "https://hwp.example.invalid/hwp-assistant/?report=" + rid in command(client, "내요청").json()["text"]


def test_cancel_and_unlink_prevent_background_creation(setup):
    app, client = setup
    link(client)
    rid = request_id(command(client, "초안 합성 보안 점검 계획 작성"))
    assert "확인했습니다" in command(client, "취소 " + rid).json()["text"]
    process(app, rid)
    with transaction(app.state.engine) as db:
        assert db.get(ReportTask, rid) is None
    rid = request_id(command(client, "초안 합성 보안 점검 계획 작성"))
    client.delete("/api/mattermost/link")
    process(app, rid)
    with transaction(app.state.engine) as db:
        assert db.get(MattermostRequest, rid).status == "suppressed" and db.get(ReportTask, rid) is None
    assert FakeBot.messages == []


def test_revocation_during_search_prevents_private_delivery(setup, monkeypatch):
    app, client = setup
    link(client)
    rid = request_id(command(client, "검색 점검 자료"))
    from app import document_search
    def revoked(*args, **kwargs):
        with transaction(app.state.engine) as db:
            db.get(UserAccount, "alice").is_active = False
        return {"results": [], "semantic_available": True}
    monkeypatch.setattr(document_search, "search_documents", revoked)
    process(app, rid)
    assert FakeBot.messages == []


def test_admission_rechecks_cancel_and_link_generation(setup):
    app, client = setup
    link(client)
    rid = request_id(command(client, "초안 합성 문서 작성 요청입니다."))
    item = worker.claim(app.state.engine, bot.configured(), rid)
    command(client, "취소 " + rid)
    with pytest.raises(Exception):
        worker.prepare_report(app, bot.configured(), item)
    with transaction(app.state.engine) as db:
        assert db.get(ReportTask, rid) is None


def test_uncertain_delivery_is_not_repeated_or_regenerated(setup, monkeypatch):
    app, client = setup
    link(client)
    rid = request_id(command(client, "검색 자료 검색"))
    async def uncertain(*_, before_send=None):
        await before_send()
        raise RuntimeError("synthetic-secret")
    monkeypatch.setattr(FakeBot, "send_dm", uncertain)
    process(app, rid)
    process(app, rid)
    with transaction(app.state.engine) as db:
        row = db.get(MattermostRequest, rid)
        assert row.status == "notify_failed" and row.attempts == 1
        assert "secret" not in row.outcome
    assert "알림 전송 확인 필요" in command(client, "내요청").json()["text"]


def test_search_rate_limit_keeps_valid_requests_bounded(setup):
    _, client = setup
    statuses = [command(client, "도움말").status_code for _ in range(25)]
    assert 429 in statuses


def test_changed_team_requires_old_link_to_be_removed(setup, monkeypatch):
    _, client = setup
    link(client)
    monkeypatch.setenv("HWP_MATTERMOST_TEAM_ID", "n" * 26)
    response = command(client, "연결", team_id="n" * 26)
    token = re.search(r"mattermost-link=([\w-]+)", response.json()["text"])[1]
    assert client.post("/api/mattermost/link", json={"token": token}).status_code == 409
    assert client.delete("/api/mattermost/link").status_code == 200
    assert client.post("/api/mattermost/link", json={"token": token}).status_code == 200


@pytest.mark.parametrize("change", ["unlink", "deactivate", "cancel", "trash", "version"])
def test_dm_preflight_rechecks_private_result(setup, monkeypatch, change):
    app, client = setup
    link(client)
    from test_document_search import document, new_version
    from app.document_search import refresh_search_index
    from app.db import DocumentTrash
    corpus = (app.state.engine, app.state.folder)
    doc, _ = document(corpus, "합성 점검", "비공개 점검 절차 내용", "alice")
    refresh_search_index(*corpus)
    rid = request_id(command(client, "검색 점검"))
    original_send = FakeBot.send_dm
    async def delayed_send(self, user, message, *, before_send=None):
        # Simulate a real state change during remote bot/DM preflight.
        if change == "version":
            new_version(corpus, doc, "대체된 다른 본문")
        else:
            with transaction(app.state.engine) as db:
                if change == "unlink":
                    db.delete(db.scalar(select(MattermostLink)))
                elif change == "deactivate":
                    db.get(UserAccount, "alice").is_active = False
                elif change == "cancel":
                    db.get(MattermostRequest, rid).status = "cancelled"
                else:
                    db.add(DocumentTrash(id=str(uuid.uuid4()), document_id=doc, deleted_by="alice", deleted_at=time.time()))
        return await original_send(self, user, message, before_send=before_send)
    monkeypatch.setattr(FakeBot, "send_dm", delayed_send)
    process(app, rid)
    if change in {"unlink", "deactivate", "cancel"}:
        assert FakeBot.messages == []
    else:
        assert doc not in FakeBot.messages[-1][1] and "비공개" not in FakeBot.messages[-1][1]


def test_failed_report_admission_stays_failed_after_notification(setup, monkeypatch):
    app, client = setup
    link(client)
    monkeypatch.setattr(reports, "codex_allowed", lambda _: False)
    rid = request_id(command(client, "초안 실행 권한이 없는 합성 보고서 작성"))
    process(app, rid)
    with transaction(app.state.engine) as db:
        assert db.get(MattermostRequest, rid).status == "failed"
        assert db.get(ReportTask, rid) is None
    assert "실패" in command(client, "내요청").json()["text"]


def test_preflight_failure_stops_without_post_or_repeated_attempts(setup, monkeypatch):
    app, client = setup
    link(client)
    rid = request_id(command(client, "검색 자료 검색"))
    async def unavailable(*_, **kwargs):
        raise RuntimeError("synthetic preflight failure")
    monkeypatch.setattr(FakeBot, "send_dm", unavailable)
    process(app, rid)
    process(app, rid)
    with transaction(app.state.engine) as db:
        row = db.get(MattermostRequest, rid)
        assert row.status == "notify_failed" and row.attempts == 1
        assert row.outcome == "delivery_preflight_failed"
    assert FakeBot.messages == []


def test_ready_task_survives_dispatcher_restart_without_new_ai_admission(setup, monkeypatch):
    app, client = setup
    link(client)
    rid = request_id(command(client, "초안 재시작 복구 시험용 보고서 작성"))
    item = worker.claim(app.state.engine, bot.configured(), rid)
    worker.prepare_report(app, bot.configured(), item)
    assert wait_report(app, rid) == "ready"
    monkeypatch.setattr(reports, "codex_allowed", lambda _: False)
    process(app, rid)
    assert "내용이 준비" in FakeBot.messages[-1][1]
    with transaction(app.state.engine) as db:
        from app.codex_runtime import CodexUsage
        assert len(db.scalars(select(CodexUsage)).all()) == 1


def test_reconnected_account_cannot_enqueue_old_inflight_command(setup):
    app, client = setup
    link(client)
    config = bot.configured()
    with transaction(app.state.engine) as db:
        generation = bot.linked_account(db, config, MM_USER).generation
    client.delete("/api/mattermost/link")
    link(client)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        bot.enqueue(app.state.engine, config, MM_USER, bot.fingerprint("synthetic"), "search", {"query": "점검"}, generation)
    assert failure.value.status_code == 409


def test_shutdown_drains_current_dispatch_without_claiming_next_request(setup, monkeypatch):
    app, client = setup
    link(client)
    first = request_id(command(client, "검색 첫 번째 자료"))
    second = request_id(command(client, "검색 두 번째 자료"))
    processed = []
    async def scenario():
        stop, started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def delayed(_app, rid, _config):
            processed.append(rid)
            started.set()
            await release.wait()
        monkeypatch.setattr(worker, "process_request", delayed)
        dispatcher = asyncio.create_task(REAL_BOT_LOOP(app, stop))
        await asyncio.wait_for(started.wait(), 2)
        stop.set()
        await asyncio.sleep(0)
        assert not dispatcher.done()
        release.set()
        await asyncio.wait_for(dispatcher, 2)
    asyncio.run(scenario())
    assert processed == [first] and second not in processed
