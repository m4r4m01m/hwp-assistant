"""Isolated test entry point. Never import this module in the product server."""
from app import reports, codex_runner
from app.main import create_app
import json
import os
from pathlib import Path
import sys
import tempfile
from dataclasses import replace
from fastapi import HTTPException

STREAMS = {}

DRAFT = {"title": "금융권 해킹 대응 보고서 — 합성 시험", "summary": "□ (요약) 제품 흐름을 검증하는 합성 보고서이며 실제 사고 통계나 조사 결과가 아님",
    "sections": [
        {"heading": "경영진 결정 사항", "paragraphs": ["□ (조치) 중요 업무와 데이터의 보호 우선순위를 정할 필요", "ㅇ 업무 부서가 사고 대응 훈련과 복구 계획의 실행 가능성을 확인할 것을 제안함", "- 담당자와 완료 기한을 정하고 검증 결과를 기록할 필요"]},
        {"heading": "외부 업체 위험", "paragraphs": ["제안: 외부 업체의 접근 권한과 주요 서비스 의존 관계를 점검합니다."]},
        {"heading": "고객 보호", "paragraphs": ["제안: 확인된 사실과 조사 중인 사항을 구분하여 고객 안내를 준비합니다."]}],
    "sources": [], "limitations": ["모의 AI 결과입니다. 실제 모델·웹 검색·사람 승인 시험으로 계산하지 않습니다."]}


def generate(value, settings, progress=lambda value: None, active=lambda: True):
    from tests.report_rich_fixture import RICH_DRAFT
    from tests.report_toc_fixture import LONG_TOC_DRAFT
    from tests.report_notes_fixture import AUTO_NOTES_DRAFT
    draft = LONG_TOC_DRAFT if '[LONG-TOC]' in value['prompt'] else RICH_DRAFT if '[RICH]' in value['prompt'] else DRAFT
    if '[AUTO-NOTES]' in value['prompt']:
        draft = AUTO_NOTES_DRAFT
    if '[UNVERIFIED]' in value['prompt']:
        draft = json.loads(json.dumps(DRAFT))
        draft['title'] = '자료 확인 미완료 합성 보고서'
        draft['summary'] += ' [S1]'
        draft['sources'] = [{'id': 'S1', 'title': '합성 참고 자료',
            'url': 'https://example.invalid/synthetic-reference', 'published': '2026-01-01'}]
        draft['limitations'] = [(f'기존 확인 사항 {index + 1}: ' + '합성 검수 항목의 사실과 조건을 보존하고 확인이 필요함. ' * 20)[:400]
            for index in range(6)]
    if '[OUTLINE]' in value['prompt']:
        # Fixed synthetic model output for the real outline/API/engine path.
        # Do not derive headings from the request or silently repair a mismatch.
        draft = {'title': '합성 자료 점검 대응 보고', 'summary': '□ 확인된 점검 현황과 제안한 조치를 구분함.',
            'sections': [
                {'heading': '개요', 'paragraphs': ['□ 자료 24건 중 보완 6건의 확인이 필요함. [S1]',
                    'ㅇ 원본과 기존 승인 문서는 보존함. [S1]']},
                {'heading': '자료 현황', 'paragraphs': ['□ 완료 18건과 보완 6건의 합계는 24건임. [S1]'],
                 'tables': [{'caption': '점검 처리 현황 [S1]', 'columns': ['구분', '확인 결과'],
                    'rows': [['완료 [S1]', '18건 [S1]'], ['보완 [S1]', '6건 [S1]']]}]},
                {'heading': '대응', 'paragraphs': ['□ 보완 자료를 확인한 뒤 게시할 것을 제안함.']},
                {'heading': '향후 계획', 'paragraphs': ['□ 다음 점검에서 보완 상태와 게시 조건을 확인할 필요가 있음.']}],
            'sources': [{'id': 'S1', 'title': '합성 점검 근거', 'url': 'https://example.invalid/outline-evidence',
                'published': '2026-10-26'}],
            'limitations': ['실제 업무·모델 조사 결과가 아닌 합성 개요와 저장 경계 검수 자료임.'], 'emphasis': []}
    if value['prompt'].startswith('[STREAM]'):
        from app import report_stream
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix='hwp-synthetic-stream-') as directory:
            folder = Path(directory)
            (folder / 'draft.json').write_text(json.dumps(draft), encoding='utf-8')
            STREAMS[value['id']] = folder
            args = [sys.executable, str(Path(__file__).with_name('report_rpc_fixture.py')), directory, 'e2e']
            with patch.object(report_stream, 'stream_command', lambda *args_: args):
                return report_stream.generate_stream(value, replace(settings, model='test-model'), directory, 'synthetic', 'test', progress, active)
    if os.getenv("HWP_REPORT_FIXTURES"):
        preset = (value.get("visual_standard") or {}).get("settings", {}).get("preset_id")
        name = {"appendix-1": "executive", "appendix-2": "vendor", "appendix-3": "customer"}.get(preset)
        if name is None:
            name = "vendor" if "클라우드" in value["prompt"] else "customer" if "고객정보" in value["prompt"] else "executive"
        record = json.loads((Path(os.environ["HWP_REPORT_FIXTURES"]) / f"actual-{name}.json").read_text(encoding="utf-8"))
        return record["draft"], {**record["evidence"], "delivery": "recorded-result-replay", "actual_model_calls_this_run": 0}
    return draft, {"executor": "synthetic-report-fixture", "completed": True, "web_search_calls": 0, "actual_model_calls": 0}


def synthetic_settings():
    # This test entry point uses only the recorded/synthetic runner above.
    # Do not read a real Codex login or configuration to advertise capabilities.
    return codex_runner.CodexSettings(sys.executable, 'synthetic-test-model',
                                     'http://127.0.0.1:8000', ())


reports.capture_settings = synthetic_settings
codex_runner.capture_settings = synthetic_settings
reports.generate_report = generate

# Explicit real-engine E2E fixture only: keep the real assistant API, owner
# catalog, lease/base checks and finish_turn validation. Replace just inference;
# no Codex login, remote HTTP model, production configuration or AI ACL changes.
if os.getenv("HWP_E2E_ASSISTANT_DESIGNS") == "1":
    from app import assistant_runner
    from app.document_assistant import AssistantResponse
    os.environ["HWP_DOCUMENT_ASSISTANT_PROVIDER"] = "llm"
    os.environ["LLM_PROVIDER"] = "openai-compatible"
    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
    os.environ["LLM_MODEL"] = "synthetic-design-fixture"

    def synthetic_design_answer(value, provider, settings, active=lambda: True):
        if not active():
            raise RuntimeError("Synthetic assistant request is no longer current")
        item = next((choice for choice in value.get("design_catalog", [])
            if choice["kind"] == "design" and choice["id"] == "reference-table"), None)
        if item is None:
            raise RuntimeError("Synthetic design fixture requires a supported table target")
        choice = {key: item[key] for key in ("kind", "id", "sha256")}
        response = AssistantResponse(answer="현재 표의 머리글을 구분하는 합성 디자인 제안입니다.",
            rewrite_instruction=None, design_suggestions=[{**choice, "reason": "표 내용은 유지하고 머리글만 구분합니다."}])
        return response, {"executor": "synthetic-design-fixture", "completed": True,
            "actual_model_calls": 0, "frozen_catalog_choice": choice}

    assistant_runner.generate_answer = synthetic_design_answer

if os.getenv("HWP_E2E_DOCUMENT_REWRITE") == "1":
    from app import assistant_runner
    from app.document_assistant import AssistantResponse
    from tests.document_rewrite_fixture import REPLACEMENTS
    os.environ["HWP_DOCUMENT_ASSISTANT_PROVIDER"] = "llm"
    os.environ["LLM_PROVIDER"] = "openai-compatible"
    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
    os.environ["LLM_MODEL"] = "synthetic-document-rewrite-fixture"

    def synthetic_document_rewrite(value, provider, settings, active=lambda: True):
        if not active():
            raise RuntimeError("Synthetic assistant request is no longer current")
        context = value.get("rewrite_context") or {}
        changes = [dict(section=p["section"], paragraph=p["paragraph"], before_sha256=p["text_sha256"], replacement=REPLACEMENTS[p["text"]])
            for p in context.get("paragraphs", []) if p["text"] in REPLACEMENTS]
        return AssistantResponse(answer="합성 본문 두 문단을 간결하게 다듬은 수정안입니다. 제목·표·주석과 수치는 유지합니다.",
            rewrite_instruction=None, document_rewrites=changes), {"executor": "synthetic-document-rewrite-fixture", "completed": True, "actual_model_calls": 0}

    assistant_runner.generate_answer = synthetic_document_rewrite

if os.getenv("HWP_E2E_ASSISTANT_TABLE_LAYOUT") == "1":
    from app import assistant_runner
    from app.document_assistant import AssistantResponse
    from tests.assistant_table_fixture import REPLACEMENTS
    os.environ["HWP_DOCUMENT_ASSISTANT_PROVIDER"] = "llm"
    os.environ["LLM_PROVIDER"] = "openai-compatible"
    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
    os.environ["LLM_MODEL"] = "synthetic-table-layout-fixture"

    def synthetic_table_layout(value, provider, settings, active=lambda: True):
        context = value.get("rewrite_context") or {}
        changes = [] if "표만" in value["message"] else [dict(section=p["section"], paragraph=p["paragraph"],
            before_sha256=p["text_sha256"], replacement=REPLACEMENTS[p["text"]])
            for p in context.get("paragraphs", []) if p["text"] in REPLACEMENTS]
        return AssistantResponse(answer="합성 표 배치와 본문 수정안을 비교하세요.", rewrite_instruction=None,
            document_rewrites=changes, table_layout=dict(parent_half=True, caption_gap_mm=2.0)), {
                "executor": "synthetic-table-layout-fixture", "completed": True, "actual_model_calls": 0}
    assistant_runner.generate_answer = synthetic_table_layout

app = create_app()

if os.getenv("HWP_E2E_ASSISTANT_TABLE_LAYOUT") == "1":
    from fastapi.responses import Response
    from tests.assistant_table_fixture import document as layout_document

    @app.get("/api/test/assistant-table-fixture")
    def assistant_table_fixture():
        return Response(layout_document(), media_type="application/hwp+zip")

if os.getenv("HWP_E2E_DOCUMENT_REWRITE") == "1":
    from fastapi.responses import Response
    from tests.document_rewrite_fixture import document as synthetic_document

    @app.get("/api/test/document-rewrite-fixture")
    def document_rewrite_fixture():
        return Response(synthetic_document(), media_type="application/hwp+zip")


@app.post('/api/test/report-stream/{task_id}/{step}')
def release_stream(task_id: str, step: str):
    folder = STREAMS.get(task_id)
    if not folder or not folder.is_dir() or step not in ('release', 'finish'):
        raise HTTPException(404, 'Unknown synthetic stream barrier')
    (folder / step).touch()
    return {'released':step}
