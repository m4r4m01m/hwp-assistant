import uuid
from sqlalchemy import select
from app.db import Version, transaction
from app.document_branches import WorkCopyEntry, DocumentBranch
from test_accounts import accounts, login
from test_workspaces import spaces, new_space, add, scoped_doc
from test_document_branches import edit, submit
from test_storage import package


def entry(c, doc, key=None):
    return c.post(f"/api/documents/{doc['id']}/edit-target", json={'request_id': key or str(uuid.uuid4())})


def test_user_lanes_reuse_same_workcopy_without_touching_source(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c)
    add(c, space, 'writer', 'editor'); add(c, space, 'reader', 'reader')
    source = scoped_doc(c, space)
    original = c.get(f"/api/documents/{source['id']}/versions/{source['current_version']}/file").content
    key = str(uuid.uuid4())
    mine = entry(c, source, key).json()
    assert mine['mode'] == 'workcopy'
    assert mine['document']['branch_source_id'] == source['id']
    assert entry(c, source, key).json() == mine
    assert entry(c, source).json() == mine
    assert entry(c, mine['document']).json() == mine  # opening invited/own work copy never forks it
    login(c, 'writer')
    theirs = entry(c, source).json()
    assert theirs['document']['id'] != mine['document']['id']
    assert entry(c, source, key).status_code == 409
    assert entry(c, mine['document']).status_code == 404
    login(c, 'reader')
    assert entry(c, source).json()['mode'] == 'view'
    assert len(c.get(f"/api/documents/{source['id']}/versions").json()) == 1
    assert c.get(f"/api/documents/{source['id']}/versions/{source['current_version']}/file").content == original
    with transaction(app.state.engine) as db:
        assert len(list(db.scalars(select(DocumentBranch).where(DocumentBranch.source_document_id == source['id'])))) == 2


def test_finished_workcopy_starts_new_lane_from_merged_head_but_retry_is_frozen(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c); add(c, space, 'writer', 'editor')
    source = scoped_doc(c, space)
    login(c, 'writer')
    key = str(uuid.uuid4()); branch = entry(c, source, key).json()['document']
    branch = edit(c, branch, package('검토할 수정'))
    request = submit(c, source, branch); assert request.status_code == 200, request.text
    login(c, 'manager')
    review = c.get('/api/integration-requests/'+request.json()['id']+'/review').json()
    response = c.post('/api/integration-requests/'+request.json()['id']+'/accept',json={'merged_sha256':review['merged_sha256']})
    assert response.status_code == 200, response.text
    merged = response.json()['merged_version_id']
    login(c, 'writer')
    assert entry(c, source, key).json()['document']['id'] == branch['id']
    next_branch = entry(c, source).json()['document']
    assert next_branch['id'] != branch['id']
    with transaction(app.state.engine) as db:
        assert db.get(DocumentBranch, next_branch['id']).base_version_id == merged
        assert db.get(Version, branch['current_version']) is not None


def test_private_direct_readonly_and_revoked_entry(spaces):
    from test_storage import create
    c, _, _, _ = spaces
    doc, token = create(c, package('개인 문서'))
    c.delete(f"/api/documents/{doc['id']}/session",headers={'X-Edit-Session':token})
    assert entry(c, doc).json()['mode'] == 'direct'
    space, _ = new_space(c); add(c, space, 'writer', 'editor')
    source = scoped_doc(c, space)
    login(c, 'writer'); key=str(uuid.uuid4()); branch=entry(c,source,key).json()['document']
    login(c, 'manager'); c.delete(f"/api/workspaces/{space['id']}/members/writer")
    login(c, 'writer')
    assert entry(c,source,key).status_code == 404
    assert entry(c,branch).status_code == 404
