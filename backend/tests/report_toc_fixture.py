"""Bounded synthetic TM-10 report; pagination is asserted by the native E2E.

No forced page counts or fabricated layout evidence are part of this fixture.
Long headings and the full content pass through the ordinary ReportDraft,
fenced application, rich formatting and generation-time TOC pipeline.
"""

_TOPICS = [
    "핵심 업무와 데이터 보호 우선순위", "외부 위탁과 접근 권한 관리", "고객 안내와 내부 의사소통",
    "단계별 사고 대응과 복구 준비", "부서 간 책임과 증거 보존", "후속 점검과 개선 과제 관리",
]


def _paragraphs(topic):
    result = []
    for phase in ("준비", "실행", "확인"):
        result.extend([
            f"□ ({phase} 원칙) 가상 업무의 {topic}에 관한 담당자와 검토 범위를 먼저 정하고, 합의한 판단 기준을 기록하여 다음 점검에서도 같은 조건으로 비교할 것을 제안함. 확인되지 않은 사고 규모나 예산을 사실로 추가하지 않으며 합성 훈련의 가정과 실제 운영 정보를 구분할 필요가 있음",
            f"ㅇ {phase} 단계의 담당 부서는 업무별 의존 관계와 대체 절차를 함께 검토하고, 요청한 자료의 작성 시점과 승인 여부를 확인하여 검토 근거를 남길 필요가 있음. 후속 담당자가 같은 자료를 다시 확인할 수 있도록 보관 위치와 변경 사유를 기록하고 미확인 항목은 별도로 표시할 것을 제안함",
            f"- {phase} 결과를 정리할 때에는 조치 담당자, 확인 방법, 재점검 조건을 구분해 기록하고 다음 단계의 승인자가 검토할 자료를 연결할 필요가 있음. 합성 자료의 문장을 실제 조사 결과로 해석하지 않도록 각 항목의 가정과 검증 필요 사항을 유지하며 원문에 없는 수치를 추가하지 않을 것을 제안함",
        ])
    return result


LONG_TOC_DRAFT = {
    "title": "가상 금융회사 합성 훈련의 긴 한글 제목과 업무 연속성 및 정보 보호 개선 과제를 함께 검토하는 경영 보고서",
    "summary": "□ (요약) 핵심 서비스 보호와 단계별 검증을 우선하는 합성 훈련 보고서이며, 담당 부서가 판단 기준과 증거를 함께 확인할 것을 제안함. 본문의 모든 내용은 시험용 제안이며 실제 사고 통계나 조사 결과를 의미하지 않음",
    "sections": [
        {"heading": f"{topic}에 관한 가상 금융회사 합성 훈련의 검토 기준과 부서 간 협의 절차 및 단계별 실행 조건과 확인 자료를 빠짐없이 보존하기 위한 상세 추진 방향",
         "paragraphs": _paragraphs(topic)}
        for topic in _TOPICS
    ],
    "sources": [],
    "limitations": ["모든 내용은 공개 검색과 실제 조직 자료를 사용하지 않는 합성 시험용 제안이며 실제 사고·통계·법적 의무를 의미하지 않음"],
    "emphasis": [
        {"text": "핵심 서비스 보호", "style": "bold"},
        {"text": "단계별 검증", "style": "blue"},
        {"text": "접근 권한 점검", "style": "bold_blue"},
    ],
}
LONG_TOC_DRAFT["sections"][0]["tables"] = [{
    "caption": "검토 담당자와 확인 자료를 연결하는 합성 점검표",
    "columns": ["담당", "검토 대상", "확인 방법", "후속 조건"],
    "rows": [["보안팀", "접근 권한 점검", "승인 기록 대조", "불일치 재검토"],
             ["업무팀", "업무 복구 절차", "합성 훈련 수행", "결과 공동 확인"]],
}]
LONG_TOC_DRAFT["sections"][1]["diagrams"] = [{
    "caption": "검토 요청부터 결과 확인까지의 편집 가능한 합성 흐름",
    "steps": ["검토 요청 접수", "근거와 가정 확인", "담당 부서 협의", "결과 기록과 승인"],
}]
LONG_TOC_DRAFT["sections"][3]["tables"] = [{
    "caption": "복구 준비와 검증 조건의 합성 관리표",
    "columns": ["단계", "준비 자료", "검증 조건"],
    "rows": [["준비", "의존 관계 목록", "범위 공동 확인"], ["복구", "대체 절차 기록", "복구 및 검증"]],
}]
LONG_TOC_DRAFT["sections"][4]["diagrams"] = [{
    "caption": "후속 개선 과제를 다음 점검에 연결하는 합성 흐름",
    "steps": ["개선 과제 기록", "담당자와 조건 합의", "이행 자료 검토", "다음 점검에 반영"],
}]
