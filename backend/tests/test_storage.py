import io
import uuid
import zipfile
import hashlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Document, transaction


def package(text="원본", extra=None):
    """Synthetic ZIP fixture for storage tests; NOT an editor compatibility fixture."""
    f = io.BytesIO()
    with zipfile.ZipFile(f, "w") as z:
        for name, data in {
            "mimetype": "application/hwp+zip",
            "Contents/header.xml": "<head/>",
            "Contents/content.hpf": "<package/>",
            "Contents/section0.xml": f"<section><p>{text}</p></section>",
            **(extra or {}),
        }.items():
            z.writestr(zipfile.ZipInfo(name), data)
    return f.getvalue()


@pytest.fixture
def env(tmp_path):
    url = f"sqlite:///{tmp_path}/test.db"
    app = create_app(url, tmp_path / "files")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, app, url, tmp_path / "files"


def create(c, data=None):
    r = c.post(
        "/api/documents",
        params={"name": "문서.hwpx", "document_id": str(uuid.uuid4())},
        content=data or package(),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    s = c.post(f"/api/documents/{d['id']}/session")
    assert s.status_code == 200
    return d, s.json()["token"]


def save(c, d, t, data=None, key=None, base=None, cause="web-edit"):
    return c.post(
        f"/api/documents/{d['id']}/versions",
        params={"base_version": base or d["current_version"], "cause": cause},
        content=data or package("수정"),
        headers={"X-Edit-Session": t, "Idempotency-Key": key or str(uuid.uuid4())},
    )


def file(c, d, v):
    return c.get(f"/api/documents/{d['id']}/versions/{v}/file").content


def test_original_versions_restore_and_restart(env):
    c, app, url, folder = env
    original = package()
    d, t = create(c, original)
    assert (
        hashlib.sha256(file(c, d, d["current_version"])).digest()
        == hashlib.sha256(original).digest()
    )
    edited = save(c, d, t)
    assert edited.status_code == 200
    v2 = edited.json()["id"]
    reuploaded = save(c, d, t, package("한글 재업로드"), base=v2, cause="reupload")
    assert reuploaded.status_code == 200
    restored = c.post(
        f"/api/documents/{d['id']}/versions/{d['current_version']}/restore",
        params={"base_version": reuploaded.json()["id"]},
        headers={"X-Edit-Session": t, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert restored.status_code == 200
    assert file(c, d, restored.json()["id"]) == original
    assert file(c, d, v2) == package("수정")
    assert file(c, d, d["current_version"]) == original
    with TestClient(create_app(url, folder)) as restarted:
        assert len(restarted.get(f"/api/documents/{d['id']}/versions").json()) == 4
        assert (
            restarted.get("/api/documents").json()[0]["current_version"]
            == restored.json()["id"]
        )


def test_concurrent_writers_and_idempotency(env):
    c, _, _, _ = env
    d, t = create(c)
    assert c.post(f"/api/documents/{d['id']}/session").status_code == 409
    key = str(uuid.uuid4())
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: save(c, d, t, key=key), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["id"] == results[1].json()["id"]
    assert save(c, d, t).status_code == 409
    assert save(c, d, t, package("다른 데이터"), key=key).status_code == 409
    assert len(c.get(f"/api/documents/{d['id']}/versions").json()) == 2


def test_overlapping_different_saves(env):
    c, _, _, _ = env
    d, t = create(c)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda i: save(c, d, t, package(str(i))), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]


def test_expired_lease_and_wrong_token(env):
    c, app, _, _ = env
    d, t = create(c)
    assert save(c, d, "wrong").status_code == 409
    with transaction(app.state.engine) as db:
        db.get(Document, d["id"]).lease_until = 0
    assert save(c, d, t).status_code == 409
    assert (
        c.put(
            f"/api/documents/{d['id']}/session", headers={"X-Edit-Session": t}
        ).status_code
        == 409
    )
    new = c.post(f"/api/documents/{d['id']}/session").json()["token"]
    assert new != t
    assert save(c, d, new).status_code == 200


def test_old_receipt_cannot_report_latest_saved(env):
    c, app, _, _ = env
    d, token = create(c)
    key = str(uuid.uuid4())
    first = save(c, d, token, key=key).json()
    second = save(c, d, token, package("최신"), base=first["id"])
    assert second.status_code == 200
    assert save(c, d, token, key=key).status_code == 409
    assert c.get("/api/documents").json()[0]["current_version"] == second.json()["id"]


def test_file_write_failure_keeps_latest(env, monkeypatch):
    c, app, _, _ = env
    d, t = create(c)

    def fail(_):
        raise OSError("injected fsync failure")

    monkeypatch.setattr("app.main.os.fsync", fail)
    assert save(c, d, t).status_code == 500
    assert c.get("/api/documents").json()[0]["current_version"] == d["current_version"]
    assert len(c.get(f"/api/documents/{d['id']}/versions").json()) == 1


def test_db_commit_failure_keeps_latest(env, monkeypatch):
    c, app, _, _ = env
    d, t = create(c)
    from sqlalchemy import event

    def fail(conn):
        raise RuntimeError("injected commit failure")

    event.listen(app.state.engine, "commit", fail)
    assert save(c, d, t).status_code == 500
    event.remove(app.state.engine, "commit", fail)
    assert c.get("/api/documents").json()[0]["current_version"] == d["current_version"]


def test_zip_validation_and_loss_guard(env):
    c, _, _, _ = env
    d, t = create(c, package(extra={"BinData/image.png": b"123"}))
    assert save(c, d, t, b"fake hwpx").status_code == 422
    assert save(c, d, t, package(extra={"../escape": "x"})).status_code == 422
    assert save(c, d, t).status_code == 422
    assert c.get("/api/documents").json()[0]["current_version"] == d["current_version"]
    bomb = package(extra={"big": b"0" * (31 * 1024 * 1024)})
    assert save(c, d, t, bomb).status_code == 413


def test_same_names_separate_and_cross_origin_rejected(env):
    c, _, _, _ = env
    d, t = create(c)
    other, _ = create(c)
    assert d["id"] != other["id"]
    assert (
        c.patch(
            f"/api/documents/{d['id']}",
            json={"name": "new"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        c.get(
            f"/api/documents/{d['id']}/versions/{other['current_version']}/file"
        ).status_code
        == 404
    )


def test_ai_unconfigured_does_not_block_save(env, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    c, _, _, _ = env
    d, t = create(c)
    assert c.get("/api/ai/models").status_code == 503
    assert save(c, d, t).status_code == 200
