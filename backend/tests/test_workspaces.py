"""Synthetic account boundaries, real API/storage; no models or production data."""
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.account_admin import maintain
from app.db import Access, Version, transaction
from app.main import create_app
from app.workspaces import WorkspaceDocument
from test_accounts import PASSWORD, accounts, login
from test_storage import create, package, save


@pytest.fixture
def spaces(accounts, monkeypatch):
    _, old, url, folder = accounts
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "shared")
    for user, role in [("manager", "admin"), ("writer", "member"), ("reader", "member"), ("outsider", "admin")]:
        maintain(old.state.engine, "create", user, password=PASSWORD, name="합성 " + user,
                 employee="SPACE-" + user, role=role)
    app = create_app(url, folder)
    with TestClient(app, base_url="https://testserver") as c:
        assert login(c, "manager").status_code == 200
        yield c, app, url, folder


def new_space(c, name="합성 부서", kind="department", parent=None):
    value = {"id": str(uuid.uuid4()), "name": name, "kind": kind, "parent_id": parent}
    r = c.post("/api/workspaces", json=value)
    assert r.status_code == 200, r.text
    return r.json(), value


def add(c, space, user, role):
    r = c.put(f"/api/workspaces/{space['id']}/members/{user}", json={"role": role})
    assert r.status_code == 200, r.text


def affiliate(c, space, user="manager"):
    current = c.get(f"/api/organization/accounts/{user}").json()
    value = {"department_id":space["parent_id"] if space["kind"]=="team" else space["id"],
             "team_id":space["id"] if space["kind"]=="team" else None,
             "expected_revision":current.get("revision")}
    response=c.put(f"/api/organization/accounts/{user}",json=value)
    assert response.status_code==200,response.text


def scoped_doc(c, space):
    affiliate(c, space)
    doc, token = create(c, package("비공개 공간 문서"))
    assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": token}).status_code == 200
    response = c.put(f"/api/documents/{doc['id']}/workspace", json={"workspace_id": space["id"], "expected_version": doc["current_version"]})
    assert response.status_code == 200, response.text
    return doc


def test_create_and_last_manager_rules(spaces):
    c, _, _, _ = spaces
    space, value = new_space(c)
    assert c.post("/api/workspaces", json=value).json() == space
    assert c.post("/api/workspaces", json={**value, "name": "다른 부서"}).status_code == 409
    add(c, space, "writer", "editor")
    assert c.delete(f"/api/workspaces/{space['id']}/members/manager").status_code == 409
    assert c.put(f"/api/workspaces/{space['id']}/members/manager", json={"role": "reader"}).status_code == 409
    assert c.put(f"/api/workspaces/{space['id']}/members/missing", json={"role": "reader"}).status_code == 422
    assert login(c, "writer").status_code == 200
    assert c.post("/api/workspaces", json={**value, "id": str(uuid.uuid4())}).status_code == 403
    assert c.delete(f"/api/workspaces/{space['id']}/members/manager").status_code == 403


def test_membership_not_inherited_and_nonmember_admin_cannot_read(spaces):
    c, _, _, _ = spaces
    dep, _ = new_space(c)
    team, _ = new_space(c, "합성 팀", "team", dep["id"])
    add(c, dep, "writer", "editor")
    doc = scoped_doc(c, team)
    assert login(c, "writer").status_code == 200
    assert [s["id"] for s in c.get("/api/workspaces").json()] == [dep["id"]]
    assert c.get(f"/api/workspaces/{team['id']}/members").status_code == 404
    assert c.get(f"/api/documents/{doc['id']}").status_code == 404
    assert login(c, "outsider").status_code == 200
    assert c.get("/api/workspaces").json() == []
    assert c.get(f"/api/documents/{doc['id']}").status_code == 404
    assert c.get("/api/documents").json() == []


def test_reader_editor_and_existing_grants_cannot_bypass_scope(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c)
    add(c, space, "reader", "reader"); add(c, space, "writer", "editor")
    doc = scoped_doc(c, space)
    with transaction(app.state.engine) as db:
        db.add(Access(document_id=doc["id"], user_id="outsider", role="owner"))
    assert login(c, "reader").status_code == 200
    assert c.get("/api/documents").json()[0]["role"] == "reader"
    assert c.get(f"/api/documents/{doc['id']}/versions").status_code == 200
    assert c.get(f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file").content == package("비공개 공간 문서")
    assert c.post(f"/api/documents/{doc['id']}/session").status_code == 403
    assert c.post(f"/api/documents/{doc['id']}/coedit", json={"request_id": str(uuid.uuid4())}).status_code == 403
    assert login(c, "writer").status_code == 200
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    assert save(c, doc, token).status_code == 200
    assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": token}).status_code == 200
    assert c.get("/api/documents").json()[0]["can_trash"] is False
    assert login(c, "outsider").status_code == 200
    assert c.get("/api/documents").json() == []
    assert c.get(f"/api/documents/{doc['id']}/versions").status_code == 404


def test_membership_revocation_blocks_held_session_and_all_read_paths(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c); add(c, space, "writer", "editor")
    doc = scoped_doc(c, space)
    thread = str(uuid.uuid4())
    assert c.post(f"/api/documents/{doc['id']}/threads", json={"id": thread, "title": "공간 검토"}).status_code == 200
    with TestClient(app, base_url="https://testserver") as writer:
        assert login(writer, "writer").status_code == 200
        token = writer.post(f"/api/documents/{doc['id']}/session").json()["token"]
        assert c.delete(f"/api/workspaces/{space['id']}/members/writer").status_code == 200
        assert save(writer, doc, token).status_code == 404
        for path in [f"/api/documents/{doc['id']}", f"/api/documents/{doc['id']}/versions",
                     f"/api/documents/{doc['id']}/versions/{doc['current_version']}/file",
                     f"/api/documents/{doc['id']}/threads", f"/api/threads/{thread}"]:
            assert writer.get(path).status_code == 404, path
        assert writer.get("/api/documents?q=비공개").json() == []


def test_assignment_requires_original_owner_affiliated_editor_and_no_lease(spaces):
    c, _, _, _ = spaces
    space, _ = new_space(c); add(c, space, "writer", "manager")
    affiliate(c, space)
    doc, token = create(c)
    payload = {"workspace_id": space["id"], "expected_version": doc["current_version"]}
    assert c.put(f"/api/documents/{doc['id']}/workspace", json=payload).status_code == 409
    assert c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": token}).status_code == 200
    assert login(c, "writer").status_code == 200
    assert c.put(f"/api/documents/{doc['id']}/workspace", json=payload).status_code == 404
    assert login(c, "manager").status_code == 200
    assert c.put(f"/api/documents/{doc['id']}/workspace", json={**payload, "expected_version": str(uuid.uuid4())}).status_code == 409
    assert c.put(f"/api/documents/{doc['id']}/workspace", json=payload).status_code == 200
    assert c.put(f"/api/documents/{doc['id']}/workspace", json=payload).status_code == 200
    assert c.put(f"/api/documents/{doc['id']}/access", json={"user_id": "outsider", "role": "editor"}).status_code == 200


def test_template_catalogs_hide_scoped_sources(spaces):
    c, _, _, _ = spaces
    space, _ = new_space(c)
    doc = scoped_doc(c, space)
    template = c.post("/api/templates", json={"document_id": doc["id"], "version_id": doc["current_version"], "name": "비공개 양식"})
    assert template.status_code == 200, template.text
    assert len(c.get("/api/templates").json()) == 1
    assert login(c, "outsider").status_code == 200
    assert c.get("/api/templates").json() == []
    assert c.get("/api/template-market").json()["items"] == []
    assert c.get(f"/api/template-market/{template.json()['id']}/file").status_code == 404


def test_restart_keeps_scope_history_and_original_bytes(spaces):
    c, app, url, folder = spaces
    space, _ = new_space(c); add(c, space, "reader", "reader")
    doc = scoped_doc(c, space)
    with Session(app.state.engine) as db:
        versions = [(v.id, v.sha256, v.storage_key) for v in db.scalars(select(Version).where(Version.document_id == doc["id"]))]
    hashes = {path.name: path.read_bytes() for path in folder.glob("*.hwpx")}
    with TestClient(create_app(url, folder), base_url="https://testserver") as restarted:
        assert login(restarted, "reader").status_code == 200
        assert restarted.get("/api/documents").json()[0]["role"] == "reader"
    with Session(app.state.engine) as db:
        assert [(v.id, v.sha256, v.storage_key) for v in db.scalars(select(Version).where(Version.document_id == doc["id"]))] == versions
    assert {path.name: path.read_bytes() for path in folder.glob("*.hwpx")} == hashes


def test_builtin_copy_and_template_apply_preserve_space_and_retry(spaces):
    c, _, _, _ = spaces
    space, _ = new_space(c); add(c, space, "reader", "reader")
    doc = scoped_doc(c, space)
    template = c.post("/api/templates", json={"document_id": doc["id"], "version_id": doc["current_version"], "name": "공간 양식"}).json()
    assert login(c, "reader").status_code == 200
    payload = {"document_id": str(uuid.uuid4()), "source_version": doc["current_version"], "request_id": str(uuid.uuid4()), "name": "공간 사본"}
    copied = c.post(f"/api/documents/{doc['id']}/copies", json=payload)
    assert copied.status_code == 200, copied.text
    assert copied.json()["role"] == "reader" and copied.json()["workspace_id"] == space["id"]
    assert c.post(f"/api/documents/{doc['id']}/copies", json=payload).json()["current_version"] == copied.json()["current_version"]
    apply_payload = {"document_id": str(uuid.uuid4()), "name": "공간 양식 사본", "request_id": str(uuid.uuid4())}
    applied = c.post(f"/api/templates/{template['id']}/apply", json=apply_payload)
    assert applied.status_code == 200, applied.text
    assert applied.json()["role"] == "reader" and applied.json()["workspace_id"] == space["id"]
    assert c.post(f"/api/templates/{template['id']}/apply", json=apply_payload).json()["current_version"] == applied.json()["current_version"]
    assert login(c, "outsider").status_code == 200
    assert c.get("/api/documents").json() == []
    for result in [copied, applied]:
        assert c.get(f"/api/documents/{result.json()['id']}").status_code == 404


def test_personal_branch_cannot_escape_through_copy_or_template(spaces):
    c, _, _, _ = spaces
    space, _ = new_space(c)
    doc = scoped_doc(c, space)
    payload = {"document_id": str(uuid.uuid4()), "source_version": doc["current_version"], "request_id": str(uuid.uuid4()), "name": "개인 작업본"}
    result = c.post(f"/api/documents/{doc['id']}/branches", json=payload)
    assert result.status_code == 200, result.text
    branch = result.json()["document"]
    copied = c.post(f"/api/documents/{branch['id']}/copies", json={**payload, "document_id": str(uuid.uuid4()), "source_version": branch["current_version"]})
    assert copied.status_code == 409
    registered = c.post("/api/templates", json={"document_id": branch["id"], "version_id": branch["current_version"], "name": "내 비공개 초안"})
    assert registered.status_code == 409


def create_work_copy(c, source, name="초대된 작업본.hwpx"):
    response = c.post(f"/api/documents/{source['id']}/branches", json={
        "document_id": str(uuid.uuid4()), "source_version": source["current_version"],
        "request_id": str(uuid.uuid4()), "name": name})
    assert response.status_code == 200, response.text
    return response.json()["document"]


def test_invited_branch_scope_matches_listing_and_rechecks_source_editor(spaces):
    c, app, _, folder = spaces
    space, _ = new_space(c); add(c, space, "writer", "editor"); add(c, space, "reader", "reader")
    source = scoped_doc(c, space)
    branch = create_work_copy(c, source)
    with TestClient(app, base_url="https://testserver") as participant:
        assert login(participant, "writer").status_code == 200
        assert participant.get(f"/api/documents/{branch['id']}").status_code == 404
        assert branch["id"] not in [row["id"] for row in participant.get("/api/documents").json()]
        invited = c.put(f"/api/documents/{branch['id']}/branch-participants/writer")
        assert invited.status_code == 200, invited.text
        listed = next(row for row in participant.get("/api/documents").json() if row["id"] == branch["id"])
        assert listed["role"] == "editor" and listed["can_trash"] is False
        assert participant.get(f"/api/documents/{branch['id']}").json()["role"] == "editor"
        assert participant.put(f"/api/documents/{branch['id']}/branch-participants/reader").status_code == 403
        token = participant.post(f"/api/documents/{branch['id']}/session").json()["token"]
        saved = save(participant, branch, token, package("참여자 수정"))
        assert saved.status_code == 200, saved.text
        with Session(app.state.engine) as db:
            before = [(v.id, v.sha256) for v in db.scalars(select(Version).where(Version.document_id == branch["id"]))]
        hashes = {p.name: p.read_bytes() for p in folder.glob("*.hwpx")}
        add(c, space, "writer", "reader")
        assert branch["id"] not in [row["id"] for row in participant.get("/api/documents").json()]
        for path in [f"/api/documents/{branch['id']}", f"/api/documents/{branch['id']}/versions",
                     f"/api/documents/{branch['id']}/versions/{branch['current_version']}/file",
                     f"/api/documents/{branch['id']}/branches"]:
            assert participant.get(path).status_code == 404, path
        assert save(participant, branch, token, package("회수 후 수정"), base=saved.json()["id"]).status_code == 404
        assert branch["id"] not in participant.get(f"/api/documents/{source['id']}/branches").text
        assert {p.name: p.read_bytes() for p in folder.glob("*.hwpx")} == hashes
        with Session(app.state.engine) as db:
            assert [(v.id, v.sha256) for v in db.scalars(select(Version).where(Version.document_id == branch["id"]))] == before
        add(c, space, "writer", "editor")
        assert participant.get(f"/api/documents/{branch['id']}").status_code == 200
        assert c.delete(f"/api/documents/{branch['id']}/branch-participants/writer").status_code == 200
        assert participant.get(f"/api/documents/{branch['id']}").status_code == 404
        assert branch["id"] not in [row["id"] for row in participant.get("/api/documents").json()]


def test_shared_source_never_implicitly_grants_private_branch_participation(spaces):
    c, app, _, _ = spaces
    source, token = create(c)
    # Model an existing legacy shared document; new uploads are now private.
    from app.document_library import PersonalDocument
    with transaction(app.state.engine) as db:
        db.delete(db.get(PersonalDocument, source['id']))
    assert c.delete(f"/api/documents/{source['id']}/session", headers={"X-Edit-Session": token}).status_code == 200
    branch = create_work_copy(c, source, "비공개 분기 이름.hwpx")
    assert c.put(f"/api/documents/{branch['id']}/branch-participants/writer").status_code == 200
    with TestClient(app, base_url="https://testserver") as participant:
        assert login(participant, "writer").status_code == 200
        joined = participant.post(f"/api/documents/{branch['id']}/coedit", json={"request_id": str(uuid.uuid4())})
        assert joined.status_code == 200, joined.text
        assert c.delete(f"/api/documents/{branch['id']}/branch-participants/writer").status_code == 200
        assert participant.put(f"/api/documents/{branch['id']}/coedit", headers={"X-Coedit-Session": joined.json()["token"]}).status_code == 404
        flow = participant.get(f"/api/documents/{source['id']}/branches")
        assert flow.status_code == 200
        assert branch["id"] not in flow.text and "비공개 분기 이름" not in flow.text
        assert branch["id"] not in [row["id"] for row in participant.get("/api/documents").json()]
    assert login(c, "outsider").status_code == 200
    assert c.get(f"/api/documents/{source['id']}").status_code == 200
    assert c.get(f"/api/documents/{branch['id']}").status_code == 404


def test_cross_team_share_exposes_only_invited_document_not_space_or_branch(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c)
    doc, other = scoped_doc(c, space), scoped_doc(c, space)
    branch = create_work_copy(c, doc)
    assert c.put(f"/api/documents/{doc['id']}/access", json={"user_id": "outsider", "role": "reader"}).status_code == 200
    with TestClient(app, base_url="https://testserver") as invited:
        assert login(invited, "outsider").status_code == 200
        rows = invited.get("/api/documents").json()
        assert [r['id'] for r in rows] == [doc['id']]
        assert rows[0]['shared_with_me'] and rows[0]['role'] == 'reader' and not rows[0]['can_trash']
        assert invited.get('/api/workspaces').json() == []
        for identity in [other['id'], branch['id']]:
            assert invited.get(f'/api/documents/{identity}/versions').status_code == 404
        assert invited.get(f'/api/workspaces/{space["id"]}/members').status_code == 404
        assert invited.get(f'/api/documents/{doc["id"]}/versions').status_code == 200
        assert invited.get(f'/api/documents/{doc["id"]}/access').status_code == 403
        assert invited.post(f'/api/documents/{doc["id"]}/session').status_code == 403
        assert c.delete(f'/api/documents/{doc["id"]}/access/outsider').status_code == 200
        assert invited.get('/api/documents').json() == []
        assert invited.get(f'/api/documents/{doc["id"]}/versions/{doc["current_version"]}/file').status_code == 404


def test_cross_team_editor_revoke_fences_held_save_and_regrant_starts_new_session(spaces):
    c, app, _, folder = spaces
    space, _ = new_space(c); doc = scoped_doc(c, space)
    path = f'/api/documents/{doc["id"]}'
    assert c.put(path+'/access', json={'user_id':'outsider','role':'editor'}).status_code == 200
    with TestClient(app, base_url='https://testserver') as invited:
        assert login(invited,'outsider').status_code == 200
        token = invited.post(path+'/session').json()['token']
        assert save(invited,doc,token,package('초대받은 팀원의 수정')).status_code == 200
        latest = invited.get(path).json()
        hashes = {p.name:p.read_bytes() for p in folder.glob('*.hwpx')}
        assert c.put(path+'/access',json={'user_id':'outsider','role':'reader'}).status_code == 200
        assert save(invited,latest,token,package('회수 후 수정')).status_code == 403
        assert {p.name:p.read_bytes() for p in folder.glob('*.hwpx')} == hashes
        assert c.put(path+'/access',json={'user_id':'outsider','role':'editor'}).status_code == 200
        assert save(invited,latest,token).status_code == 409
        assert invited.post(path+'/session').status_code == 200


def test_cross_team_revoke_keeps_other_coedit_participants(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c); doc = scoped_doc(c, space); path = f'/api/documents/{doc["id"]}'
    assert c.put(path+'/access',json={'user_id':'outsider','role':'editor'}).status_code == 200
    with TestClient(app, base_url='https://testserver') as invited:
        assert login(invited,'outsider').status_code == 200
        token = invited.post(path+'/coedit',json={'request_id':str(uuid.uuid4())}).json()['token']
        manager = c.post(path+'/coedit',json={'request_id':str(uuid.uuid4())}).json()['token']
        assert c.delete(path+'/access/outsider').status_code == 200
        assert invited.put(path+'/coedit',headers={'X-Coedit-Session':token}).status_code == 404
        kept = c.put(path+'/coedit',headers={'X-Coedit-Session':manager})
        assert kept.status_code == 200 and len(kept.json()['participants']) == 1
        assert c.put(path+'/access',json={'user_id':'outsider','role':'editor'}).status_code == 200
        assert invited.put(path+'/coedit',headers={'X-Coedit-Session':token}).status_code == 409


def test_cross_team_invite_never_grants_reshare_copy_template_or_management(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c); add(c,space,'reader','reader'); doc = scoped_doc(c,space)
    path = f'/api/documents/{doc["id"]}'
    for user in ['reader','missing']:
        assert c.put(path+'/access',json={'user_id':user,'role':'editor'}).status_code in (409,422)
    assert c.put(path+'/access',json={'user_id':'outsider','role':'editor'}).status_code == 200
    with TestClient(app, base_url='https://testserver') as invited:
        assert login(invited,'outsider').status_code == 200
        assert invited.put(path+'/access',json={'user_id':'writer','role':'editor'}).status_code == 403
        assert invited.get('/api/documents').json()[0]['can_trash'] is False
        assert invited.post(path+'/copies',json={'document_id':str(uuid.uuid4()),'source_version':doc['current_version'],'request_id':str(uuid.uuid4()),'name':'밖으로 복제'}).status_code == 403
        assert invited.post('/api/templates',json={'document_id':doc['id'],'version_id':doc['current_version'],'name':'공개 우회'}).status_code == 403


def test_document_move_clears_invites_and_never_revives_legacy_acl(spaces):
    c, app, _, _ = spaces
    first, _ = new_space(c,'첫 문서함'); second, _ = new_space(c,'둘째 문서함')
    doc = scoped_doc(c,first); path = f'/api/documents/{doc["id"]}'
    with transaction(app.state.engine) as db:
        db.add(Access(document_id=doc['id'],user_id='outsider',role='owner'))
    assert c.put(path+'/access',json={'user_id':'outsider','role':'reader'}).status_code == 200
    for space in [second,first]:
        affiliate(c,space)
        assert c.put(path+'/workspace',json={'workspace_id':space['id'],'expected_version':doc['current_version']}).status_code == 200
        assert c.get(path+'/access').json() == []
        with TestClient(app,base_url='https://testserver') as invited:
            assert login(invited,'outsider').status_code == 200
            assert invited.get(path).status_code == 404 and invited.get('/api/documents').json() == []
