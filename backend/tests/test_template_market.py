"""Synthetic HWPX storage/ACL tests; these are not Hancom or AI model tests."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.db import Access, Document, Template, TemplateCatalog, TemplateFavorite, Version, transaction
from app.hwpx import digest
from app.main import create_app
from test_collaboration import TOKENS, as_user, document, secured, share
from test_storage import package, save


def register(c, doc, **values):
    value = {"id": str(uuid.uuid4()), "document_id": doc["id"], "version_id": doc["current_version"],
             "name": "기본 보고서", "description": "실행 계획과 점검", "tags": ["업무"], "category": "보고서", **values}
    response = c.post("/api/templates", json=value)
    assert response.status_code == 200, response.text
    return response.json()["id"], value


def detail(c, template_id):
    response = c.get(f"/api/template-market/{template_id}")
    assert response.status_code == 200, response.text
    return response.json()


def metadata(item, **values):
    return {key: item[key] for key in ("name", "description", "tags", "category")} | {
        "expected_revision": item["revision"], **values}


def counts(app):
    with transaction(app.state.engine) as db:
        return tuple(db.scalar(select(func.count()).select_from(model))
                     for model in (Document, Version, Template, TemplateFavorite))


def test_preview_is_read_only_and_uses_pinned_historical_bytes(secured):
    c, app, _, folder = secured
    doc = document(c)
    tid, _ = register(c, doc)
    initial = counts(app)
    item = detail(c, tid)
    assert item["revision"] == 1 and item["category"] == "보고서"
    assert item["sha256"] == digest(package()) and item["size"] == len(package())
    assert item["can_manage"] and not item["favorite"]
    assert item["source_name"] == doc["name"]
    assert "storage_key" not in json.dumps(item)
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    edited = save(c, doc, token, package("후속 사용자 편집"))
    assert edited.status_code == 200
    before = {file.name: digest(file.read_bytes()) for file in folder.iterdir()}
    for _ in range(2):
        response = c.get(f"/api/template-market/{tid}/file")
        assert response.content == package()
        assert response.headers["etag"] == '"' + digest(package()) + '"'
        assert response.headers["content-disposition"].startswith("inline;")
    assert detail(c, tid)["version_id"] == doc["current_version"]
    assert c.get("/api/template-market").json()["items"][0]["id"] == tid
    assert counts(app) == (initial[0], initial[1] + 1, initial[2], initial[3])
    assert before == {file.name: digest(file.read_bytes()) for file in folder.iterdir()}


def test_registration_response_loss_and_concurrent_identical_retry(secured):
    c, app, _, _ = secured
    doc = document(c)
    tid, value = register(c, doc)
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda _: c.post("/api/templates", json=value), range(2)))
    assert [reply.status_code for reply in replies] == [200, 200]
    assert replies[0].json() == replies[1].json() == {"id": tid, "version_id": doc["current_version"]}
    assert counts(app) == (1, 1, 1, 0)
    for changed in ({"name": "다른 등록"}, {"category": "계획서"}, {"tags": ["다름"]}):
        assert c.post("/api/templates", json=value | changed).status_code == 409
    item = detail(c, tid)
    assert c.put(f"/api/template-market/{tid}", json=metadata(item, name="수정한 메타데이터")).status_code == 200
    assert c.post("/api/templates", json=value).json() == replies[0].json()
    assert detail(c, tid)["name"] == "수정한 메타데이터"


def test_registration_commit_failure_does_not_claim_registered(secured):
    c, app, _, _ = secured
    doc = document(c)
    value = {"id": str(uuid.uuid4()), "document_id": doc["id"], "version_id": doc["current_version"], "name": "복구 양식"}
    def fail(_):
        raise RuntimeError("injected commit failure")
    event.listen(app.state.engine, "commit", fail)
    try:
        assert c.post("/api/templates", json=value).status_code == 500
    finally:
        event.remove(app.state.engine, "commit", fail)
    assert counts(app) == (1, 1, 0, 0)
    assert c.post("/api/templates", json=value).status_code == 200
    assert counts(app) == (1, 1, 1, 0)


def test_reader_bookmark_is_personal_and_revocation_blocks_all_market_paths(secured):
    c, app, _, _ = secured
    doc = document(c)
    tid, value = register(c, doc)
    share(c, doc, "reader")
    as_user(c, "bob")
    item = detail(c, tid)
    assert not item["can_manage"]
    favorite_path = f"/api/template-market/{tid}/favorite"
    assert c.put(favorite_path, json={"favorite": True}).json()["favorite"]
    assert c.put(favorite_path, json={"favorite": True}).json()["favorite"]
    assert counts(app) == (1, 1, 1, 1)
    assert c.get("/api/template-market?favorite=true").json()["total"] == 1
    assert c.put(f"/api/template-market/{tid}", json=metadata(item)).status_code == 403
    assert c.post("/api/templates", json=value).status_code == 403
    assert c.get(f"/api/template-market/{tid}/file").content == package()
    body = {"document_id": str(uuid.uuid4()), "name": "독립 사본", "request_id": "copy"}
    applied = c.post(f"/api/templates/{tid}/apply", json=body)
    assert applied.status_code == 200
    as_user(c, "alice")
    assert not detail(c, tid)["favorite"]
    assert c.get("/api/template-market?favorite=true").json()["total"] == 0
    assert c.delete(f"/api/documents/{doc['id']}/access/bob").status_code == 200
    as_user(c, "bob")
    assert c.get("/api/template-market").json()["items"] == []
    assert c.get("/api/template-market?favorite=true").json()["items"] == []
    assert c.get(f"/api/template-market/{tid}").status_code == 404
    assert c.get(f"/api/template-market/{tid}/file").status_code == 404
    assert c.put(favorite_path, json={"favorite": False}).status_code == 404
    assert c.put(f"/api/template-market/{tid}", json=metadata(item)).status_code == 404
    assert c.post("/api/templates", json=value).status_code == 404
    assert c.post(f"/api/templates/{tid}/apply", json=body).status_code == 404
    assert c.get(f"/api/documents/{applied.json()['id']}").status_code == 200
    as_user(c, "alice")
    share(c, doc, "reader")
    as_user(c, "bob")
    assert detail(c, tid)["favorite"]
    assert not c.put(favorite_path, json={"favorite": False}).json()["favorite"]


def test_editor_downgrade_rechecks_edit_and_registration_retries(secured):
    c, _, _, _ = secured
    doc = document(c)
    share(c, doc)
    as_user(c, "bob")
    tid, value = register(c, doc)
    item = detail(c, tid)
    assert item["can_manage"]
    assert c.put(f"/api/template-market/{tid}", json=metadata(item, description="공동 편집")).status_code == 200
    as_user(c, "alice")
    share(c, doc, "reader")
    as_user(c, "bob")
    assert not detail(c, tid)["can_manage"]
    assert c.put(f"/api/template-market/{tid}", json=metadata(detail(c, tid))).status_code == 403
    assert c.post("/api/templates", json=value).status_code == 403
    assert c.get(f"/api/template-market/{tid}/file").status_code == 200


def test_metadata_optimistic_conflict_preserves_blob_and_source_linkage(secured):
    c, app, _, folder = secured
    doc = document(c)
    tid, _ = register(c, doc)
    item = detail(c, tid)
    before = {file.name: digest(file.read_bytes()) for file in folder.iterdir()}
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda name: c.put(f"/api/template-market/{tid}",
                            json=metadata(item, name=name, category="계획서")), ["일정 양식 A", "일정 양식 B"]))
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    final = detail(c, tid)
    assert final["revision"] == 2 and final["version_id"] == doc["current_version"]
    assert final["sha256"] == item["sha256"] and final["category"] == "계획서"
    assert before == {file.name: digest(file.read_bytes()) for file in folder.iterdir()}
    assert counts(app) == (1, 1, 1, 0)
    assert c.put(f"/api/template-market/{tid}", json=metadata(final, document_id=str(uuid.uuid4()))).status_code == 422
    assert c.put(f"/api/template-market/{tid}", json=metadata(final, version_id=str(uuid.uuid4()))).status_code == 422


def test_search_category_sort_paging_favorite_and_legacy_inference(secured):
    c, app, _, _ = secured
    doc = document(c)
    ids = [register(c, doc, name=name, category=category, tags=tags)[0] for name, category, tags in (
        ("A 보고", "보고서", ["회의"]), ("B 회의록", "회의록", ["회의", "결정"]),
        ("C 계획", "계획서", ["일정"]), ("D 점검", "점검표", ["점검"]))]
    legacy_id = str(uuid.uuid4())
    with transaction(app.state.engine) as db:
        db.add(Template(id=legacy_id, document_id=doc["id"], version_id=doc["current_version"],
                        name="E 회의 기록", description="레거시", tags='["회의"]', created_at=0))
    listed = c.get("/api/template-market?sort=name&offset=1&limit=2").json()
    assert listed["total"] == 5 and [item["id"] for item in listed["items"]] == ids[1:3]
    assert c.get("/api/template-market?offset=100").json()["items"] == []
    filtered = c.get("/api/template-market?q=회의&category=회의록&sort=relevance").json()
    assert filtered["total"] == 2
    assert {item["id"] for item in filtered["items"]} == {ids[1], legacy_id}
    assert {facet["name"]: facet["count"] for facet in filtered["categories"]}["보고서"] == 1
    assert detail(c, legacy_id)["revision"] == 0 and detail(c, legacy_id)["category"] == "회의록"
    c.put(f"/api/template-market/{ids[2]}/favorite", json={"favorite": True})
    assert [item["id"] for item in c.get("/api/template-market?favorite=true").json()["items"]] == [ids[2]]
    assert c.get("/api/template-market?q=없는검색어").json()["total"] == 0
    # The original recommendation route keeps its array/score/reason contract.
    recommended = c.get("/api/templates?q=회의").json()
    assert isinstance(recommended, list) and all("score" in item and "reason" in item for item in recommended)


@pytest.mark.parametrize("mode,status", [("corrupt", 422), ("invalid-hwpx", 422), ("missing", 503), ("path", 422)])
def test_invalid_source_never_registers_previews_or_applies(secured, mode, status):
    c, app, _, folder = secured
    doc = document(c)
    tid, value = register(c, doc)
    with transaction(app.state.engine) as db:
        version = db.get(Version, doc["current_version"])
        path = folder / version.storage_key
        if mode == "corrupt":
            path.write_bytes(b"broken")
        elif mode == "invalid-hwpx":
            path.write_bytes(b"non hwpx but matching metadata")
            version.size, version.sha256 = path.stat().st_size, digest(path.read_bytes())
        elif mode == "missing":
            path.unlink()
        else:
            version.storage_key = "../outside.hwpx"
    initial = counts(app)
    assert c.get(f"/api/template-market/{tid}/file").status_code == status
    assert c.post("/api/templates", json=value | {"id": str(uuid.uuid4())}).status_code == status
    assert c.post("/api/templates", json=value).status_code == status
    body = {"document_id": str(uuid.uuid4()), "name": "실패할 사본", "request_id": "copy"}
    assert c.post(f"/api/templates/{tid}/apply", json=body).status_code == status
    assert counts(app) == initial


def test_cross_document_version_or_registration_id_never_leaks_metadata(secured):
    c, app, _, _ = secured
    doc = document(c)
    other = document(c)
    tid, value = register(c, doc)
    invalid = value | {"id": str(uuid.uuid4()), "version_id": other["current_version"]}
    assert c.post("/api/templates", json=invalid).status_code == 404
    collided = c.post("/api/templates", json=value | {"document_id": other["id"], "version_id": other["current_version"]})
    assert collided.status_code == 404 and tid not in collided.text
    share(c, other)
    as_user(c, "bob")
    assert c.post("/api/templates", json=value | {"document_id": other["id"], "version_id": other["current_version"]}).status_code == 404
    assert c.get(f"/api/template-market/{tid}").status_code == 404
    as_user(c, "alice")
    with transaction(app.state.engine) as db:
        db.get(Template, tid).version_id = other["current_version"]
    assert c.get(f"/api/template-market/{tid}").status_code == 404
    assert c.get(f"/api/template-market/{tid}/file").status_code == 404
    assert c.get("/api/template-market").json()["total"] == 0
    assert counts(app) == (2, 2, 1, 0)


def test_apply_response_loss_retries_keep_original_copy_after_catalog_edits(secured):
    c, app, _, folder = secured
    doc = document(c)
    tid, _ = register(c, doc)
    body = {"document_id": str(uuid.uuid4()), "name": "고정 사본", "request_id": "apply"}
    path = f"/api/templates/{tid}/apply"
    first = c.post(path, json=body)
    assert first.status_code == 200
    item = detail(c, tid)
    assert c.put(f"/api/template-market/{tid}", json=metadata(item, name="새 마켓 이름")).status_code == 200
    before = {file.name: digest(file.read_bytes()) for file in folder.iterdir()}
    assert c.post(path, json=body).json() == first.json()
    destination = first.json()["id"]
    renamed = c.patch(f"/api/documents/{destination}", json={"name": "사용자가 이름을 변경한 사본"})
    assert renamed.status_code == 200
    retry = c.post(path, json=body)
    assert retry.status_code == 200 and retry.json()["id"] == destination
    assert retry.json()["current_version"] == first.json()["current_version"]
    assert retry.json()["name"] == "사용자가 이름을 변경한 사본.hwpx"
    # The retry key does not modify the already-created copy's current metadata.
    changed_retry = c.post(path, json=body | {"name": "다른 이름"})
    assert changed_retry.status_code == 200 and changed_retry.json() == retry.json()
    assert counts(app) == (2, 2, 1, 0)
    assert before == {file.name: digest(file.read_bytes()) for file in folder.iterdir()}


def test_additive_catalog_survives_restart_and_edits_legacy_rows(secured):
    c, app, url, folder = secured
    doc = document(c)
    tid, _ = register(c, doc)
    legacy_id = str(uuid.uuid4())
    with transaction(app.state.engine) as db:
        db.add(Template(id=legacy_id, document_id=doc["id"], version_id=doc["current_version"],
                        name="옛 계획 양식", description="", tags="[]", created_at=0))
    c.put(f"/api/template-market/{tid}/favorite", json={"favorite": True})
    initial = counts(app)
    with TestClient(create_app(url, folder), headers={"Authorization": "Bearer " + TOKENS["alice"]}) as restarted:
        assert detail(restarted, tid)["favorite"]
        item = detail(restarted, legacy_id)
        assert item["revision"] == 0
        updated = restarted.put(f"/api/template-market/{legacy_id}", json=metadata(item, category="점검표"))
        assert updated.status_code == 200 and updated.json()["revision"] == 1
    assert counts(app) == initial
    assert c.get(f"/api/template-market/{tid}/file").content == package()


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "category=bad", "sort=bad"])
def test_market_query_validation(secured, query):
    c, _, _, _ = secured
    assert c.get("/api/template-market?" + query).status_code == 422


def test_no_auth_and_other_identity_never_see_catalog(secured):
    c, _, _, _ = secured
    doc = document(c)
    tid, _ = register(c, doc)
    as_user(c, "eve")
    assert c.get("/api/template-market").json()["total"] == 0
    assert c.get(f"/api/template-market/{tid}").status_code == 404
    c.headers.pop("Authorization")
    assert c.get("/api/template-market").status_code == 401
    assert c.get(f"/api/template-market/{tid}/file").status_code == 401


def test_template_metadata_rejects_invalid_utf8_without_registration(secured):
    c, app, _, _ = secured
    doc = document(c)
    response = c.post("/api/templates", content=json.dumps({"id": str(uuid.uuid4()),
                      "document_id": doc["id"], "version_id": doc["current_version"],
                      "name": "invalid \ud800"}), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert counts(app) == (1, 1, 0, 0)
