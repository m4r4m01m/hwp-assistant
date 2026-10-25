"""Synthetic AI choices through the real conversation API; never applies a design."""
import copy
import json
import uuid

import pytest
from app import document_assistant
from app.db import ConversationTurn, transaction
from app.hwpx import digest
from test_document_assistant import assistant, create
from test_collaboration import secured, share, as_user


TABLE = {"kind": "design", "id": "reference-table", "sha256": "b" * 64,
         "name": "기준서 표", "design_kind": "table", "settings": {"table_color": "#000000"}}
STANDARD = {"kind": "standard", "id": "synthetic-standard", "sha256": "c" * 64,
            "name": "합성 표준", "design_kind": None, "settings": {}}


@pytest.fixture
def designs(assistant, monkeypatch):
    rows = [copy.deepcopy(TABLE), copy.deepcopy(STANDARD)]
    owners = []
    def catalog(db, user, context):
        owners.append(user)
        return copy.deepcopy(rows)
    monkeypatch.setattr(document_assistant, "design_catalog", catalog)
    body = copy.deepcopy(assistant[5])
    body["selection"] = None
    body["message"] = "선택한 표에 어울리는 디자인을 추천해줘"
    body["design_context"] = {"source_sha256": "a" * 64,
        "target": {"kind": "table", "section": 0, "paragraph": 2, "control": 0}}
    return assistant, body, rows, owners


def complete(fixture, body, suggestions):
    document_assistant.finish_turn(fixture[1].state.engine, body["id"], {
        "answer": "현재 선택한 표에 어울리는 디자인입니다. 미리보기를 확인하세요.",
        "rewrite_instruction": None, "design_suggestions": suggestions,
    })
    return fixture[0].get(f"/api/assistant-turns/{body['id']}").json()


def choice(item=TABLE):
    return {key: item[key] for key in ("kind", "id", "sha256")} | {"reason": "표 머리글을 구분해 읽기 쉽게 표시합니다."}


def test_design_proposal_freezes_source_target_owner_catalog_without_edit_or_extra_ai(designs):
    fixture, body, _, owners = designs
    before = {p.name: p.read_bytes() for p in fixture[7].iterdir()}
    first = create(fixture, body)
    assert first.status_code == 200, first.text
    assert create(fixture, body).json() == first.json()
    result = complete(fixture, body, [choice()])
    assert result["status"] == "ready", result
    assert result["design_context"] == body["design_context"]
    assert result["document_context"]["revision"] == 3 and result["document_context"]["epoch"] == 1
    assert result["base_version"] == body["base_version"]
    assert result["design_proposals"] == [{**choice(), "name": TABLE["name"], "design_kind": "table", "target": body["design_context"]["target"]}]
    assert len(fixture[6]) == 1 and owners == ["alice", "alice"]
    assert create(fixture, body).json() == result and len(fixture[6]) == 1
    with transaction(fixture[1].state.engine) as db:
        row = db.get(ConversationTurn, body["id"])
        assert json.loads(row.input)["design_catalog"] == [TABLE, STANDARD]
        assert row.input_sha256 == digest(row.input.encode())
    assert {p.name: p.read_bytes() for p in fixture[7].iterdir()} == before
    assert len(fixture[0].get(f"/api/documents/{fixture[2]['id']}/versions").json()) == 1
    share(fixture[0], fixture[2], "reader")
    as_user(fixture[0], "bob")
    assert fixture[0].get(f"/api/assistant-turns/{body['id']}").json()["design_proposals"] == result["design_proposals"]
    assert create(fixture, {**body, "id": str(uuid.uuid4())}).status_code == 403


@pytest.mark.parametrize("mutation", ["unknown-id", "wrong-kind", "wrong-hash", "changed-hash", "removed", "duplicate", "too-many", "arbitrary-settings", "no-context"])
def test_untrusted_or_stale_design_choices_cannot_publish(designs, mutation):
    fixture, body, rows, _ = designs
    suggestions = [choice()]
    if mutation == "no-context": body.pop("design_context")
    assert create(fixture, body).status_code == 200
    if mutation == "unknown-id": suggestions[0]["id"] = "not-owned"
    if mutation == "wrong-kind": suggestions[0]["kind"] = "standard"
    if mutation == "wrong-hash": suggestions[0]["sha256"] = "f" * 64
    if mutation == "changed-hash": rows[0]["sha256"] = "d" * 64
    if mutation == "removed": rows.clear()
    if mutation == "duplicate": suggestions.append(choice())
    if mutation == "too-many": suggestions *= 4
    if mutation == "arbitrary-settings": suggestions[0]["settings"] = {"xml": "<untrusted/>"}
    result = complete(fixture, body, suggestions)
    assert result["status"] == "failed" and result["design_proposals"] == []


def test_whole_document_standard_has_explicit_document_target(designs):
    fixture, body, _, _ = designs
    body["design_context"]["target"] = None
    assert create(fixture, body).status_code == 200
    result = complete(fixture, body, [choice(STANDARD)])
    assert result["status"] == "ready"
    assert result["design_proposals"][0]["target"] == {"kind": "document", "section": 0}


@pytest.mark.parametrize("mutation", ["client-catalog", "hash", "section-bool", "table-without-control", "paragraph-with-control"])
def test_invalid_design_context_is_rejected_before_inference(designs, mutation):
    fixture, body, _, _ = designs
    if mutation == "client-catalog": body["design_catalog"] = [TABLE]
    if mutation == "hash": body["design_context"]["source_sha256"] = "bad"
    if mutation == "section-bool": body["design_context"]["target"]["section"] = False
    if mutation == "table-without-control": body["design_context"]["target"].pop("control")
    if mutation == "paragraph-with-control": body["design_context"]["target"]["kind"] = "paragraph"
    assert create(fixture, body).status_code == 422
    assert fixture[6] == []


def test_legacy_question_fingerprint_and_old_model_output_remain_valid(assistant):
    body = assistant[5]
    value = document_assistant.TurnInput.model_validate(body)
    assert document_assistant.request_payload(value) == body
    response = document_assistant.parse_response('{"answer":"기존 답변","rewrite_instruction":null}')
    assert response.design_suggestions == []
    assert create(assistant).status_code == 200
    result = complete(assistant, body, [])
    assert result["status"] == "ready" and result["design_context"] is None


@pytest.mark.parametrize("frame", [True, False])
def test_title_design_requires_a_recognized_title_frame_for_a_table_target(designs, frame):
    fixture, body, rows, _ = designs
    title = {**TABLE, "id": "reference-title", "design_kind": "title", "name": "제목 상하선"}
    rows.append(title)
    body["design_context"].update(target_text="합성 사업 추진 계획", target_text_truncated=False, target_is_title_frame=frame)
    assert create(fixture, body).status_code == 200
    result = complete(fixture, body, [choice(title)])
    assert result["status"] == ("ready" if frame else "failed")
    if frame:
        assert result["design_proposals"][0]["target"] == body["design_context"]["target"]
        assert result["design_context"]["target_text"] == "합성 사업 추진 계획"
        with transaction(fixture[1].state.engine) as db:
            payload = json.loads(db.get(ConversationTurn, body["id"]).input)
            assert payload["design_context"] == body["design_context"]


@pytest.mark.parametrize("change", ["too-long", "no-target", "frame-with-paragraph", "non-boolean"])
def test_selected_target_text_is_bounded_and_tied_to_a_supported_target(designs, change):
    fixture, body, _, _ = designs
    context = body["design_context"]
    context.update(target_text="선택한 표의 내용", target_text_truncated=False, target_is_title_frame=False)
    if change == "too-long": context["target_text"] = "가" * 8001
    if change == "no-target": context["target"] = None
    if change == "frame-with-paragraph":
        context["target"] = {"kind": "paragraph", "section": 0, "paragraph": 1}
        context["target_is_title_frame"] = True
    if change == "non-boolean": context["target_text_truncated"] = 1
    assert create(fixture, body).status_code == 422
    assert not fixture[6]


def test_catalog_prompt_metadata_is_server_owned_target_filtered_and_bounded(assistant, monkeypatch):
    from app import editor_designs
    title = {**TABLE, "id": "reference-title", "kind": "title"}
    table = {**TABLE, "kind": "table", "settings": {"table_color": "#000000", "private_extra": "excluded"}}
    observed = []
    def catalog(db, user):
        observed.append(user)
        return {"designs": [table, title], "standards": [STANDARD]}
    monkeypatch.setattr(editor_designs, "editor_design_catalog", catalog)
    target = {"kind": "table", "section": 0, "paragraph": 0, "control": 0}
    rows = document_assistant.design_catalog(None, "alice", {"target": target})
    assert [row["id"] for row in rows] == [TABLE["id"], STANDARD["id"]]
    assert rows[0]["settings"] == {"table_color": "#000000"}
    assert observed == ["alice"]
    rows = document_assistant.design_catalog(None, "alice", {"target": target, "target_is_title_frame": True})
    assert [row["id"] for row in rows] == ["reference-title", STANDARD["id"]]


def test_data_table_design_cannot_replace_a_recognized_title_frame(designs):
    fixture, body, _, _ = designs
    body["design_context"].update(target_text="합성 제목", target_is_title_frame=True)
    assert create(fixture, body).status_code == 200
    result = complete(fixture, body, [choice(TABLE)])
    assert result["status"] == "failed"
    assert not result["design_proposals"]
