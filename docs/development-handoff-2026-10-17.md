# HWP Assistant 구현 마감·최종 검증 · 2026-10-17

TM-10의 세 보고서 프로필과 생성 단계 자동 목차를 실제 rhwp에서 검증했다. 기존 구현을 재사용하고, 저장 재확인의 버전 응답 및 조용한 Codex 스트림의 마지막 수신 내용 표시 결함을 수정했다. 이 문서는 오늘의 단일 인수인계 기록이며 이전 날짜의 문서·증거와 요구사항 기준선은 보존한다.

## 변경과 근거

- `backend/app/report_preview.py`: 최초 저장 이후 다른 편집기가 새 버전을 만든 경우에도 같은 저장 ID/출력의 재확인은 최초 `saved_version`을 반환한다. 현재 문서 head와 blob을 바꾸지 않으며 현재 owner 권한을 다시 검사하고 DB commit 뒤 응답한다. commit 실패 뒤 미승인 초안과 동일 ID 재시도도 시험했다.
- `backend/app/report_stream.py`, `report_rpc.py`: 200ms 갱신 제한으로 보류된 실제 delta를 같은 worker의 queue 대기 중 반영한다. 새 timer thread나 가짜 진행률을 추가하지 않고 기존 owner/status·취소·정리 경계를 유지한다. 실제 stdio child와 고정 시계로 수정 전 실패를 재현했다.
- `backend/tests/report_toc_fixture.py`, `report_mock_app.py`, `frontend/tests/report-toc.spec.ts`: 긴 한글 절 제목 6개, 표 2개, 편집 가능한 흐름도 2개와 강조를 계약 범위 안에 구성했다. 최초 편집 잠금, 삽입 전후 실제 쪽수, 최대 3회 수렴, 저장·다운로드·재열기를 확인한다.
- `test_report_templates.py`, `frontend/tests/report-template.spec.ts`: 세 프로필 각각의 실제 선택값·리비전·표준/profile SHA·입력 해시 고정과 동일 ID 접수 재확인을 검증한다. stale/변조 입력 거부와 프로필 없는 과거 입력의 호환성은 유지한다.
- `test_report_preview.py`, `test_report_stream.py`, `report-rich.spec.ts`, `reports.spec.ts`: 위 결함과 가상 캔버스·soft break·자동 목차 완료 대기 조건을 보강했다. 입력 축약이나 지원 한도 축소로 통과시키지 않았다.
- `scripts/test-isolated.ps1`: 진단 빌드의 기본 필터에도 `MOCK report`를 포함한다. `start-user-review.ps1`에는 별도 `-DataDirectory`를 제공해 기존 DB를 보존하면서 확인용 앱을 실행한다.

## 최종 소스와 엔진

브랜치는 `codex/hwp-assistant-progress-2026-10-14`, 시작 HEAD는 `493fffdd821d8953967f91aa236a8da4b6cac1cb`다. 시작 시 작업 트리는 깨끗했고 원격 개발 브랜치와 main은 요청문의 기준과 같았다. 이번 요청에는 원격 push의 새 명시적 승인이 없어 로컬 커밋까지만 수행한다. main 병합·운영 배포는 범위에 없다. 최종 커밋 HEAD와 작업 트리 상태는 최종 응답과 Git 이력에서 확인한다.

[최종 manifest](evidence/final-manifest-2026-10-17.json)는 소스·lock·시험 161개와 사용 엔진 82개 파일의 바이트 해시를 기록한다. 소스 집합 SHA는 `a19cdc9119e7f023e0c00745c2e9fba43f57d21dee6c6a1c6b4696d4d17e4d95`다. 검증 종료 후 같은 파일 집합을 다시 대조해 변경이 없음을 확인했다. 최종 실행별 결과와 제공 파일은 [실행 요약](evidence/final-verification-2026-10-17.json)에 연결한다.

사용 엔진은 기존 `.cache/toc-engine-final-2026-10-11/public/rhwp`, rhwp `0.8.6`, pinned commit `e8800c8def63449808a4092798442652ed460552`다. WASM SHA는 `0e81cdeb4a61de8dd12be6d79d3bf06a8c1153525883e4112f4dcb1257d68195`, bridge SHA는 `1c72afaab4cdeb936db935e87470bf0335b5c09f535b2f85db9c3d7579f9ce3b`다. 엔진·의존성을 재설치하거나 교체하지 않았다.

고정 기준 HWPX SHA `6c68b50656867f1fc724c50549e60e1109e2f7a00219f2928ef58de0a45711f1`와 profile SHA `7b2a0cb0fd540a83fea952abd891b8fc3e535ed3ac92a5affa6898313d7e4271`를 유지한다. 제목의 파란 위·아래선과 15pt 본문·계층 들여쓰기·괄호 표지/강조를 실제 엔진과 XML에서 확인했다.

## 실행 결과

각 행은 독립 실행이다. 이전 날짜와 대상 시험 수를 최종 전체 결과에 더하지 않는다.

| 명령/범위 | 결과 | 로그 |
|---|---|---|
| `.venv/Scripts/python -m pytest -q --basetemp=.cache/final-backend-2026-10-17-full-r2-tmp` | 425 passed / 0 failed / 0 skipped, 483.46초, exit 0. Native 오류 없음, 기존 Starlette 경고 1개 | `.cache/final-backend-2026-10-17-full-r2.log` |
| `node frontend/node_modules/typescript/bin/tsc --noEmit --project frontend/tsconfig.json` | exit 0 | `.cache/final-2026-10-17/typescript-final.log` |
| `node --test scripts/engine-build.test.mjs scripts/rewrite-request.test.mjs scripts/context-save.test.mjs scripts/text-diff.test.mjs` | 37 passed / 0 failed / 0 skipped, exit 0 | `.cache/final-2026-10-17/node-final.log` |
| `npm --prefix frontend run build` | exit 1, 기본 `frontend/public/rhwp/build-info.json`의 `coreBuild`·`bundle` 누락으로 `Missing pinned core/bundle provenance`. 가드 유지 | `.cache/final-2026-10-17/normal-build.log` |
| `test-isolated.ps1 -EnginePublic .cache/toc-engine-final-2026-10-11/public -ReportMock -OutDir .cache/final-source-r3-2026-10-17` | 61 passed / 0 failed / 5 skipped, 8.2분, exit 0, 잔여 리스너 0. Native 오류 없음 | 해당 경로의 `run.json`, `e2e.log` |
| `test-isolated.ps1 -EnginePublic .cache/toc-engine-final-2026-10-11/public -ReportMock -BuiltApp -TestPattern 'MOCK report\|MOCK HTTP local provider\|MOCK provider\|MOCK delayed provider' -OutDir .cache/final-built-r2-2026-10-17` | 37 passed / 0 failed / 1 skipped, 9.6분, exit 0, 잔여 리스너 0 | 해당 경로의 `run.json`, `e2e.log` |
| `test-isolated.ps1 ... -ReportMock -TestPattern 'MOCK provider: cancel resolves a lost request' -OutDir .cache/final-native-case-2026-10-17` | 1 passed / 0 failed / 0 skipped, 6.6초, exit 0, 잔여 리스너 0 | 해당 경로의 `run.json`, `e2e.log` |

일반 배포 빌드 성공으로 표시하지 않는다. 별도 검증 엔진은 `diagnostic=true`, `wasm_opt=false`이며 진단 번들에서 TypeScript·Vite bundle·engine 검사와 실제 HTTP 제공 파일 SHA 대조를 마쳤다. 최종 수집은 source 66개, built 38개이며 세 프로필·자동 목차·긴 제목/20쪽·stream·rich 시험이 포함된다.

예비 source 전체 실행은 59 passed / 2 failed / 5 skipped였다. 실제 스트림 결함과 자동 목차 완료 전 5초 버튼 기대값에서 실패했고 위 수정 뒤 새 서버에서 최종 회귀를 마쳤다. 이전 built 실행은 결함 수정 전 API를 사용해 소유 runner만 의도적으로 중단했으며, 전체 성공 결과로 세지 않는다. 대상 stream/rich 25개와 실제 엔진 rich 저장/재열기 1개는 수정 후 통과했다. 긴 목차 R1~R3의 버튼 부재·soft break·가상 캔버스 관측 실패도 기존 로그에 보존한다.

같은 최종 소스의 source R2는 60 passed / 1 failed / 5 skipped, 12.2분이었다. 실패 한 건은 assertion 실행 전 0ms에서 Windows worker가 `3221226505 (0xC0000409)`로 종료된 경우이며 제품 검증 통과로 바꾸지 않는다. 같은 취소 경계는 built에서 10.5초, 별도 단독 source 시험에서 4.5초에 통과했다. 다른 회귀 실행을 종료한 뒤 기존 cache를 보존하는 실행별 `PWTEST_CACHE_DIR`로 전체 source R3를 단독 실행해 61개 통과·native 오류 없음·잔여 리스너 0을 확인했다. native 종료의 근본 원인은 특정하지 못했다.

선택적 skip은 source에서 장시간 lease/autosave soak 1개와 명시적 녹화 fixture를 요구하는 RECORDED 4개, built에서 같은 soak 1개다. 오늘 필수 프로필·자동 목차·긴 제목/20쪽·stream·rich는 skip하지 않는다. 실제 새 Codex 호출 2건은 녹화 결과 재생과 별도다.

## 긴 제목·목차·저장 보존

실제 rhwp 측정은 목차 전 **18쪽**, 목차 삽입 후 **20쪽**이다. 제목 물리 쪽수는 `[1,1,4,7,10,13,16,18]`에서 `[3,3,6,9,12,15,18,20]`으로 이동했고 **2회**에 수렴했다. 각 pass의 `layoutEvidence`·같은 export SHA·epoch/revision을 연결하고 마지막 목차 숫자와 모든 제목의 실제 쪽수를 대조한다. R4의 epoch는 1, revision은 0이다. 검증 응답이 UI에 도착하기 전 `data-ready=false`와 `inert`를 확인했다.

[실제 목차 렌더링](evidence/report-toc-long-2026-10-17.png)에서 긴 제목은 세 줄로 보이며 두 자리 쪽수 12·15·18·20이 잘리거나 겹치지 않는다. 표·흐름도·본문 강조와 표준 서식도 보존된다. 저장·다운로드·웹 재열기의 바이트가 같고 버전은 1개다. 확인용 20쪽 파일 SHA는 `4bbbe9586ba68def2df90115bd6e280e3122eb7ec56c8e814b67d8448caef518`이다.

기존 경계 시험으로 후속 편집, 명시적 목차 갱신, 준비/검증/취소 응답 유실의 고정 ID 재확인, 다른 preview/늦은 완료 차단, 손상/삭제/중복/미수렴의 현재 초안 보존, commit 뒤 저장 재확인과 후속 편집을 검증한다. 기존 `data`·`.cache/user-review/data`의 blob·기준 자산 71개와 두 DB는 실행 전후 해시/행 스냅샷이 모두 같았다. 새 확인용 DB와 합성 시험 데이터는 이 보존 대상과 분리한다.

## 실제 Codex·Hancom·확인용 앱

기존 ChatGPT 로그인과 실행기로 **실제 보고서 1회 + 문단 후보 1회**를 호출했다. 보고서는 `executive-decision`, 공개 검색 없이 합성 업무를 사용했다. 실제 rhwp 초안 적용·자동 목차·자동 테스트 승인·서버 저장·재열기와 표/흐름도 내용을 확인했다. 문단은 후보 2개 비교·자동 테스트 승인·exact 영수증·v2 저장·재열기를 확인하고 v1 blob SHA가 유지됐다. 사람의 업무 문서 승인이나 모의 모델 결과로 표현하지 않는다. 증거는 `.cache/live-final-codex-20261017-2fa1aace/proof/proof.json`이다.

한컴 2018의 최초 시험에서는 긴 preview 사본 열기·별도 HWPX SaveAs와 본문/표 보존을 확인했으나, 최종 사본 재시험은 복구 창 제어 실패로 보류했다. 이후 사용자가 복구 창의 `불러옴`과 후속 안내의 `확인`을 직접 선택했고, 복구된 이전 시험 기준 사본은 별도 ASV 백업으로 보존했다. 원래 실패 영수증은 그대로 유지한다.

추가 검증에서는 최종 실제 Codex 4쪽 사본과 긴 목차 20쪽 사본을 각각 Hancom으로 열고, 명시적으로 `한글 표준 문서 (*.hwpx)` 형식의 새 파일에 저장한 뒤 창을 닫아 새 창에서 재열었다. 두 저장본의 논리 본문(공백 정규화)과 문단 순서가 같았고, 짧은 파일의 표 3개/셀 23개와 긴 파일의 표 5개/셀 44개를 보존했다. 문자별 굵기·색·크기, 괄호 선행 표지 강조, 파란 위/아래선 XML과 좌우선 없음도 모두 같았다. 짧은 보고서는 실제 4쪽이며, 긴 보고서는 Hancom 인쇄 미리 보기에서 총 20쪽·2쪽 목차·20쪽 출처 제목을 확인했다. 긴 제목과 두 자리 쪽수에 관찰된 잘림·겹침은 없다. 입력 원본과 사본은 변경되지 않았다. 증거는 `.cache/final-hancom-roundtrip-2026-10-17-r1/native-ui-proof.json`, `short-comparison-r2.json`, `long-comparison.json`이다. 첫 비교 관찰기의 간접 문단 스타일 참조 누락을 보정했고 이전 결과도 보존했다.

두 Hancom 저장본을 실제 rhwp에도 다시 가져와 내보냈다. 짧은 4쪽·긴 20쪽, 내용 손실 0, 전체 텍스트와 표/셀 수 보존을 확인했고 `layoutEvidence`를 같은 export SHA·epoch/revision과 연결했다. 짧은 사본의 가져오기 경고는 0개, 긴 사본은 빈 `lineseg` 배열 경고 120개이며 실제 렌더링·내용 보존 결과와 별도로 기록한다. 이 경로는 API·DB 쓰기와 모델 호출 없이 별도 임시 브라우저에서 수행했다. 제공 번들 10개 파일·WASM SHA도 시험 엔진과 일치한다. 증거는 같은 폴더의 `rhwp-readonly-proof.json`이다.

제목선의 주황/황색 표시는 고정 기준 원본 사본에서도 재현됐으며 XML의 파란색 값은 원본·생성본·Hancom 저장본이 같다. 이 Hancom 2018 시각 차이는 남아 있어 완전한 시각 호환으로 판정하지 않는다. 복구 창을 닫은 조작은 위 두 번의 사용자 조작이며, 최종 사본의 열기·저장·재열기는 자동 시험으로 수행했다. 실제 폐쇄망 운영·사람 검수는 수행하지 않았다.

확인 URL은 [로컬 확인용 앱](http://127.0.0.1:15181/)이다. `start-user-review.ps1`로 별도 `.cache/final-user-review-2026-10-17-r2/data`를 사용해 실행하며 종료하지 않고 유지한다. 기존 서비스와 데이터는 보존한다. 제공 중인 핵심 앱/엔진 파일 10개의 HTTP SHA와 시험한 진단 bundle 전체 85개 파일이 일치한다. 목록에 넣은 세 보고서는 각각 열기·재열기, 내용 손실 0, 전체 텍스트 보존, 원본/API/UI/엔진 SHA 일치와 버전 1개를 확인했다.

확인용 파일은 Git ignored `reports/final-review-2026-10-17/`에 보존한다.

- `short-rich-3-pages.hwpx`: 실제 rhwp + 모의 rich 생성, 수동 편집·저장 재시도·재열기를 통과한 3쪽 사본.
- `long-toc-20-pages.hwpx`: 실제 rhwp + 합성 입력, 긴 제목·20쪽 목차 사본.
- `short-actual-codex-report.hwpx`: 이번 실제 Codex 보고서, SHA `f11dc5575967235e7aee478c8e876204dad86b9b49d539ff753f9fb1a21d4d7a`.
- `short-hancom-saved.hwpx`: 최종 짧은 보고서의 Hancom 저장·재열기 사본, SHA `9cebcfd9ed7d38e43bb7211c36753753758e0767cb326d325eab4ff1388173c6`.
- `long-hancom-saved.hwpx`: 최종 긴 보고서의 Hancom 저장·재열기 사본, SHA `239a3531dee0194af5589d0f8dfed6b77bf66c736b395863efd8587c5ddeffc8`.

임의 마켓 HWPX의 보고서 프로필 변환, 범용 템플릿 편집기, 복합 이미지/필드 병합, SSO·사내 폐쇄망 운영 구축은 후속 범위다. 실문서·DB·토큰·인증 파일·전체 cache·trace·생성 HWPX는 Git에 포함하지 않는다.

## 추가 작업 — 업무용 절 제목·목차 가독성

이 추가 작업의 시작 HEAD는 `82388691c821df38138e288be27f129c1c578301`이며 당시 작업 트리는 깨끗했다. 위 전체 회귀와 manifest는 앞선 기능 마감의 역사적 증거로 유지한다. 이번에는 새 초안의 생성 지시와 작성 예시만 보완했고, 이전 425개 시험을 이번 실행 수에 합산하지 않는다.

문제의 20쪽 샘플은 고정 경계시험이다. 목차 8항목 중 6개 절 제목이 93~97자이고 각각 3줄이며, 목차 자체는 물리 2쪽의 한 페이지에 들어간다. 원인은 항목 수보다 항목별 길이다. 이전 실제 Codex 1건은 사용자가 짧은 제목을 지정한 결과라 일반 생성에서 장문이 반복된다는 근거로 쓰지 않는다. 공통 지시에는 제목 번호 금지와 기본 흐름만 있어 간결한 제목 작성·설명 이전 기준을 추가할 필요가 있었다.

`backend/app/report_runner.py`에 하나의 논점을 나타내는 구별 가능한 명사형 제목, 필요한 기간·대상·범위 유지, 배경·조건을 첫 본문으로 옮기되 의미 보존, 같은 수준의 표현 통일, 한 줄 목표/필요한 두 줄 허용을 추가했다. 일반적인 절 구성은 검증된 프로필 목적을 우선하는 흐름으로 명확히 했다. `docs/document-writing-standard.md`에는 같은 기준과 두 가지 전후 예시를 추가했다. 예를 들어 긴 승인 조건 제목을 `지연 자료 재공유 승인 요청`으로 쓰고, 담당자 확인 후에만 운영팀이 재공유한다는 조건은 첫 본문에 남긴다.

프로필 정의·리비전·SHA, ReportDraft 스키마·기존 한도, 목차·UUID·원문 해시·실제 쪽수·최대 3회 수렴·명시적 갱신 코드는 변경하지 않았다. 생성 HWPX나 frozen preview의 제목을 사후 축약하지 않는다. `backend/tests/test_report_templates.py`는 실제 생성 진입점과 stdio 지시/요청을 세 프로필로 확인하며, 최종 실행은 **14 passed / 0 failed / 0 skipped**, 3.19초, exit 0이다. 로그는 `.cache/heading-input-tests-final-2026-10-17.log`다. 기존 Starlette 경고 1개가 있다.

실제 Codex는 공개 검색 없이 합성 업무 자료로 총 **4회** 호출했다. 표준 업무·의사결정·대응 계획의 검증된 완료본은 각각 1개이며 실제 rhwp에서 **4·5·5쪽**, 모두 목차 **1페이지·6항목·항목별 1줄**이다. 기본 16pt 목차·15pt 본문과 동일한 native 표 폭을 유지했다. 세 프로필의 제목은 각각 정비 필요성/수집 현황/보존 조건/일정, 관리 쟁점/대안 비교/시범 권고/승인 일정, 대응 목표/우선 조치/역할과 일정/완료 기준의 흐름을 보여준다. 기준일·120/18/7건·집합 중복 미확인·3개 부서·원본 보존/사본 확인 조건·목표와 미완료 구분, 의사결정의 조건부 2주 시범은 본문에 보존됐다. 관련 첫 항목과 바로 이어지는 보조 항목을 함께 검토했으며 모든 사실이 첫 □ 한 문단에만 들어갔다고 주장하지 않는다.

초기 대응 계획 1건은 완성 JSON 검증 단계에서 거부됐다. 기존 실행기가 원문·검증 예외를 보존하지 않아 정확한 validator 원인은 미확인이다. 해당 task와 표시용 부분 출력, 3회 시점 증거를 보존했고 HWPX에는 적용하지 않았다. 같은 지시·입력으로 완성 응답을 기록하는 진단 생성 1회를 별도로 수행했고 정상 ReportDraft 검증·저장·재열기를 통과했다. 원인을 수정해 해결했다고 표현하거나 실패를 성공 집계에 합치지 않는다. 최종 확인용 앱은 진단 수집 wrapper를 제거한 일반 `app.main`으로 복원했고, 재시작 전후 3문서의 버전·blob SHA와 읽기 전용 재열기 결과가 일치했다.

모든 목차 항목의 제목 UUID·텍스트 SHA·물리 쪽수는 같은 엔진 export SHA·epoch/revision과 연결했다. 대표 표준 업무 샘플은 직접 본문 편집 → 갱신 필요 → 명시적 목차 갱신 → 서버 저장 → 일반 웹 편집기 재열기를 확인했다. 보호 파일 91개와 기존 DB 2개, 긴 제목 fixture·원문·과거 실행 증거는 변경되지 않았다. 실제 모델 문구·화면은 에이전트가 검토했으며 사람의 업무 승인·사람 검수와 구분한다. 이 정상 샘플에서는 별도 목차 표제가 필요한 사례가 없었다.

상세 결과와 해시는 [업무용 제목 검증](evidence/business-headings-2026-10-17.json), 화면은 [표준 업무](evidence/business-heading-standard-2026-10-17.png)·[의사결정](evidence/business-heading-executive-2026-10-17.png)·[대응 계획](evidence/business-heading-response-2026-10-17.png)에 기록한다. 확인용 HWPX는 Git ignored `reports/business-headings-2026-10-17/standard-business.hwpx`, `executive-decision.hwpx`, `response-plan.hwpx`다. 확인 앱은 기존 앱을 보존한 별도 [15183 포트](http://127.0.0.1:15183/)에서 제공한다. 프런트엔드·엔진 산출물을 재사용했으며 전체 pytest/E2E와 빌드·배포 시험을 반복하지 않았다.

화면 확인 중 의사결정 샘플의 본문에서 기존 `report_outline.py`가 끝말 `필요함.`을 별도 □로 나누는 사례를 관찰했다. 원문 문자는 보존되며 제목·목차와 독립적인 기존 본문 분할 경로다. 이번 작업에서는 이 경로와 Hancom 색상·Docker/WSL 배포 환경을 변경하지 않았다. 긴 경계 샘플과 새 업무 샘플은 내용이 달라 동일 입력의 모델 A/B 성능 비교로 해석하지 않는다.
