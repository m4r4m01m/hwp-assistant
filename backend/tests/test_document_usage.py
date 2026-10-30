import time
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import Document, DocumentTrash, Version, transaction
from app.document_branches import DocumentBranch
from test_accounts import accounts, login
from test_workspaces import spaces, new_space, scoped_doc
from test_storage import create, package, save


def test_usage_counts_versions_trash_branches_without_document_access(spaces):
    c, app, _, _ = spaces
    dep, _ = new_space(c)
    team, _ = new_space(c, "합성 팀", "team", dep["id"])
    empty, _ = new_space(c, "빈 팀", "team", dep["id"])
    root = scoped_doc(c, team)
    dept_doc = scoped_doc(c, dep)
    personal, token = create(c, package("개인 문서"))
    assert save(c, personal, token, package("개인 문서 수정")).status_code == 200
    branch, _ = create(c, package("별도 수정안"))
    with transaction(app.state.engine) as db:
        db.add(DocumentBranch(document_id=branch["id"], source_document_id=root["id"],
               base_version_id=root["current_version"], owner_id="manager", name="private name",
               created_at=time.time(), request_id=str(uuid.uuid4())))
        db.add(DocumentTrash(id=str(uuid.uuid4()), document_id=dept_doc["id"], deleted_by="manager", deleted_at=time.time()))
    login(c, "outsider")
    assert c.get('/api/documents/'+root['id']).status_code == 404
    response = c.get('/api/admin/document-usage')
    assert response.status_code == 200
    result = response.json()
    assert root['id'] not in response.text and 'private name' not in response.text
    rows = {row['id']: row for row in result['rows']}
    assert rows[team['id']]['documents'] == 2
    assert rows[team['id']]['work_copies'] == 1
    assert rows[dep['id']]['documents'] == 0
    assert rows[dep['id']]['trashed_documents'] == 1
    assert rows[dep['id']]['current_bytes'] == 0
    assert rows[empty['id']]['stored_bytes'] == 0
    assert rows['other']['documents'] == 1 and rows['other']['versions'] == 2
    with Session(app.state.engine) as db:
        all_versions = list(db.scalars(select(Version)))
        current_ids = list(db.scalars(select(Document.current_version).where(Document.id != dept_doc['id'])))
        assert result['totals']['stored_bytes'] == sum(v.size for v in all_versions)
        assert result['totals']['current_bytes'] == sum(v.size for v in all_versions if v.id in current_ids)
        assert result['totals']['versions'] == len(all_versions)
    assert result['totals']['documents'] == 3 and result['totals']['trashed_documents'] == 1
    login(c, 'writer')
    assert c.get('/api/admin/document-usage').status_code == 403
    c.post('/api/auth/logout')
    assert c.get('/api/admin/document-usage').status_code == 401
