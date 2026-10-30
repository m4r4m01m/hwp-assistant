"""Real bot pipeline with synthetic workspaces; no Mattermost or model calls.

These tests exercise command admission, the actual document index/search and
the worker's final delivery callback. Organization metadata never grants access.
"""
import uuid

import pytest
from sqlalchemy import select

from app import document_search, mattermost_worker as worker
from app.account_admin import maintain
from app.db import transaction
from app.document_library import PersonalDocument
from app.mattermost_store import MattermostLink, MattermostRequest
from test_document_search import document, new_version
from test_mattermost_bot import (
    FakeBot, MM_OTHER, PASSWORD, command, link, login, process, request_id, setup,
)
from test_workspaces import add, new_space, affiliate
from test_work_copy_entry import separate


QUERY = "문서범위검수"
TEAM_TEXT = QUERY + " 팀 문서의 공개된 검토 내용"
PRIVATE_TEXT = QUERY + " 다른 계정의 비공개 내용"
BRANCH_TEXT = QUERY + " 아직 제출하지 않은 작업본의 비공개 내용"


@pytest.fixture
def scoped_bot(setup, monkeypatch):
    app, client = setup
    # Explicit spaces/private documents must override the legacy shared mode.
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "shared")
    maintain(app.state.engine, "create", "manager", password=PASSWORD,
             name="합성 관리자", employee="SYNTHETIC-MANAGER", role="admin")
    login(client, "manager")
    department, _ = new_space(client, "합성 부서")
    team, _ = new_space(client, "합성 팀", "team", department["id"])
    affiliate(client, team)
    corpus = (app.state.engine, app.state.folder)
    source, version = document(corpus, QUERY + " 팀 문서.hwpx", TEAM_TEXT, "manager")
    private, _ = document(corpus, QUERY + " 개인 문서.hwpx", PRIVATE_TEXT, "manager")
    with transaction(app.state.engine) as db:
        db.add(PersonalDocument(document_id=private, owner_id="manager"))
    response = client.put(f"/api/documents/{source}/workspace",
                          json={"workspace_id": team["id"], "expected_version": version})
    assert response.status_code == 200, response.text
    link(client)
    return app, client, department, team, source, version, private


def indexed(app):
    result = document_search.refresh_search_index(
        app.state.engine, app.state.folder, max_documents=10)
    assert result["failed"] == 0


def search_message(app, client, *, mm_user=None):
    options = {"mm_user": mm_user} if mm_user else {}
    rid = request_id(command(client, "검색 " + QUERY, **options))
    process(app, rid)
    with transaction(app.state.engine) as db:
        receipt = db.get(MattermostRequest, rid)
        assert receipt.status == "completed"
        assert receipt.outcome == "search_complete"
        assert receipt.attempts == 1
    assert FakeBot.messages
    return FakeBot.messages[-1][1]


def observe_search(monkeypatch):
    actual = document_search.search_documents
    captured = []

    def observe(*args, **kwargs):
        result = actual(*args, **kwargs)
        captured.append({hit["document_id"] for hit in result["results"]})
        return result

    monkeypatch.setattr(document_search, "search_documents", observe)
    return captured


def test_bot_affiliation_and_parent_membership_do_not_grant_team_search(scoped_bot):
    app, client, department, team, source, version, private = scoped_bot
    with transaction(app.state.engine) as db:
        generation = db.scalar(select(MattermostLink)).generation
    login(client, "manager")
    response = client.put("/api/organization/accounts/alice", json={
        "department_id": department["id"], "team_id": team["id"],
        "expected_revision": None,
    })
    assert response.status_code == 200, response.text
    assert response.json()["team"]["can_access"] is False
    add(client, department, "alice", "reader")
    indexed(app)

    denied = search_message(app, client)
    assert source not in denied and TEAM_TEXT not in denied
    assert private not in denied and PRIVATE_TEXT not in denied

    add(client, team, "alice", "reader")
    allowed = search_message(app, client)
    assert source in allowed and version in allowed and TEAM_TEXT in allowed
    assert private not in allowed and PRIVATE_TEXT not in allowed

    assert client.delete(f"/api/workspaces/{team['id']}/members/alice").status_code == 200
    response = client.put(f"/api/documents/{source}/access",
                          json={"user_id": "alice", "role": "reader"})
    assert response.status_code == 200, response.text
    invited = search_message(app, client)
    assert source in invited and version in invited and TEAM_TEXT in invited
    assert private not in invited and PRIVATE_TEXT not in invited
    with transaction(app.state.engine) as db:
        assert db.scalar(select(MattermostLink)).generation == generation


@pytest.mark.parametrize("change", ["membership", "document_share", "workspace_move", "make_private"])
def test_bot_rechecks_workspace_access_after_remote_dm_preflight(scoped_bot, monkeypatch, change):
    app, client, department, team, source, version, private = scoped_bot
    login(client, "manager")
    if change == "document_share":
        response = client.put(f"/api/documents/{source}/access",
                              json={"user_id": "alice", "role": "reader"})
        assert response.status_code == 200, response.text
    else:
        add(client, team, "alice", "editor")
    branch = None
    if change == "membership":
        login(client)
        branch = separate(client, {"id": source, "current_version": version})["id"]
        new_version((app.state.engine, app.state.folder), branch, BRANCH_TEXT)
        login(client, "manager")
    destination = new_space(client, "다른 합성 팀", "team", department["id"])[0] if change == "workspace_move" else None
    if destination:
        affiliate(client, destination)
    indexed(app)
    captured = observe_search(monkeypatch)
    original_send = FakeBot.send_dm

    async def delayed_send(self, user, message, *, before_send=None):
        # The real search already completed; a concurrent admin now changes ACLs.
        if change == "membership":
            response = client.delete(f"/api/workspaces/{team['id']}/members/alice")
        elif change == "document_share":
            response = client.delete(f"/api/documents/{source}/access/alice")
        else:
            response = client.put(f"/api/documents/{source}/workspace", json={
                "workspace_id": destination["id"] if destination else None,
                "expected_version": version,
            })
        assert response.status_code == 200, response.text
        return await original_send(self, user, message, before_send=before_send)

    monkeypatch.setattr(FakeBot, "send_dm", delayed_send)
    message = search_message(app, client)
    assert len(captured) == 1 and source in captured[0]
    if branch:
        assert branch in captured[0]
        assert branch not in message and BRANCH_TEXT not in message
    assert source not in message and TEAM_TEXT not in message
    assert private not in message and PRIVATE_TEXT not in message


def test_bot_source_reader_keeps_source_but_loses_invited_private_work_copy(scoped_bot, monkeypatch):
    app, client, _, team, source, version, private = scoped_bot
    login(client, "manager")
    add(client, team, "alice", "editor")
    add(client, team, "bob", "editor")
    login(client)
    branch = separate(client, {"id": source, "current_version": version})["id"]
    response = client.put(f"/api/documents/{branch}/branch-participants/bob")
    assert response.status_code == 200, response.text
    new_version((app.state.engine, app.state.folder), branch, BRANCH_TEXT)
    link(client, "bob", MM_OTHER)
    login(client, "manager")
    indexed(app)
    captured = observe_search(monkeypatch)
    original_send = FakeBot.send_dm

    async def delayed_send(self, user, message, *, before_send=None):
        add(client, team, "bob", "reader")
        return await original_send(self, user, message, before_send=before_send)

    monkeypatch.setattr(FakeBot, "send_dm", delayed_send)
    message = search_message(app, client, mm_user=MM_OTHER)
    assert len(captured) == 1 and {source, branch}.issubset(captured[0])
    assert source in message and version in message and TEAM_TEXT in message
    assert branch not in message and BRANCH_TEXT not in message
    assert private not in message and PRIVATE_TEXT not in message
