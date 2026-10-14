"""Saved-document copies use immutable bytes and current access, without an editor/model."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy.orm import Session
from app.db import Version
from test_collaboration import secured, document, thread, share, as_user
from test_storage import package, save


def payload(doc, **changes):
    return {"document_id": str(uuid.uuid4()), "source_version": doc["current_version"],
            "request_id": str(uuid.uuid4()), "name": "테스트 사본", **changes}


def copy(c, doc, body):
    return c.post(f"/api/documents/{doc['id']}/copies", json=body)


def test_copy_saved_bytes_and_retry_does_not_copy_history_access_or_threads(secured):
    c, _, _, _ = secured; source = document(c); thread(c, source); share(c, source, "reader")
    lease = c.post(f"/api/documents/{source['id']}/session").json()["token"]
    saved = save(c, source, lease, package(extra={"copy-proof.txt": "later version"})).json()
    body = payload(source)  # Explicit older version, not the later current version.
    response = copy(c, source, body)
    assert response.status_code == 200, response.text
    target = response.json()
    assert target["name"] == "테스트 사본.hwpx" and target["id"] != source["id"]
    assert target["source_version"] == source["current_version"]
    assert copy(c, source, body).json() == target
    assert c.get(f"/api/documents/{target['id']}/versions/{target['current_version']}/file").content == package()
    assert len(c.get(f"/api/documents/{target['id']}/versions").json()) == 1
    assert len(c.get(f"/api/documents/{source['id']}/versions").json()) == 2
    assert c.get(f"/api/documents/{source['id']}").json()["current_version"] == saved["id"]
    assert c.get(f"/api/documents/{target['id']}/threads").json() == []
    assert c.get(f"/api/documents/{target['id']}/access").json() == [{"user_id": "alice", "role": "owner"}]
    as_user(c, "bob")
    assert c.get(f"/api/documents/{target['id']}").status_code == 404


def test_reader_can_copy_downloadable_version_but_other_users_cannot(secured):
    c, _, _, _ = secured; source = document(c); share(c, source, "reader")
    as_user(c, "bob"); body = payload(source)
    response = copy(c, source, body)
    assert response.status_code == 200 and response.json()["role"] == "owner"
    as_user(c, "eve")
    assert copy(c, source, payload(source)).status_code == 404
    as_user(c, "alice"); c.delete(f"/api/documents/{source['id']}/access/bob")
    as_user(c, "bob")
    assert copy(c, source, body).status_code == 404  # Reconfirming also checks source access.
    assert c.get(f"/api/documents/{body['document_id']}").status_code == 200


@pytest.mark.parametrize("field", ["name", "request_id", "source_version"])
def test_different_copy_request_cannot_reuse_destination(secured, field):
    c, _, _, _ = secured; source = document(c); body = payload(source)
    assert copy(c, source, body).status_code == 200
    changed = {**body, field: "다른 이름" if field == "name" else str(uuid.uuid4())}
    assert copy(c, source, changed).status_code == (404 if field == "source_version" else 409)
    assert len(c.get("/api/documents").json()) == 2


def test_copy_cannot_replace_source_or_another_document(secured):
    c, _, _, _ = secured; source = document(c); other = document(c)
    assert copy(c, source, payload(source, document_id=source["id"])).status_code == 409
    assert copy(c, source, payload(source, document_id=other["id"])).status_code == 409
    assert copy(c, source, payload(source, source_version=other["current_version"])).status_code == 404


def test_concurrent_copy_retry_publishes_one_blob_and_version(secured):
    c, _, _, folder = secured; source = document(c); body = payload(source)
    before = set(folder.iterdir())
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: copy(c, source, body), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert len(set(folder.iterdir()) - before) == 1


def test_copy_retry_after_target_edit_never_rolls_back_version(secured):
    c, _, _, _ = secured; source = document(c); body = payload(source)
    target = copy(c, source, body).json()
    lease = c.post(f"/api/documents/{target['id']}/session").json()["token"]
    saved = save(c, target, lease).json()
    assert copy(c, source, body).status_code == 409
    assert c.get(f"/api/documents/{target['id']}").json()["current_version"] == saved["id"]


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_source_does_not_create_copy(secured, damage):
    c, app, _, folder = secured; source = document(c)
    with Session(app.state.engine) as db: key = db.get(Version, source["current_version"]).storage_key
    path = folder / key; before = path.read_bytes()
    # Damage only this test's temporary synthetic store, restored in finally.
    try:
        if damage == "missing": path.unlink()
        else: path.write_bytes(b"corrupt synthetic fixture")
        assert copy(c, source, payload(source)).status_code == (503 if damage == "missing" else 422)
        assert len(c.get("/api/documents").json()) == 1
    finally: path.write_bytes(before)


@pytest.mark.parametrize("name", [" ", "../x", "x\\y", "a\x00b", "a" * 236, "\ud800"])
def test_copy_name_validation(secured, name):
    c, _, _, _ = secured; source = document(c)
    response = c.post(f"/api/documents/{source['id']}/copies", content=json.dumps(payload(source, name=name)), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert len(c.get("/api/documents").json()) == 1
