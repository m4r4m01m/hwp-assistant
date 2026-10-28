"""Synthetic storage/authorization checks; not native editor compatibility tests."""
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Version, transaction
from app.document_branches import IntegrationRequest
from test_coedit import document, text
from test_storage import save

TOKENS = {name: name + "-" + name[0] * 40 for name in ("alice", "bob", "eve")}


def user(client, name):
    client.headers["Authorization"] = "Bearer " + TOKENS[name]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HWP_AUTH_TOKENS", json.dumps(TOKENS))
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "private")
    app = create_app(f"sqlite:///{tmp_path}/test.db", tmp_path / "files")
    with TestClient(app) as client:
        user(client, "alice")
        response = client.post("/api/documents", params={"name": "공통.hwpx", "document_id": str(uuid.uuid4())},
            headers={"Idempotency-Key": str(uuid.uuid4())}, content=document())
        assert response.status_code == 200, response.text
        source = response.json()
        assert client.put(f"/api/documents/{source['id']}/access", json={"user_id": "bob", "role": "reader"}).status_code == 200
        user(client, "bob")
        body = {"document_id": str(uuid.uuid4()), "source_version": source["current_version"], "request_id": str(uuid.uuid4()), "name": "검토 작업본.hwpx"}
        response = client.post(f"/api/documents/{source['id']}/branches", json=body)
        assert response.status_code == 200, response.text
        yield client, app, source, response.json()["document"], body


def edit(client, doc, data):
    token = client.post(f"/api/documents/{doc['id']}/session").json()["token"]
    response = save(client, doc, token, data=data)
    assert response.status_code == 200, response.text
    client.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": token})
    return {**doc, "current_version": response.json()["id"]}


def submit(client, source, branch, key=None):
    return client.post(f"/api/documents/{branch['id']}/integration-requests", json={
        "request_id": key or str(uuid.uuid4()), "source_version": branch["current_version"], "target_version": source["current_version"]})


def request_ready(setup):
    client, app, source, branch, _ = setup
    branch = edit(client, branch, document(a="작업자 수정"))
    response = submit(client, source, branch)
    assert response.status_code == 200, response.text
    user(client, "alice")
    identity = response.json()["id"]
    review = client.get(f"/api/integration-requests/{identity}/review")
    assert review.status_code == 200, review.text
    return client, app, source, branch, identity, review.json()


def test_private_branch_is_not_shared_copy(setup):
    client, _, source, branch, _ = setup
    assert {d["id"] for d in client.get("/api/documents").json()} == {source["id"], branch["id"]}
    user(client, "alice")
    assert client.get(f"/api/documents/{branch['id']}").status_code == 404
    assert branch["id"] not in [d["id"] for d in client.get("/api/documents").json()]
    flow = client.get(f"/api/documents/{source['id']}/branches").json()
    assert flow["branches"] == []
    user(client, "eve")
    assert client.get(f"/api/documents/{source['id']}/branches").status_code == 404


def test_create_retry_does_not_reset_later_branch_edits(setup):
    client, _, source, branch, body = setup
    latest = edit(client, branch, document(a="후속 수정"))
    response = client.post(f"/api/documents/{source['id']}/branches", json=body)
    assert response.status_code == 200
    assert response.json()["document"]["current_version"] == latest["current_version"]
    body["name"] = "다른 작업본.hwpx"
    assert client.post(f"/api/documents/{source['id']}/branches", json=body).status_code == 409


def test_submit_freezes_content_and_requires_other_owner(setup):
    client, _, source, branch, identity, review = request_ready(setup)
    assert "작업자 수정" in review["after_text"] and "첫 문단 원문" in review["before_text"]
    user(client, "bob")
    edit(client, branch, document(a="아직 제출하지 않은 후속 수정"))
    assert client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}).status_code == 403
    user(client, "alice")
    assert client.get(f"/api/integration-requests/{identity}/review").json()["after_text"] == review["after_text"]
    accepted = client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]})
    assert accepted.status_code == 200, accepted.text
    result = client.get(f"/api/documents/{source['id']}/versions/{accepted.json()['merged_version_id']}/file").content
    assert text(result) == "작업자 수정|둘째 문단 원문"
    assert client.get(f"/api/documents/{source['id']}/versions/{source['current_version']}/file").content == document()


def test_merge_three_way_and_preview_bytes_match(setup):
    client, _, source, branch, _ = setup
    branch = edit(client, branch, document(a="개요 수정"))
    user(client, "alice"); source = edit(client, source, document(b="공통 일정 수정"))
    user(client, "bob"); response = submit(client, source, branch)
    assert response.status_code == 200, response.text
    identity = response.json()["id"]
    user(client, "alice")
    review = client.get(f"/api/integration-requests/{identity}/review").json()
    preview = client.get(f"/api/integration-requests/{identity}/preview").content
    assert text(preview) == "개요 수정|공통 일정 수정"
    accepted = client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}).json()
    saved = client.get(f"/api/documents/{source['id']}/versions/{accepted['merged_version_id']}/file").content
    assert saved == preview


def test_overlapping_changes_block_submission(setup):
    client, _, source, branch, _ = setup
    branch = edit(client, branch, document(a="첫 문단 작업본"))
    user(client, "alice"); source = edit(client, source, document(a="첫 문단 공통"))
    user(client, "bob")
    assert submit(client, source, branch).status_code == 409


def test_stale_review_target_and_wrong_digest_never_publish(setup):
    client, _, source, _, identity, review = request_ready(setup)
    assert client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": "0" * 64}).status_code == 409
    edit(client, source, document(b="검토 중 공통 문서 변경"))
    assert client.get(f"/api/integration-requests/{identity}/review").status_code == 409
    assert client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}).status_code == 409


def test_active_editor_blocks_merge(setup):
    client, _, source, _, identity, review = request_ready(setup)
    token = client.post(f"/api/documents/{source['id']}/session").json()["token"]
    assert client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}).status_code == 409
    client.delete(f"/api/documents/{source['id']}/session", headers={"X-Edit-Session": token})
    assert client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}).status_code == 200


def test_lost_accept_response_replays_after_newer_version(setup):
    client, _, source, _, identity, review = request_ready(setup)
    body = {"merged_sha256": review["merged_sha256"]}
    first = client.post(f"/api/integration-requests/{identity}/accept", json=body).json()
    edit(client, {**source, "current_version": first["merged_version_id"]}, document(a="작업자 수정", b="통합 후 후속 편집"))
    replay = client.post(f"/api/integration-requests/{identity}/accept", json=body)
    assert replay.status_code == 200 and replay.json() == first
    assert len(client.get(f"/api/documents/{source['id']}/versions").json()) == 3


def test_revoked_author_cannot_merge_or_read_private_work(setup):
    client, _, source, branch, identity, review = request_ready(setup)
    assert client.delete(f"/api/documents/{source['id']}/access/bob").status_code == 200
    assert client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}).status_code == 409
    archived = client.get(f"/api/integration-requests/{identity}/source")
    assert archived.status_code == 200 and text(archived.content) == "작업자 수정|둘째 문단 원문"
    user(client, "bob")
    assert client.get(f"/api/documents/{branch['id']}").status_code == 404
    assert client.get(f"/api/integration-requests/{identity}/preview").status_code == 404
    assert client.get(f"/api/integration-requests/{identity}/source").status_code == 404
    user(client, "eve")
    assert client.get(f"/api/integration-requests/{identity}/source").status_code == 404


def test_reject_keeps_both_documents_unchanged(setup):
    client, _, source, branch, identity, review = request_ready(setup)
    result = client.post(f"/api/integration-requests/{identity}/reject")
    assert result.status_code == 200 and result.json()["status"] == "rejected"
    assert client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}).status_code == 409
    assert client.get(f"/api/documents/{source['id']}").json()["current_version"] == source["current_version"]
    user(client, "bob")
    assert client.get(f"/api/documents/{branch['id']}").json()["current_version"] == branch["current_version"]


def test_submit_retry_and_request_collision(setup):
    client, _, source, branch, _ = setup
    branch = edit(client, branch, document(a="제출")); key = str(uuid.uuid4())
    first = submit(client, source, branch, key)
    latest = edit(client, branch, document(a="제출 뒤 편집"))
    replay = submit(client, source, branch, key)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert submit(client, source, latest, key).status_code == 409


def test_atomic_duplicate_accept_and_restart(setup):
    client, app, source, _, identity, review = request_ready(setup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["merged_version_id"] == results[1].json()["merged_version_id"]
    with TestClient(create_app(str(app.state.engine.url), app.state.folder)) as restarted:
        user(restarted, "alice")
        flow = restarted.get(f"/api/documents/{source['id']}/branches").json()
        assert flow["requests"][0]["status"] == "merged" and len(flow["versions"]) == 2


def test_tampered_immutable_blob_blocks_merge(setup):
    client, app, _, branch, identity, review = request_ready(setup)
    with transaction(app.state.engine) as db:
        key = db.get(Version, branch["current_version"]).storage_key
    (app.state.folder / key).write_bytes(document(a="tampered synthetic test"))
    assert client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}).status_code == 422


def test_shared_library_never_exposes_private_branch(setup, monkeypatch):
    from app.accounts import UserAccount
    client, app, source, branch, _ = setup
    with transaction(app.state.engine) as db:
        for name in TOKENS:
            db.add(UserAccount(user_id=name, name=name, password_hash="synthetic-unusable", session_revision="synthetic", created_at=time.time()))
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "1")
    monkeypatch.setenv("HWP_LIBRARY_ACCESS_MODE", "shared")
    user(client, "eve")
    assert client.get(f"/api/documents/{source['id']}").status_code == 200
    assert client.get(f"/api/documents/{branch['id']}").status_code == 404
    assert branch["id"] not in [item["id"] for item in client.get("/api/documents").json()]
    assert client.post(f"/api/documents/{branch['id']}/coedit", json={"request_id": str(uuid.uuid4())}).status_code == 404
    assert client.get(f"/api/documents/{source['id']}/branches").json()["branches"] == []


def test_accept_and_reject_are_serialized_and_reject_replay_is_stable(setup):
    client, _, source, _, identity, review = request_ready(setup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = pool.submit(lambda: client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]}))
        rejected = pool.submit(lambda: client.post(f"/api/integration-requests/{identity}/reject"))
        responses = [accepted.result(), rejected.result()]
    assert sorted(r.status_code for r in responses) == [200, 409]
    current = client.get(f"/api/documents/{source['id']}/branches").json()["requests"][0]
    if current["status"] == "rejected":
        replay = client.post(f"/api/integration-requests/{identity}/reject")
        assert replay.json() == current
    else:
        assert current["merged_version_id"] is not None
    assert len(client.get(f"/api/documents/{source['id']}/versions").json()) == (2 if current["status"] == "merged" else 1)


def test_inactive_author_does_not_log_out_reviewer(setup, monkeypatch):
    from app.accounts import UserAccount
    client, app, _, _, identity, review = request_ready(setup)
    with transaction(app.state.engine) as db:
        db.add(UserAccount(user_id="bob", name="bob", password_hash="synthetic-unusable", session_revision="synthetic", created_at=time.time(), is_active=False))
    monkeypatch.setenv("HWP_ACCOUNTS_ENABLED", "1")
    response = client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": review["merged_sha256"]})
    assert response.status_code == 409
    assert not response.headers.get("set-cookie")


def test_review_text_lists_nested_table_cells_once():
    from app.document_branches import _text
    from test_storage import package
    data = package(extra={"Contents/section0.xml": '''<section xmlns:hp="urn:test">
      <hp:p><hp:run><hp:t>비교표</hp:t><hp:tbl><hp:tr>
        <hp:tc><hp:subList><hp:p><hp:run><hp:t>대상 기업</hp:t></hp:run></hp:p></hp:subList></hp:tc>
        <hp:tc><hp:subList><hp:p><hp:run><hp:t>조치 현황</hp:t></hp:run></hp:p></hp:subList></hp:tc>
      </hp:tr></hp:tbl></hp:run></hp:p>
      <hp:p><hp:run><hp:t>후속 조치</hp:t></hp:run></hp:p>
    </section>'''})
    result, truncated = _text(data)
    assert result == "비교표\n대상 기업\n조치 현황\n후속 조치"
    assert not truncated


def test_graph_uses_actual_save_branch_merge_edges_and_hides_unsubmitted_versions(setup):
    client, _, source, branch, _ = setup
    initial = branch["current_version"]
    branch = edit(client, branch, document(a="제출할 수정"))
    own = client.get(f"/api/documents/{branch['id']}/branches").json()["graph"]
    assert {"from": source["current_version"], "to": initial, "kind": "branch"} in own["edges"]
    assert {"from": initial, "to": branch["current_version"], "kind": "save"} in own["edges"]
    assert next(n for n in own["nodes"] if n["id"] == branch["current_version"])["document"]["branch_source_id"] == source["id"]
    request = submit(client, source, branch).json()
    later = edit(client, branch, document(a="미제출 내용"))
    user(client, "alice")
    before = client.get(f"/api/documents/{source['id']}/branches").json()["graph"]
    nodes = {node["id"]: node for node in before["nodes"]}
    assert later["current_version"] not in nodes
    assert nodes[initial]["kind"] == "anchor" and "author" not in nodes[initial]
    frozen = nodes[branch["current_version"]]
    assert frozen["can_read"] and not frozen["can_edit"] and not frozen["can_restore"]
    assert frozen["document"] is None
    assert text(client.get("/api" + frozen["file_url"]).content) == "제출할 수정|둘째 문단 원문"
    preview = client.get(f"/api/integration-requests/{request['id']}/review").json()
    accepted = client.post(f"/api/integration-requests/{request['id']}/accept", json={"merged_sha256": preview["merged_sha256"]}).json()
    flow = client.get(f"/api/documents/{source['id']}/branches").json()
    assert {"from": branch["current_version"], "to": accepted["merged_version_id"], "kind": "merge"} in flow["graph"]["edges"]
    assert {"from": source["current_version"], "to": accepted["merged_version_id"], "kind": "save"} in flow["graph"]["edges"]
    assert {"size", "sha256", "document_id", "restore_source", "cause"} <= flow["versions"][0].keys()
    # Processed requests remain immutable historical nodes, even after newer saves.
    edit(client, {**source, "current_version": accepted["merged_version_id"]}, document(a="새 공통 편집"))
    assert text(client.get("/api" + frozen["file_url"]).content) == "제출할 수정|둘째 문단 원문"


def test_graph_truncation_has_explicit_parent_anchors_not_invented_edges(setup):
    client, app, source, _, _ = setup
    from app.db import Document
    identities = []
    with transaction(app.state.engine) as db:
        doc = db.get(Document, source["id"])
        original = db.get(Version, source["current_version"])
        base = original.id
        for index in range(105):
            identity = str(uuid.uuid4()); identities.append(identity)
            db.add(Version(id=identity, document_id=doc.id, base_version=base, storage_key=f"synthetic-graph-{index}.hwpx",
                size=original.size, sha256=original.sha256, created_at=original.created_at + index + 1,
                cause="web-edit", request_id=identity, fingerprint="f" * 64, session_hash="s" * 64))
            base = identity
        doc.current_version = base
    user(client, "alice")
    graph = client.get(f"/api/documents/{source['id']}/branches").json()["graph"]
    assert graph["truncated"] and graph["limits"]["root_versions"] == 100
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert len(nodes) == 101
    assert nodes[identities[4]]["kind"] == "anchor"
    assert identities[3] not in nodes
    assert {"from": identities[4], "to": identities[5], "kind": "save"} in graph["edges"]
    assert all(edge["from"] in nodes and edge["to"] in nodes for edge in graph["edges"])


def test_contributor_cannot_approve_even_after_invitation_removed(setup):
    from app.accounts import UserAccount
    client, app, source, branch, _ = setup
    with transaction(app.state.engine) as db:
        db.add(UserAccount(user_id="alice", name="alice", password_hash="synthetic-unusable", session_revision="synthetic", created_at=time.time()))
    assert client.put(f"/api/documents/{branch['id']}/branch-participants/alice", json={}).status_code == 200
    user(client, "alice")
    branch = edit(client, branch, document(a="관리자가 직접 수정한 내용"))
    assert submit(client, source, branch).status_code == 404
    user(client, "bob")
    requested = submit(client, source, branch)
    assert requested.status_code == 200, requested.text
    identity = requested.json()["id"]
    assert client.delete(f"/api/documents/{branch['id']}/branch-participants/alice").status_code == 200
    user(client, "alice")
    preview = client.get(f"/api/integration-requests/{identity}/review")
    assert preview.status_code == 200, preview.text
    assert not preview.json()["request"]["can_review"]
    assert client.post(f"/api/integration-requests/{identity}/accept", json={"merged_sha256": preview.json()["merged_sha256"]}).status_code == 403
