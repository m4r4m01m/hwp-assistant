import time
import uuid
from sqlalchemy import select
from app.db import Version, transaction
from app.document_branches import WorkCopyEntry, DocumentBranch, VersionMilestone
from test_accounts import accounts, login
from test_workspaces import spaces, new_space, add, scoped_doc
from test_document_branches import edit, submit
from test_storage import package


def entry(c, doc, key=None):
    return c.post(f"/api/documents/{doc['id']}/edit-target", json={'request_id': key or str(uuid.uuid4())})


def separate(c, source):
    response=c.post(f"/api/documents/{source['id']}/branches",json={
        'document_id':str(uuid.uuid4()),'source_version':source['current_version'],
        'request_id':str(uuid.uuid4()),'name':'별도 검토안'})
    assert response.status_code==200,response.text
    return response.json()['document']


def test_shared_opens_join_same_document_without_creating_versions_or_copies(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c)
    add(c, space, 'writer', 'editor'); add(c, space, 'reader', 'reader')
    source = scoped_doc(c, space)
    original = c.get(f"/api/documents/{source['id']}/versions/{source['current_version']}/file").content
    key = str(uuid.uuid4())
    for account in ['manager','writer','manager']:
        login(c,account)
        for request_id in [key,str(uuid.uuid4())]:
            result=entry(c,source,request_id).json()
            assert result['mode']=='coedit'
            assert result['document']['id']==source['id']
    login(c,'reader')
    assert entry(c,source).json()['mode']=='view'
    assert len(c.get(f"/api/documents/{source['id']}/versions").json())==1
    assert c.get(f"/api/documents/{source['id']}/versions/{source['current_version']}/file").content==original
    with transaction(app.state.engine) as db:
        assert list(db.scalars(select(DocumentBranch)))==[]
        assert list(db.scalars(select(WorkCopyEntry)))==[]


def test_explicit_copy_and_legacy_receipt_preserve_history_but_new_open_joins_source(spaces):
    c, app, _, _ = spaces
    space,_=new_space(c);add(c,space,'writer','editor')
    source=scoped_doc(c,space);login(c,'writer')
    branch=separate(c,source);key=str(uuid.uuid4())
    with transaction(app.state.engine) as db:
        db.add(WorkCopyEntry(request_id=key,source_id=source['id'],user_id='writer',document_id=branch['id'],created_at=time.time()))
    assert entry(c,source,key).json()['document']['id']==branch['id']
    assert entry(c,branch).json()['mode']=='workcopy'
    branch=edit(c,branch,package('검토할 수정'))
    request=submit(c,source,branch);assert request.status_code==200,request.text
    login(c,'manager')
    assert entry(c,source,key).status_code==409
    review=c.get('/api/integration-requests/'+request.json()['id']+'/review').json()
    accepted=c.post('/api/integration-requests/'+request.json()['id']+'/accept',json={'merged_sha256':review['merged_sha256']})
    assert accepted.status_code==200,accepted.text
    login(c,'writer')
    assert entry(c,source,key).json()['document']['id']==branch['id']
    current=entry(c,source).json()
    assert current['mode']=='coedit' and current['document']['id']==source['id']
    assert current['document']['current_version']==accepted.json()['merged_version_id']
    with transaction(app.state.engine) as db:
        assert db.get(Version,branch['current_version']) is not None
        assert len(list(db.scalars(select(DocumentBranch))))==1


def test_private_direct_readonly_and_revoked_entry(spaces):
    from test_storage import create
    c, _, _, _ = spaces
    doc, token = create(c, package('개인 문서'))
    c.delete(f"/api/documents/{doc['id']}/session",headers={'X-Edit-Session':token})
    assert entry(c, doc).json()['mode'] == 'direct'
    space, _ = new_space(c); add(c, space, 'writer', 'editor')
    source = scoped_doc(c, space)
    login(c, 'writer');branch=separate(c,source)
    login(c, 'manager'); c.delete(f"/api/workspaces/{space['id']}/members/writer")
    login(c, 'writer')
    assert entry(c,source).status_code == 404
    assert entry(c,branch).status_code == 404


def test_named_version_is_idempotent_authorized_and_preserves_saved_bytes(spaces):
    c,app,_,_=spaces
    space,_=new_space(c);add(c,space,'writer','editor');add(c,space,'reader','reader')
    source=scoped_doc(c,space);version=source['current_version']
    path=f"/api/documents/{source['id']}/versions/{version}/milestone"
    before=c.get(f"/api/documents/{source['id']}/versions").json()
    login(c,'writer')
    first=c.post(path,json={'name':'  검토 반영본  '});assert first.status_code==200,first.text
    assert c.post(path,json={'name':'검토 반영본'}).json()==first.json()
    assert c.post(path,json={'name':'다른 이름'}).status_code==409
    assert c.post(path,json={'name':' '}).status_code==422
    graph=c.get(f"/api/documents/{source['id']}/branches").json()['graph']
    assert next(n for n in graph['nodes'] if n['id']==version)['milestone']=={'name':'검토 반영본','author_id':'writer'}
    assert c.get(f"/api/documents/{source['id']}/versions").json()==before
    login(c,'reader');assert c.post(path,json={'name':'검토 반영본'}).status_code==403
    login(c,'manager')
    other=scoped_doc(c,space)
    assert c.post(f"/api/documents/{other['id']}/versions/{version}/milestone",json={'name':'다른 문서'}).status_code==404
    with transaction(app.state.engine) as db:
        assert len(list(db.scalars(select(VersionMilestone))))==1
