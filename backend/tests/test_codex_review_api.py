"""Real review API fences/admission with synthetic providers; no model calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import codex_chat_review, codex_runtime
from app.db import Access, Document, LeaseOwner, Version, transaction
from app.hwpx import digest
from app.llm import Suggestion
from test_collaboration import secured, document
from test_storage import save


@pytest.fixture
def review_api(secured, monkeypatch):
    client, app, _, _ = secured
    monkeypatch.setenv("HWP_REVIEW_PROVIDER", "codex")
    monkeypatch.setenv("HWP_CODEX_USER", "alice")
    monkeypatch.delenv("HWP_CODEX_ALLOWED_USERS", raising=False)
    monkeypatch.setenv("HWP_LLM_ALLOWED_USERS", "someone-else")
    monkeypatch.setenv("HWP_CODEX_DAILY_REQUEST_LIMIT", "1")
    app.state.rewrite_capacity = BoundedSemaphore(1)
    doc = document(client)
    lease = client.post(f"/api/documents/{doc['id']}/session").json()["token"]
    body = {"document_id": doc["id"], "base_version": doc["current_version"],
            "revision": 3, "original": "합성 원문", "target": {"paragraphIndex": 0}}
    calls = []

    class Provider:
        async def review(self, original, *, active):
            assert active()
            calls.append(original)
            return Suggestion(replacement="합성 수정 제안", reason="표현 개선")

    monkeypatch.setattr(codex_chat_review, "capture_provider", lambda name, factory: Provider())

    def submit():
        return client.post("/api/ai/review", json=body, headers={"X-Edit-Session": lease})

    def usage():
        with transaction(app.state.engine) as db:
            return [(row.request_key, row.user_id) for row in db.scalars(select(codex_runtime.CodexUsage))]

    return client, app, doc, lease, body, submit, usage, calls


def test_codex_review_uses_codex_acl_and_returns_existing_suggestion_shape_without_saving(review_api):
    client, app, doc, _, body, submit, usage, calls = review_api
    response = submit()
    assert response.status_code == 200, response.text
    assert response.json() == {**body, "replacement": "합성 수정 제안", "reason": "표현 개선",
                              "original_sha256": digest(body["original"].encode())}
    assert calls == [body["original"]]
    rows = usage()
    assert len(rows) == 1 and rows[0][0].startswith("review:") and rows[0][1] == "alice"
    assert client.get(f"/api/documents/{doc['id']}").json()["current_version"] == doc["current_version"]
    with transaction(app.state.engine) as db:
        assert len(list(db.scalars(select(Version).where(Version.document_id == doc["id"])))) == 1
    # No request ID exists for review: a second POST is a new, capped admission.
    assert submit().status_code == 429
    assert len(calls) == 1 and usage() == rows


@pytest.mark.parametrize("reason,expected", [("acl", 403), ("capacity", 429), ("login", 503),
                                             ("lease", 409), ("base", 409)])
def test_denied_review_never_consumes_codex_daily_quota(review_api, monkeypatch, reason, expected):
    _, app, doc, _, body, submit, usage, calls = review_api
    if reason == "acl":
        monkeypatch.setenv("HWP_CODEX_USER", "someone-else")
    elif reason == "capacity":
        assert app.state.rewrite_capacity.acquire(blocking=False)
    elif reason == "login":
        def denied(*args):
            raise HTTPException(503, "합성 로그인 필요")
        monkeypatch.setattr(codex_chat_review, "capture_provider", denied)
    elif reason == "lease":
        with transaction(app.state.engine) as db:
            db.get(Document, doc["id"]).lease_until = 0
    else:
        body["base_version"] = "stale-base"
    try:
        assert submit().status_code == expected
        assert usage() == [] and calls == []
    finally:
        if reason == "capacity":
            app.state.rewrite_capacity.release()


@pytest.mark.parametrize("change,expected", [("base", 409), ("lease", 409), ("owner", 409), ("access", 409)])
def test_codex_review_rejects_late_response_after_document_or_permission_change(review_api, monkeypatch, change, expected):
    client, app, doc, lease, _, submit, usage, _ = review_api
    started, released = Event(), Event()

    class SlowProvider:
        async def review(self, original, *, active):
            started.set()
            while not released.is_set():
                await asyncio.sleep(.01)
            return Suggestion(replacement="늦은 합성 제안", reason="합성")

    monkeypatch.setattr(codex_chat_review, "capture_provider", lambda *args: SlowProvider())
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(submit)
        try:
            assert started.wait(3)
            # The async endpoint remains responsive while inference is waiting.
            assert client.get("/api/health").status_code == 200
            if change == "base":
                assert save(client, doc, lease).status_code == 200
            else:
                with transaction(app.state.engine) as db:
                    row = db.get(Document, doc["id"])
                    if change == "lease":
                        row.lease_until = 0
                    elif change == "owner":
                        db.get(LeaseOwner, doc["id"]).user_id = "bob"
                    else:
                        db.delete(db.get(Access, (doc["id"], "alice")))
        finally:
            released.set()
        response = pending.result(timeout=4)
    assert response.status_code == expected, response.text
    assert len(usage()) == 1
    with transaction(app.state.engine) as db:
        versions = list(db.scalars(select(Version).where(Version.document_id == doc["id"])))
        assert len(versions) == (2 if change == "base" else 1)


def test_each_accepted_codex_review_gets_unique_namespace_key(review_api, monkeypatch):
    _, _, _, _, _, submit, usage, _ = review_api
    monkeypatch.setenv("HWP_CODEX_DAILY_REQUEST_LIMIT", "3")
    assert submit().status_code == 200
    assert submit().status_code == 200
    keys = [key for key, _ in usage()]
    assert len(keys) == len(set(keys)) == 2 and all(key.startswith("review:") for key in keys)
