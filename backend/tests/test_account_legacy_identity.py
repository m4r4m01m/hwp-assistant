"""Retired principals must never be reclaimed through public signup."""
import json
import time
from fastapi.testclient import TestClient
from app.db import transaction
from app.main import create_app
from test_accounts import accounts, register, login, TOKENS
from test_storage import package


def test_retired_legacy_principal_cannot_be_reclaimed_by_public_signup(accounts, monkeypatch):
    import uuid
    c, _, url, folder = accounts
    c.headers["Authorization"] = "Bearer " + TOKENS["owner"]
    result = c.post("/api/documents?name=legacy.hwpx&document_id=" + str(uuid.uuid4()),
                    content=package(), headers={"Idempotency-Key": "legacy-only"})
    assert result.status_code == 200
    document_id = result.json()["id"]
    c.headers.pop("Authorization")
    # Retire the owner token and mapping. Its principal still owns a document.
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps({"guest": TOKENS["guest"]}))
    monkeypatch.setenv("HWP_CODEX_USER", "new-owner")
    with TestClient(create_app(url, folder), base_url="https://testserver") as other:
        assert register(other, "owner", "TEST-RECLAIM").status_code == 409
        assert register(other, "fresh-user", "TEST-FRESH").status_code == 200
        assert other.get(f"/api/documents/{document_id}").status_code == 404


def test_audit_only_legacy_case_variant_is_reserved(accounts):
    from app.accounts import historical_identity_in_use
    from app.db import Message
    c, app, _, _ = accounts
    # A revoked share can leave only an authorship record, not current access.
    assert login(c).status_code == 200
    import uuid
    result = c.post("/api/documents?name=audit.hwpx&document_id=" + str(uuid.uuid4()),
                    content=package(), headers={"Idempotency-Key": "audit"})
    document_id = result.json()["id"]
    thread_id = str(uuid.uuid4())
    assert c.post(f"/api/documents/{document_id}/threads", json={"id": thread_id, "title": "합성 기록"}).status_code == 200
    with transaction(app.state.engine) as db:
        db.add(Message(id=str(uuid.uuid4()), conversation_id=thread_id, author_id="FormerUser",
                       role="user", content="합성 기록", source="user", request_id="old-author", created_at=time.time()))
    with transaction(app.state.engine) as db:
        assert historical_identity_in_use(db, "formeruser")
    assert register(c, "formeruser", "TEST-FORMER").status_code == 409
