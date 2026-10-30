"""Explicit cross-workspace copies preserve source and access boundaries."""
import json
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import Version
from app.hwpx import digest
from test_accounts import accounts, login
from test_document_copy import payload, copy
from test_document_library import fresh
from test_workspaces import spaces, new_space, add, scoped_doc, affiliate


def test_send_copy_is_atomic_retry_safe_and_preserves_source(spaces):
    c, app, _, _ = spaces
    source_space, _ = new_space(c, '원본 부서')
    destination, _ = new_space(c, '받는 팀', 'team', source_space['id'])
    add(c, destination, 'writer', 'reader')
    source = scoped_doc(c, source_space)
    affiliate(c,destination)
    before = c.get(f'/api/documents/{source["id"]}/versions').json()
    body = payload(source, workspace_id=destination['id'])
    response = copy(c, source, body)
    assert response.status_code == 200, response.text
    target = response.json()
    assert target['workspace_id'] == destination['id']
    assert copy(c, source, body).json() == target
    assert copy(c, source, {**body, 'workspace_id':source_space['id']}).status_code == 409
    assert c.get(f'/api/documents/{source["id"]}/versions').json() == before
    assert next(d for d in c.get('/api/documents').json() if d['id']==source['id'])['workspace_id'] == source_space['id']
    assert len(c.get(f'/api/documents/{target["id"]}/versions').json()) == 1
    original = c.get(f'/api/documents/{source["id"]}/versions/{source["current_version"]}/file').content
    assert c.get(f'/api/documents/{target["id"]}/versions/{target["current_version"]}/file').content == original
    assert login(c, 'writer').status_code == 200
    assert c.get(f'/api/documents/{source["id"]}').status_code == 404
    assert c.get(f'/api/documents/{target["id"]}').status_code == 200


def test_send_copy_rejects_nonowner_destination_reader_and_confidential(spaces):
    c, app, _, _ = spaces
    target, _ = new_space(c)
    source = fresh(c)
    affiliate(c,target)
    affiliate(c,target,'writer')
    add(c, target, 'writer', 'manager')
    assert c.put(f'/api/documents/{source["id"]}/access', json={'user_id':'writer','role':'editor'}).status_code == 200
    body = payload(source, workspace_id=target['id'])
    assert login(c, 'writer').status_code == 200
    assert copy(c, source, body).status_code == 403
    own = fresh(c)
    assert login(c, 'manager').status_code == 200
    add(c, target, 'writer', 'reader')
    assert login(c, 'writer').status_code == 200
    denied = payload(own, workspace_id=target['id'])
    assert copy(c, own, denied).status_code == 403
    assert c.get(f'/api/documents/{denied["document_id"]}').status_code == 404
    assert login(c, 'manager').status_code == 200
    assert c.put(f'/api/documents/{source["id"]}/confidential',json={'enabled':True,'expected_revision':None,'expected_version':source['current_version']}).status_code == 200
    assert copy(c, source, body).status_code == 409


def test_legacy_copy_fingerprint_unchanged_when_destination_omitted(spaces):
    c, app, _, _ = spaces
    source = fresh(c)
    body = payload(source, name='원래 계약.hwpx')
    expected = digest(json.dumps(['document-copy-v1',source['id'],body],sort_keys=True).encode())
    response = copy(c, source, body)
    assert response.status_code == 200
    with Session(app.state.engine) as db:
        assert db.scalar(select(Version).where(Version.document_id==body['document_id'])).request_id == expected
    assert copy(c, source, body).json() == response.json()


def test_affiliated_editor_can_send_but_foreign_manager_and_reassigned_retry_cannot(spaces):
    c, app, _, _ = spaces
    dep,_=new_space(c,'소속 부서');team,_=new_space(c,'소속 팀','team',dep['id']);other,_=new_space(c,'다른 부서')
    affiliate(c,team,'writer')
    add(c,dep,'writer','editor');add(c,team,'writer','editor');add(c,other,'writer','manager')
    login(c,'writer');source=fresh(c)
    assert {s['id'] for s in c.get('/api/workspaces/send-targets').json()}=={dep['id'],team['id']}
    foreign=payload(source,workspace_id=other['id'])
    assert copy(c,source,foreign).status_code==403
    assert c.put(f"/api/documents/{source['id']}/workspace",json={'workspace_id':other['id'],'expected_version':source['current_version']}).status_code==403
    body=payload(source,workspace_id=team['id']);sent=copy(c,source,body)
    assert sent.status_code==200,sent.text
    assert copy(c,source,body).json()==sent.json()
    assert c.put(f"/api/documents/{source['id']}/workspace",json={'workspace_id':dep['id'],'expected_version':source['current_version']}).status_code==200
    login(c,'manager');affiliate(c,other,'writer');login(c,'writer')
    assert copy(c,source,body).status_code==403
    assert [s['id'] for s in c.get('/api/workspaces/send-targets').json()]==[other['id']]
    login(c,'reader');assert c.get('/api/workspaces/send-targets').json()==[]
