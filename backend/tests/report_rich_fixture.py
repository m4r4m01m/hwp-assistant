"""Public synthetic content shared by contract and isolated real-engine tests."""
RICH_DRAFT = {
    "title": "보안 대응 계획 — 표·흐름도 합성 시험",
    "summary": "□ (요약) 핵심 서비스 보호를 우선하며 30일 내 담당자와 점검 기한을 확정할 것을 제안함",
    "emphasis": [{"text": "핵심 서비스 보호", "style": "bold"}, {"text": "30일 내", "style": "blue"},
                 {"text": "접근 권한 점검", "style": "bold_blue"}],
    "sections": [
        {"heading": "우선순위와 책임", "paragraphs": ["□ (조치) 우선순위별 실행 항목과 책임을 표로 관리할 필요"],
         "tables": [{"caption": "담당자별 대응 계획 (합성 제안)", "columns": ["우선순위", "담당", "조치", "기한"],
             "rows": [["상", "보안팀", "접근 권한 점검", "30일 내"], ["중", "업무팀", "복구 훈련 및 결과 확인", "60일 내"]]}]},
        {"heading": "대응 절차", "paragraphs": ["□ (절차) 확인된 사실에 따라 단계별 대응을 수행할 것을 제안함"],
         "diagrams": [{"caption": "사고 대응 흐름 (합성 제안)", "steps": ["이상 징후 접수", "영향 범위 확인", "보호 조치 실행", "복구 및 검증"]}]},
        {"heading": "확인 사항", "paragraphs": ["□ (검증) 훈련 결과와 개선 과제를 다음 점검에 반영할 필요"]}],
    "sources": [], "limitations": ["모든 내용은 합성 시험용 제안이며 실제 사고·통계·법적 의무를 의미하지 않음"]}
