"""Merged Mattermost search uses the same live scopes as the document library."""
import uuid

from app import document_search as search
from app.db import Access, transaction
from app.document_library import PersonalDocument
from test_accounts import accounts, login
from test_document_library import fresh
from test_workspaces import spaces, new_space, add, scoped_doc


def hits(app, user, query):
    result = search.search_documents(app.state.engine, app.state.folder, user, query)
    assert result["total"] == len(result["results"])
    return {item["document_id"] for item in result["results"]}


def index(app):
    search.refresh_search_index(app.state.engine, app.state.folder, max_documents=10)


def test_search_hides_private_documents_but_preserves_legacy_and_explicit_shares(spaces):
    c, app, _, _ = spaces
    legacy, private = fresh(c), fresh(c)
    with transaction(app.state.engine) as db:
        db.delete(db.get(PersonalDocument, legacy["id"]))
    # Titles must be scoped even before the derived body index is ready.
    assert private["id"] not in hits(app, "outsider", private["name"])
    index(app)
    assert hits(app, "outsider", "개인 계획") == {legacy["id"]}
    assert hits(app, "manager", "개인 계획") == {legacy["id"], private["id"]}
    path = f"/api/documents/{private['id']}/access"
    assert c.put(path, json={"user_id": "writer", "role": "reader"}).status_code == 200
    assert hits(app, "writer", "개인 계획") == {legacy["id"], private["id"]}
    assert c.delete(path + "/writer").status_code == 200
    assert hits(app, "writer", "개인 계획") == {legacy["id"]}


def test_search_team_membership_and_document_invitation_override_legacy_grants(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c)
    add(c, space, "writer", "editor")
    doc = scoped_doc(c, space)
    with transaction(app.state.engine) as db:
        db.add(Access(document_id=doc["id"], user_id="outsider", role="owner"))
    index(app)
    assert hits(app, "outsider", "비공개 공간") == set()
    assert hits(app, "writer", "비공개 공간") == {doc["id"]}
    path = f"/api/documents/{doc['id']}/access"
    assert c.put(path, json={"user_id": "reader", "role": "reader"}).status_code == 200
    assert hits(app, "reader", "비공개 공간") == {doc["id"]}
    assert c.delete(path + "/reader").status_code == 200
    assert c.delete(f"/api/workspaces/{space['id']}/members/writer").status_code == 200
    assert hits(app, "reader", "비공개 공간") == set()
    assert hits(app, "writer", "비공개 공간") == set()


def test_search_keeps_work_copies_private_and_rechecks_source_access(spaces):
    c, app, _, _ = spaces
    space, _ = new_space(c)
    add(c, space, "writer", "editor")
    source = scoped_doc(c, space)
    assert login(c, "writer").status_code == 200
    response = c.post(f"/api/documents/{source['id']}/edit-target", json={"request_id": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    branch = response.json()["document"]
    assert response.json()["mode"] == "workcopy"
    index(app)
    assert hits(app, "writer", "비공개 공간") == {source["id"], branch["id"]}
    assert hits(app, "manager", "비공개 공간") == {source["id"]}
    assert hits(app, "outsider", "비공개 공간") == set()
    assert login(c, "manager").status_code == 200
    assert c.delete(f"/api/workspaces/{space['id']}/members/writer").status_code == 200
    assert hits(app, "writer", "비공개 공간") == set()
