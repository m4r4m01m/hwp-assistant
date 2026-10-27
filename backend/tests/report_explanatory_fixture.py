"""Synthetic source evidence stays in the draft; only explanations print."""
EXPLANATORY_DRAFT = {
    "title": "협력사 정보보호 점검 현황 — 합성 검수",
    "summary": "□ 3개 협력사 중 2개사 조치 완료, 1개사 영향 범위 조사 중[S1].",
    "sections": [
        {"heading": "개요", "paragraphs": [
            "□ 추가 확인[N1]은 기존 사고의 재점검 결과임[S1].",
            "ㅇ 영향 범위[N2]와 조치 완료[N3]를 구분해 관리함[S2]."]},
        {"heading": "기업별 현황", "paragraphs": ["□ 미확정 범위는 확정 피해 규모와 분리함."],
         "tables": [{"caption": "협력사별 점검 결과", "columns": ["기업", "확인 상태", "대상", "대응"], "rows": [
             ["가람사", "추가 확인[N1]", "30건[S1]", "차단 완료"],
             ["누리사", "조사 중", "규모 미확정[S2]", "범위 확인 중"],
             ["다온사", "재점검 완료", "추가 피해 없음[S1]", "감시 유지"]]}]},
        {"heading": "후속 조치", "paragraphs": ["□ (제안) 조사 중인 누리사의 영향 범위 확인 후 공유."],
         "diagrams": [{"caption": "확인 결과 관리 절차", "steps": ["대상 대조", "범위 확인", "결과 공유"]}]}
    ],
    "notes": [
        {"id": "N1", "text": "추가 확인은 신규 사고 발생을 뜻하지 않음[S1]."},
        {"id": "N2", "text": "영향 범위에는 정보 항목과 대상 기간을 포함함."},
        {"id": "N3", "text": "조치 완료는 차단 완료를 뜻하며 피해 조사 종료와 구별함."}
    ],
    "sources": [
        {"id": "S1", "title": "합성 뉴스 제목: 피해 추가 확인", "url": "https://example.invalid/update", "published": "2026. 10. 27."},
        {"id": "S2", "title": "합성 조사 원문", "url": "https://example.invalid/scope", "published": ""}
    ],
    "limitations": ["합성 작성자 검토 메모: 실제 운영 자료가 아님."],
    "lead_summaries": [], "emphasis": []
}
