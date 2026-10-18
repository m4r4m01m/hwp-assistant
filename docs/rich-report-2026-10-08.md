# 표·흐름도·본문 강조가 있는 보고서 초안

2026-10-08. 기존 미커밋 구현 위에 추가했으며 commit/push/merge는 하지 않았다.

## 새 초안의 동작

- Codex가 비교·담당·조치·일정은 표, 실제 순서가 있는 절차는 흐름도로 선택한다. 해당 내용을 줄글로 반복하지 않고 짧은 해석을 붙이도록 생성 지시를 변경했다. 모든 보고서에 불필요한 도식을 강제하지 않는다.
- 문단 맨 앞 `(요약)`, `□ (조치)`, `ㅇ (근거)` 등의 괄호 표지를 자동으로 굵게 표시한다. 모델은 실제 본문에 있는 짧은 문구를 `bold`, `blue`, `bold_blue`로 지정할 수 있다. 본문에 없는 강조 문구와 중복 항목은 거부한다.
- 실시간 수신 화면, 문서 입력 중 화면, 생성 원문 보기, 최종 HWPX에서 같은 표·흐름도·강조를 표현한다. 부분 JSON은 표시 용도이며 완료 검증 전에는 HWPX에 적용하지 않는다.
- 흐름도는 음영 카드와 화살표를 갖는 **편집 가능한 한글 개체**로 만든다. 내부 표현은 셀 구조이며 별도 이미지 생성 모델이나 사진 검색을 호출하지 않는다. 표의 셀도 이미지로 변환하지 않는다.
- 현재 크기 제한: 보고서당 표·흐름도 각각 최대 2개, 절당 각각 1개. 표 2–4열/데이터 1–5행, 셀 52자/머리글 20자 이내. 흐름도 2–4단계, 단계 48자 이내. 실제 값·출처를 축약하거나 새 통계를 꾸며서 맞추지 않는다.

## 경계와 호환

`report_contract.py`의 선택 필드 기본값은 과거 초안을 읽기 위한 것이다. 새 Codex structured output에는 모든 필드가 명시되도록 별도 `generation_schema()`를 보낸다. 초기 실제 연결에서 `emphasis`가 required에 없다는 스키마 거부가 재현되었고 이 분리로 해결했다. 계약 ID는 `korean-report-v4-visual-emphasis`다.

모든 표 셀·도식 문구·화살표·캡션은 개별 스테이징 문단으로 만들어 기존 rhwp exact command와 문서/위치/원문 fence, 영수증, 페이지 수 보호를 통과한다. `verify_output` 뒤에만 `report_rich.py`가 서식을 적용하고 셀 구조로 옮긴다. 최종 읽기 순서에서 검증된 문단이 한 번씩 정확히 남는지 확인한다. 제목 파란 위·아래선, 본문 계층과 들여쓰기, 기준 파일은 유지했다. 선택된 강조 문구는 새 네모 항목으로 나눌 때 가능하면 끊지 않는다.

새 미리보기에는 `presentation_policy=tables-flow-emphasis-v1`과 셀 위치·강조 입력을 고정한다. 과거 고정 미리보기는 이전 `report_layout.py` 경로를 유지한다. 재시도는 기존 미리보기와 출력/영수증/저장 키를 재사용한다. 별도 미리보기에는 직접 편집할 수 있지만 **초안 확정·서버 저장** 전에는 문서나 버전을 생성하지 않는다. 기존 원본/과거 blob/문서 편집 버퍼를 대상으로 적용하지 않는다.

## 검증과 증거 구분

최종 소스 manifest: `docs/evidence/rich-report-manifest-final-2026-10-08.json`.
HEAD `1e0538781e02ac85d095bb3c72bf844943c305b3`는 그대로이며 실행 소스는 미커밋·미추적 파일을 포함한다.
소스 집합 SHA-256: `e13845ea8dbee2850b5adf46e75a91ed6d6d104da9dcf8a993b56fc49017117d`.
129개 소스/lock/시험 관련 파일과 83개 엔진 파일을 기록했다.

| 실행 | 결과/근거 |
|---|---|
| `.venv/Scripts/python -m pytest -q --basetemp=.cache/rich-report-2026-10-08/pytest-verified` | 266 passed, 0 failed, 0 skipped, exit 0. 로그 `pytest-verified.log` |
| `node frontend/node_modules/typescript/bin/tsc --noEmit --project frontend/tsconfig.json` | 통과 |
| `node --test scripts/engine-build.test.mjs` | 11 passed |
| `cd frontend; npm run build` | 기존 pinned core/bundle provenance 가드로 차단. 가드를 완화하지 않음 |
| `scripts/test-isolated.ps1 -EnginePublic .cache/engine-release-2026-09-27/public -OutDir .cache/rich-report-source-verified-2026-10-08` | 일반/인증 source E2E. 45 collected, 35 passed, 0 failed, 10 skipped(장시간 1개/보고서 환경 9개) |
| 같은 스크립트 `-BuiltApp -ReportMock -TestPattern 'MOCK report'`, OutDir `.cache/rich-report-built-verified-2026-10-08` | 현재 소스로 새로 빌드. 5 passed, 0 failed/skipped |
| 같은 스크립트 `-BuiltApp -ReportMock -RecordedReports .cache/rich-report-2026-10-08/recorded -TestPattern 'RECORDED rich report'` | 1 passed. 실제 Codex 결과를 실제 rhwp에 입력해 2쪽 렌더/내보내기, 손실 0, 전체 문구 순서 보존. 브라우저에서 새 모델 호출 0 |

새 `test_report_rich.py` 13개는 셀/출처/강조 계약, 진행 중 부분 JSON, 선택 문구 분할, 옛 초안 호환, 전체 문구 순서, 저장 전 무출판 및 고정 재시도를 확인한다. 새 실제 엔진 E2E는 생성 완료 전 표/도식 표시, 실제 강조 스타일, 수동 편집, 저장 응답 유실 시 동일 출력 재시도, 버전 1개, 재열기를 확인한다.

실제 Codex 실행 시도는 3회였다. 앞의 2회는 응답 스키마를 거부하여 생성이 완료되지 않았고, 수정 후 1회가 표 1개·흐름도 1개·강조를 포함해 완료되었다(27.835초, 웹 검색 없음, 합성 훈련 계획). 실제 생성은 최종 본문 분할/CSS/시험 보완 전이므로 그 실행을 최종 변경셋의 전체 회귀로 세지 않는다. 생성 지시·스키마는 최종과 같고, 기록된 결과를 최종 제품 소스에서 한글 렌더·내보내기 시험에 사용했다. 사람의 실제 승인 및 한컴 실행 시험은 수행하지 않았다.

중간 새 번들 시험은 새 시험이 먼저 저장한 문서 때문에 기존 시험의 “전체 문서 0개” 가정에서 1건 실패했다. 제품 코드는 변경하지 않고 시작 시 문서 수와 비교하도록 시험을 수정했다. 수정 후 5개를 한 번에 다시 실행한 결과를 위에 기록했다. 앞선 결과를 합산하지 않았다.

최종 pytest 시작 시 CPython 3.14 `platform._wmi_query`의 Windows access violation 진단이 출력되었으나 이후 266개가 완료되고 프로세스 exit 0이었다. 로그를 보존했다. 이전 동일 제품 소스 실행도 266개 통과했으며 환경 진단의 원인은 이번 기능 수정 범위에서 확인하지 않았다. 602초/1시간 시험은 이번 회귀로 재실행하지 않았다.

## 보존 및 사용

기존 보호 blob 68개의 SHA-256 일치, 기존 사용자 보고서 요청 6개의 상태/초안 해시/승인 기록 유지, 기준 파일 및 엔진 해시 유지 확인. 사용자 브라우저를 조작하거나 새로고침하지 않았다. 별도 시험 DB/문서 저장소와 자동화 브라우저만 사용했다. 기본 AI Codex와 `context_used=false`를 유지했다.

현재 사용자 서버는 `http://127.0.0.1:15180/`, 실행 디렉터리는 `.cache/user-review/runs/20261008-102734-fbb76854`. 새 JS `index-Bdqfcaaa.js`, CSS `index-vLGOXahb.css`를 제공하며 실제 제공 파일 10개 해시를 검증했다. 페이지를 새로고침하고 **새 초안**을 요청하면 적용된다. 기존 고정 초안을 자동으로 재작성하지 않는다.

합성 예시: `reports/2026-10-08-rich-draft-example/actual-rich-preview.hwpx`, `actual-page-1.png`, `actual-page-2.png`. 이 파일은 미리보기 내보내기이며 사용자 서버 저장이나 실제 금융 사고 보고서가 아니다. 전체 자동 목차, 임의 이미지 생성, 일반 차트 편집기는 이번 범위에 포함하지 않았다.
