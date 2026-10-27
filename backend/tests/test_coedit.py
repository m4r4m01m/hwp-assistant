import io
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree as E
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Document, Version, transaction
from app.coedit_merge import merge_hwpx, merge_text
from test_storage import package


def document(a="첫 문단 원문", b="둘째 문단 원문"):
    return package(extra={"Contents/section0.xml": f'<section><p id="1"><t>{a}</t></p><p id="2"><t>{b}</t></p></section>'})


def text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        return "|".join(E.fromstring(source.read("Contents/section0.xml")).itertext())


@pytest.fixture
def room(tmp_path):
    app = create_app(f"sqlite:///{tmp_path}/test.db", tmp_path / "files")
    with TestClient(app) as client:
        data = document()
        response = client.post("/api/documents", params={"name": "공동.hwpx", "document_id": str(uuid.uuid4())}, content=data, headers={"Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 200, response.text
        doc = response.json(); path = f"/api/documents/{doc['id']}/coedit"
        yield client, app, doc, path, data


def join(client, path, key=None):
    response = client.post(path, json={"request_id": key or str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def write(client, path, token, base, data, key=None):
    return client.post(path + "/changes", params={"base_version": base, "request_id": key or str(uuid.uuid4())}, headers={"X-Coedit-Session": token}, content=data)


def downloaded(client, doc, version):
    return client.get(f"/api/documents/{doc['id']}/versions/{version}/file").content


def test_two_simultaneous_writers_merge_and_preserve_history(room):
    client, app, doc, path, original = room
    a, b = join(client, path), join(client, path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda args: write(client, path, args[0], doc["current_version"], args[1]), [(a, document(a="첫 문단 변경")), (b, document(b="둘째 문단 변경"))]))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    state = client.put(path, headers={"X-Coedit-Session": a}).json()
    assert len(state["participants"]) == 2
    assert text(downloaded(client, doc, state["current_version"])) == "첫 문단 변경|둘째 문단 변경"
    assert downloaded(client, doc, doc["current_version"]) == original
    versions = client.get(f"/api/documents/{doc['id']}/versions").json()
    assert len(versions) == 3
    assert all(v["author"]["user_id"] == "local" for v in versions)


def test_overlap_conflict_does_not_overwrite(room):
    client, _, doc, path, _ = room
    a, b = join(client, path), join(client, path)
    first = write(client, path, a, doc["current_version"], document(a="첫 문단 A")).json()
    response = write(client, path, b, doc["current_version"], document(a="첫 문단 B"))
    assert response.status_code == 409
    assert text(downloaded(client, doc, first["version_id"])) == "첫 문단 A|둘째 문단 원문"
    assert len(client.get(f"/api/documents/{doc['id']}/versions").json()) == 2


def test_lost_save_response_replays_after_another_save(room):
    client, _, doc, path, _ = room
    a, b = join(client, path), join(client, path); key = str(uuid.uuid4()); edit = document(a="A")
    first = write(client, path, a, doc["current_version"], edit, key).json()
    second = write(client, path, b, first["version_id"], document(a="A", b="B")).json()
    retry = write(client, path, a, doc["current_version"], edit, key)
    assert retry.status_code == 200 and retry.json()["replayed"]
    assert retry.json()["version_id"] == first["version_id"]
    assert retry.json()["current_version"] == second["version_id"]
    assert write(client, path, b, doc["current_version"], edit, key).status_code == 409
    assert write(client, path, a, doc["current_version"], document(a="different"), key).status_code == 409


def test_exclusive_and_shared_leases_cannot_overlap(room):
    client, _, doc, path, _ = room
    exclusive = f"/api/documents/{doc['id']}/session"
    first = client.post(exclusive).json()["token"]
    assert client.post(path, json={"request_id": str(uuid.uuid4())}).status_code == 409
    client.delete(exclusive, headers={"X-Edit-Session": first})
    a, b = join(client, path), join(client, path)
    assert client.post(exclusive).status_code == 409
    client.delete(path, headers={"X-Coedit-Session": a})
    assert client.post(exclusive).status_code == 409
    client.delete(path, headers={"X-Coedit-Session": b})
    assert client.post(exclusive).status_code == 200
    assert write(client, path, a, doc["current_version"], document(a="stale")).status_code == 409


def test_join_is_idempotent_and_session_survives_server_restart(room):
    client, app, doc, path, _ = room
    key = str(uuid.uuid4()); token = join(client, path, key)
    assert join(client, path, key) == token
    new = create_app(str(app.state.engine.url), app.state.folder)
    with TestClient(new) as restarted:
        assert restarted.put(path, headers={"X-Coedit-Session": token}).status_code == 200
        assert write(restarted, path, token, doc["current_version"], document(a="재시작")).status_code == 200


def test_expired_generation_cannot_overwrite_new_room(room):
    client, app, doc, path, _ = room
    a = join(client, path)
    with transaction(app.state.engine) as db: db.get(Document, doc["id"]).lease_until = time.time() - 1
    b = join(client, path)
    assert a != b
    assert write(client, path, a, doc["current_version"], document(a="stale")).status_code == 409


@pytest.mark.parametrize("base,current,incoming,expected", [
    ("가나다라마바사", "가A다라마바사", "가나다라마B사", "가A다라마B사"),
    ("abc", "abcX", "Yabc", "YabcX"),
    ("abc", "abcX", "abcX", "abcX"),
])
def test_nonoverlapping_same_paragraph(base, current, incoming, expected):
    assert merge_text(base, current, incoming) == expected


@pytest.mark.parametrize("left,right", [("aXbc", "aYbc"), ("ac", "aBc"), ("aXXc", "aYc")])
def test_overlapping_characters_are_not_guessed(left, right):
    with pytest.raises(HTTPException): merge_text("abc", left, right)


def test_concurrent_structure_changes_are_rejected():
    base = document()
    added = package(extra={"Contents/section0.xml": '<section><p id="1"><t>first</t></p><p id="3"><t>new</t></p><p id="2"><t>second</t></p></section>'})
    with pytest.raises(HTTPException): merge_hwpx(base, added, document(b="다른 수정"))
