import io
import json
import uuid
import zipfile
from xml.sax.saxutils import escape

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from app.db import VersionAuthor, Version, transaction
from app.main import create_app
from app.compare_hwpx import snapshot, align
from test_collaboration import secured, as_user, share, TOKENS


def hwpx(paragraphs, *, bold=False, style_id="0", table=None):
    runs = lambda text: f'<hp:run charPrIDRef="{style_id}"><hp:t>{escape(text)}</hp:t></hp:run>'
    ps = ''.join(f'<hp:p id="{i}" paraPrIDRef="0" styleIDRef="0">{runs(text)}</hp:p>' for i, text in enumerate(paragraphs))
    if table is not None:
        cells = ''.join(f'<hp:tc><hp:cellAddr rowAddr="0" colAddr="{i}"/><hp:subList><hp:p paraPrIDRef="0" styleIDRef="0">{runs(text)}</hp:p></hp:subList></hp:tc>' for i, text in enumerate(table))
        ps += f'<hp:p paraPrIDRef="0"><hp:run charPrIDRef="{style_id}"><hp:tbl rowCnt="1" colCnt="{len(table)}"><hp:tr>{cells}</hp:tr></hp:tbl></hp:run></hp:p>'
    files = {"mimetype": "application/hwp+zip", "Contents/content.hpf": '<package/>',
             "Contents/header.xml": f'<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"><hh:refList><hh:charProperties><hh:charPr id="{style_id}" height="1000">{"<hh:bold/>" if bold else ""}</hh:charPr></hh:charProperties><hh:paraProperties><hh:paraPr id="0"/></hh:paraProperties><hh:styles><hh:style id="0" name="바탕글" paraPrIDRef="0" charPrIDRef="{style_id}"/></hh:styles></hh:refList></hh:head>',
             "Contents/section0.xml": f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">{ps}</hs:sec>'}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return output.getvalue()


def upload(c, data):
    response = c.post('/api/documents', params={"name": "비교.hwpx", "document_id": str(uuid.uuid4())},
                      headers={"Idempotency-Key": str(uuid.uuid4())}, content=data)
    assert response.status_code == 200, response.text
    return response.json()


def save_version(c, doc, data, *, cause="web-edit", key=None):
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    response = c.post(f"/api/documents/{doc['id']}/versions", params={"base_version": doc["current_version"], "cause": cause},
                      headers={"X-Edit-Session": token, "Idempotency-Key": key or str(uuid.uuid4())}, content=data)
    assert response.status_code == 200, response.text
    c.delete(f"/api/documents/{doc['id']}/session", headers={"X-Edit-Session": token})
    doc["current_version"] = response.json()["id"]
    return response.json()


def compare(c, doc, left, right):
    response = c.get(f"/api/documents/{doc['id']}/compare", params={"left": left, "right": right})
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def test_skipped_versions_attribute_each_paragraph_to_its_saver(secured):
    c, app, url, folder = secured
    doc = upload(c, hwpx(["예산은 100만원", "담당 부서는 기획부", "유지 문단"]))
    first = doc["current_version"]
    share(c, doc)
    as_user(c, "bob")
    middle = save_version(c, doc, hwpx(["예산은 150만원", "담당 부서는 기획부", "유지 문단"]))
    as_user(c, "alice")
    last = save_version(c, doc, hwpx(["예산은 150만원", "담당 부서는 운영부", "유지 문단"]))
    result = compare(c, doc, first, last["id"])
    assert len(result["changes"]) == 2
    budget, department = result["changes"]
    assert budget["right"]["last_change"]["author"]["user_id"] == "bob"
    assert [e["version_id"] for e in budget["history"]] == [middle["id"]]
    assert department["right"]["last_change"]["author"]["user_id"] == "alice"
    assert [e["version_id"] for e in department["history"]] == [last["id"]]
    reverse = compare(c, doc, last["id"], first)
    assert reverse["reverse"]
    assert reverse["changes"][0]["left"]["last_change"]["author"]["user_id"] == "bob"
    with TestClient(create_app(url, folder), headers={"Authorization": "Bearer " + TOKENS["alice"]}) as restarted:
        assert compare(restarted, doc, first, last["id"]) == result
    with transaction(app.state.engine) as db:
        assert len(list(db.scalars(select(VersionAuthor)))) == 3


def test_author_unknown_for_old_versions_and_identity_cannot_be_forged(secured):
    c, app, _, _ = secured
    doc = upload(c, hwpx(["기존 내용"]))
    first = doc["current_version"]
    with transaction(app.state.engine) as db:
        db.delete(db.get(VersionAuthor, first))
    c.headers["X-User-ID"] = "bob"
    last = save_version(c, doc, hwpx(["수정 내용"]))
    assert last["author"] == {"user_id": "alice", "identity_kind": "authenticated"}
    result = compare(c, doc, first, last["id"])
    assert result["left"]["author"] is None
    assert result["changes"][0]["left"]["last_change"]["author"] is None


def test_local_attribution_does_not_claim_a_person(tmp_path, monkeypatch):
    monkeypatch.delenv("HWP_AUTH_TOKENS", raising=False)
    with TestClient(create_app(f"sqlite:///{tmp_path}/local.db", tmp_path / "files")) as c:
        doc = upload(c, hwpx(["처음"]))
        versions = c.get(f"/api/documents/{doc['id']}/versions").json()
        assert versions[0]["author"] == {"user_id": "local", "identity_kind": "local"}


def test_reader_permissions_cross_document_and_revocation(secured, monkeypatch):
    c, app, _, _ = secured
    doc = upload(c, hwpx(["처음"]))
    first = doc["current_version"]
    second = save_version(c, doc, hwpx(["나중"]))["id"]
    other = upload(c, hwpx(["비공개"]))
    share(c, doc, "reader"); as_user(c, "bob")
    assert len(compare(c, doc, first, second)["changes"]) == 1
    assert c.get(f"/api/documents/{doc['id']}/compare", params={"left": first, "right": other["current_version"]}).status_code == 404
    as_user(c, "eve")
    assert c.get(f"/api/documents/{doc['id']}/compare", params={"left": first, "right": second}).status_code == 404
    as_user(c, "alice")
    from app import version_compare
    original = version_compare.snapshot
    def revoke(data):
        from app.db import Access
        with transaction(app.state.engine) as db:
            grant = db.get(Access, (doc["id"], "bob"))
            if grant: db.delete(grant)
        return original(data)
    monkeypatch.setattr(version_compare, "snapshot", revoke)
    as_user(c, "bob")
    assert c.get(f"/api/documents/{doc['id']}/compare", params={"left": first, "right": second}).status_code == 404


def test_format_only_and_style_id_renumbering(secured):
    c, _, _, _ = secured
    doc = upload(c, hwpx(["내용 그대로"]))
    first = doc["current_version"]
    renumber = save_version(c, doc, hwpx(["내용 그대로"], style_id="7"))
    assert compare(c, doc, first, renumber["id"])["changes"] == []
    formatted = save_version(c, doc, hwpx(["내용 그대로"], bold=True, style_id="7"))
    result = compare(c, doc, first, formatted["id"])
    assert len(result["changes"]) == 1
    assert result["changes"][0]["kind"] == "format"
    assert [e["version_id"] for e in result["changes"][0]["history"]] == [formatted["id"]]


def test_cell_edit_insertion_deletion_and_restore_keep_correct_history(secured):
    c, app, _, folder = secured
    original = hwpx(["첫 문단", "삭제할 문단"], table=["기획팀", "100만원"])
    doc = upload(c, original); first = doc["current_version"]
    share(c, doc); as_user(c, "bob")
    second = save_version(c, doc, hwpx(["삽입 문단", "첫 문단"], table=["기획팀", "150만원"]))
    result = compare(c, doc, first, second["id"])
    cell = next(d for d in result["changes"] if (d["right"] or d["left"])["kind"] == "cell")
    assert cell["right"]["text"] == "150만원"
    assert cell["right"]["anchor"]["cell_path"][0]["col"] == 1
    assert cell["right"]["last_change"]["author"]["user_id"] == "bob"
    as_user(c, "alice")
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    response = c.post(f"/api/documents/{doc['id']}/versions/{first}/restore", params={"base_version": second["id"]},
                      headers={"X-Edit-Session": token, "Idempotency-Key": "restore-compare"})
    assert response.status_code == 200, response.text
    restored = response.json()
    assert restored["author"]["user_id"] == "alice"
    assert compare(c, doc, first, restored["id"])["changes"] == []
    result = compare(c, doc, second["id"], restored["id"])
    assert all(d["history"][-1]["cause"] == "restore" for d in result["changes"])
    assert c.get(f"/api/documents/{doc['id']}/versions/{first}/file").content == original


def test_commit_failure_leaves_no_author_or_version(secured):
    c, app, _, _ = secured
    doc = upload(c, hwpx(["원본"]))
    token = c.post(f"/api/documents/{doc['id']}/session").json()["token"]
    def fail(_): raise RuntimeError("commit failed")
    event.listen(app.state.engine, "commit", fail)
    response = c.post(f"/api/documents/{doc['id']}/versions", params={"base_version": doc["current_version"], "cause": "web-edit"},
                      headers={"X-Edit-Session": token, "Idempotency-Key": "failed"}, content=hwpx(["수정"]))
    event.remove(app.state.engine, "commit", fail)
    assert response.status_code == 500
    with transaction(app.state.engine) as db:
        assert len(list(db.scalars(select(Version)))) == 1
        assert len(list(db.scalars(select(VersionAuthor)))) == 1


def test_history_limit_and_corrupt_bytes_are_not_misrepresented(secured, monkeypatch):
    c, app, _, folder = secured
    doc = upload(c, hwpx(["처음"])); first = doc["current_version"]
    middle = save_version(c, doc, hwpx(["중간"]))
    last = save_version(c, doc, hwpx(["마지막"]))
    monkeypatch.setattr("app.version_compare.MAX_HISTORY", 1)
    result = compare(c, doc, first, last["id"])
    assert not result["history_complete"] and result["warnings"]
    assert result["changes"][0]["right"]["last_change"] is None
    with transaction(app.state.engine) as db:
        key = db.get(Version, first).storage_key
    (folder / key).write_bytes(b"corruption injected by test")
    assert c.get(f"/api/documents/{doc['id']}/compare", params={"left": first, "right": last["id"]}).status_code == 409


def test_shifted_repeated_paragraphs_have_uncertain_lineage():
    left = snapshot(hwpx(["같은 문장", "같은 문장", "끝"]))["blocks"]
    right = snapshot(hwpx(["추가", "같은 문장", "같은 문장", "끝"]))["blocks"]
    pairs, _, _ = align(left, right)
    assert any(ambiguous for _, _, ambiguous in pairs)


def test_moved_paragraph_is_reported_but_insertion_does_not_reassign_savers(secured):
    c, _, _, _ = secured
    doc = upload(c, hwpx(["고유 첫 문단", "고유 둘째 문단", "고유 셋째 문단"]))
    first = doc["current_version"]
    share(c, doc); as_user(c, "bob")
    second = save_version(c, doc, hwpx(["고유 둘째 문단", "고유 첫 문단", "고유 셋째 문단"]))
    result = compare(c, doc, first, second["id"])
    assert len(result["changes"]) == 1 and result["changes"][0]["kind"] == "moved"
    assert result["changes"][0]["right"]["last_change"]["author"]["user_id"] == "bob"
    as_user(c, "alice")
    third = save_version(c, doc, hwpx(["앞에 새 문단", "고유 둘째 문단", "고유 첫 문단", "고유 셋째 문단"]))
    result = compare(c, doc, second["id"], third["id"])
    assert len(result["changes"]) == 1 and result["changes"][0]["kind"] == "added"


def test_control_wrapper_and_resource_content_are_compared():
    original = hwpx(["본문"])
    def with_control(text):
        result = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as z:
            for name in source.namelist():
                value = source.read(name)
                if name == "Contents/section0.xml":
                    value = value.replace(b'</hp:run>', f'<hp:ctrl><hp:header><hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList></hp:header></hp:ctrl></hp:run>'.encode(), 1)
                z.writestr(name, value)
        return snapshot(result.getvalue())["blocks"]
    a, b = with_control("이전 머리말"), with_control("이후 머리말")
    assert len(a) == len(b) == 2
    assert a[1]["kind"] == "object" and a[1]["format"] != b[1]["format"]


def test_empty_paragraph_format_and_ambiguous_duplicate_format():
    from app.compare_hwpx import change_kind
    before = snapshot(hwpx([""]))["blocks"]
    after = snapshot(hwpx([""], bold=True))["blocks"]
    assert change_kind(before[0], after[0]) == "format"
    before = snapshot(hwpx(["반복", "반복"]))["blocks"]
    after = snapshot(hwpx(["반복", "반복"], bold=True))["blocks"]
    assert all(uncertain for _, _, uncertain in align(before, after)[0])


def test_image_resource_bytes_and_reference_renumbering():
    def picture(ident, content):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(hwpx(["본문"]))) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
            for name in source.namelist():
                value = source.read(name)
                if name == "Contents/content.hpf":
                    value = f'<package><item id="{ident}" href="../BinData/picture.png"/></package>'.encode()
                elif name == "Contents/section0.xml":
                    value = value.replace(b'</hp:run>', f'<hp:pic><hp:img binaryItemIDRef="{ident}"/></hp:pic></hp:run>'.encode(), 1)
                z.writestr(name, value)
            z.writestr("BinData/picture.png", content)
        return next(b for b in snapshot(output.getvalue())["blocks"] if b["kind"] == "image")
    # Parser fixtures only: bytes are deliberately not sent to the renderer.
    first = picture("image1", b"synthetic first resource")
    assert first["format"] == picture("renumbered9", b"synthetic first resource")["format"]
    assert first["format"] != picture("image1", b"synthetic changed resource")["format"]


def test_malformed_cell_address_is_a_validation_error():
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(hwpx([], table=["셀"]))) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for name in source.namelist():
            value = source.read(name)
            if name == "Contents/section0.xml":
                value = value.replace(b'rowAddr="0"', b'rowAddr="invalid"')
            z.writestr(name, value)
    with pytest.raises(HTTPException) as failure:
        snapshot(output.getvalue())
    assert failure.value.status_code == 422
