"""Real account/API revocation; synthetic HWPX/jobs, no model or remote messaging."""
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import Conversation, Document, RewriteTask, transaction
from test_accounts import accounts, login
from test_storage import package, save
from test_workspaces import spaces, new_space, add, scoped_doc, create_work_copy


def queued_rewrite(app, doc, user):
    identity, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    with transaction(app.state.engine) as db:
        db.add(Conversation(id=conversation, document_id=doc["id"], owner_id=user,
            visibility="document", title="합성 권한 회수 시험", created_at=time.time()))
        db.flush()
        db.add(RewriteTask(id=identity, conversation_id=conversation, document_id=doc["id"],
            requested_by=user, base_version=doc["current_version"], snapshot="{}",
            instruction="합성 시험", status="running", token_hash="0" * 64,
            execution="{}", created_at=time.time()))
    return identity


def lease(c, doc):
    response = c.post(f"/api/documents/{doc['id']}/session")
    assert response.status_code == 200, response.text
    return response.json()["token"]


def join(c, doc):
    response = c.post(f"/api/documents/{doc['id']}/coedit", json={"request_id": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def sync(c, doc, token):
    return c.put(f"/api/documents/{doc['id']}/coedit", headers={"X-Coedit-Session": token})


@pytest.mark.parametrize("change", ["remove", "reader"])
def test_membership_regrant_never_revives_old_lease_or_rewrite(spaces, change):
    c, app, _, folder = spaces
    space, _ = new_space(c)
    other, _ = new_space(c, "다른 문서함")
    add(c, space, "writer", "editor")
    add(c, other, "writer", "editor")
    doc, unrelated = scoped_doc(c, space), scoped_doc(c, other)
    with TestClient(app, base_url="https://testserver") as writer:
        assert login(writer, "writer").status_code == 200
        token, other_token = lease(writer, doc), lease(writer, unrelated)
        task, other_task = queued_rewrite(app, doc, "writer"), queued_rewrite(app, unrelated, "writer")
        before = {p.name: p.read_bytes() for p in folder.glob("*.hwpx")}
        if change == "remove":
            assert c.delete(f"/api/workspaces/{space['id']}/members/writer").status_code == 200
        else:
            add(c, space, "writer", "reader")
        with Session(app.state.engine) as db:
            assert db.get(Document, doc["id"]).lease_token is None
            assert db.get(Document, unrelated["id"]).lease_token == other_token
            failed = db.get(RewriteTask, task)
            assert failed.status == "failed" and json.loads(failed.execution)["invalidated"] is True
            assert db.get(RewriteTask, other_task).status == "running"
        assert {p.name: p.read_bytes() for p in folder.glob("*.hwpx")} == before
        add(c, space, "writer", "editor")
        assert save(writer, doc, token).status_code == 409
        assert writer.get(f"/api/documents/{doc['id']}").json()["current_version"] == doc["current_version"]
        assert lease(writer, doc) != token


def test_membership_downgrade_retires_invited_branch_but_preserves_other_users_and_own_branch(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c)
    add(c, space, "writer", "editor")
    source = scoped_doc(c, space)
    invited = create_work_copy(c, source)
    assert c.put(f"/api/documents/{invited['id']}/branch-participants/writer").status_code == 200
    with TestClient(app, base_url="https://testserver") as writer:
        assert login(writer, "writer").status_code == 200
        own = create_work_copy(writer, source)
        own_token = lease(writer, own)
        writer_source, writer_branch = join(writer, source), join(writer, invited)
        manager_source, manager_branch = join(c, source), join(c, invited)
        task, own_task = queued_rewrite(app, invited, "writer"), queued_rewrite(app, own, "writer")
        add(c, space, "writer", "reader")
        for doc, token in [(source, manager_source), (invited, manager_branch)]:
            kept = sync(c, doc, token)
            assert kept.status_code == 200, kept.text
            assert [row["user_id"] for row in kept.json()["participants"]] == ["manager"]
        with Session(app.state.engine) as db:
            assert db.get(RewriteTask, task).status == "failed"
            assert db.get(RewriteTask, own_task).status == "running"
            assert db.get(Document, own["id"]).lease_token == own_token
        add(c, space, "writer", "editor")
        assert sync(writer, source, writer_source).status_code == 409
        assert sync(writer, invited, writer_branch).status_code == 409
        assert save(writer, own, own_token, package("내 작업본은 유지")).status_code == 200


@pytest.mark.parametrize("change", ["remove", "reader"])
def test_document_invitation_regrant_cannot_revive_derived_branch_edit_session(spaces, change):
    c, app, _, folder = spaces
    space, _ = new_space(c)
    source = scoped_doc(c, space)
    branch = create_work_copy(c, source)
    path = f"/api/documents/{source['id']}/access"
    assert c.put(path, json={"user_id": "outsider", "role": "editor"}).status_code == 200
    assert c.put(f"/api/documents/{branch['id']}/branch-participants/outsider").status_code == 200
    with TestClient(app, base_url="https://testserver") as outsider:
        assert login(outsider, "outsider").status_code == 200
        token = lease(outsider, branch)
        task = queued_rewrite(app, branch, "outsider")
        before = {p.name: p.read_bytes() for p in folder.glob("*.hwpx")}
        if change == "remove":
            assert c.delete(path + "/outsider").status_code == 200
        else:
            assert c.put(path, json={"user_id": "outsider", "role": "reader"}).status_code == 200
        with Session(app.state.engine) as db:
            assert db.get(Document, branch["id"]).lease_token is None
            assert db.get(RewriteTask, task).status == "failed"
        assert c.put(path, json={"user_id": "outsider", "role": "editor"}).status_code == 200
        assert save(outsider, branch, token).status_code == 409
        assert {p.name: p.read_bytes() for p in folder.glob("*.hwpx")} == before
        assert lease(outsider, branch) != token


def test_removing_membership_preserves_independent_editor_invitation(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c)
    doc = scoped_doc(c, space)
    assert c.put(f"/api/documents/{doc['id']}/access",
        json={"user_id": "outsider", "role": "editor"}).status_code == 200
    add(c, space, "outsider", "editor")
    with TestClient(app, base_url="https://testserver") as outsider:
        assert login(outsider, "outsider").status_code == 200
        token = lease(outsider, doc)
        task = queued_rewrite(app, doc, "outsider")
        assert c.delete(f"/api/workspaces/{space['id']}/members/outsider").status_code == 200
        with Session(app.state.engine) as db:
            assert db.get(Document, doc["id"]).lease_token == token
            assert db.get(RewriteTask, task).status == "running"
        assert save(outsider, doc, token).status_code == 200
