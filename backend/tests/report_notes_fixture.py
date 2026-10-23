"""Synthetic multi-source report for actual-engine automatic-note verification."""
AUTO_NOTES_DRAFT = {
    "title": "자료 점검 계획 — 자동 주석 합성 검수", "summary": "□ 합성 자료의 점검 절차를 검수함.",
    "sections": [
        {"heading": "점검 현황", "paragraphs": [
            "□ 자료 24건을 확인함. [S8][S4] 동일 근거를 유지함. [S8]",
            "ㅇ 보완 6건은 담당 확인 후 게시함. [S4]"],
         "tables": [{"caption": "확인 대상 [S4]", "columns": ["항목", "수량"],
                     "rows": [["완료 [S8]", "18건"], ["보완 [S4]", "6건"]]}]},
        {"heading": "추진 조건", "paragraphs": ["□ 원본과 이전 버전을 보존함. [S8]"]},
        {"heading": "추진 일정", "paragraphs": ["□ 2026. 11. 2.~11. 13. 시범 운영 예정임."]}],
    "sources": [
        {"id": "S8", "title": "합성 점검 자료", "url": "https://example.com/check", "published": "2026. 10. 23."},
        {"id": "S4", "title": "합성 승인 조건", "url": "https://example.com/approval", "published": ""}],
    "limitations": ["실제 업무자료나 모델 답변이 아닌 합성 주석 배치 검수 자료임."], "emphasis": []}
