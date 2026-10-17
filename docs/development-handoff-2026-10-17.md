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

한컴 2018에서는 시험용 긴 preview 사본 열기와 별도 HWPX SaveAs, 본문 전체 텍스트와 표 5개 보존을 확인했다. 제목선의 주황/황색 표시는 고정 기준 원본 사본에서도 재현됐고 XML의 색상 값은 원본·생성본·Hancom 저장본이 같다. 완전한 시각 호환으로 판정하지 않는다. 최종 확인 사본의 native 재시험은 복구 창을 제어하지 못해 미검증이다. helper 오류와 복구 자료를 보존하고 소유한 시험 Hancom 프로세스만 정리했다. 실제 폐쇄망 운영·사람 검수는 수행하지 않았다.

확인 URL은 [로컬 확인용 앱](http://127.0.0.1:15181/)이다. `start-user-review.ps1`로 별도 `.cache/final-user-review-2026-10-17-r2/data`를 사용해 실행하며 종료하지 않고 유지한다. 기존 서비스와 데이터는 보존한다. 제공 중인 핵심 앱/엔진 파일 10개의 HTTP SHA와 시험한 진단 bundle 전체 85개 파일이 일치한다. 목록에 넣은 세 보고서는 각각 열기·재열기, 내용 손실 0, 전체 텍스트 보존, 원본/API/UI/엔진 SHA 일치와 버전 1개를 확인했다.

확인용 파일은 Git ignored `reports/final-review-2026-10-17/`에 보존한다.

- `short-rich-3-pages.hwpx`: 실제 rhwp + 모의 rich 생성, 수동 편집·저장 재시도·재열기를 통과한 3쪽 사본.
- `long-toc-20-pages.hwpx`: 실제 rhwp + 합성 입력, 긴 제목·20쪽 목차 사본.
- `short-actual-codex-report.hwpx`: 이번 실제 Codex 보고서, SHA `f11dc5575967235e7aee478c8e876204dad86b9b49d539ff753f9fb1a21d4d7a`.

임의 마켓 HWPX의 보고서 프로필 변환, 범용 템플릿 편집기, 복합 이미지/필드 병합, SSO·사내 폐쇄망 운영 구축은 후속 범위다. 실문서·DB·토큰·인증 파일·전체 cache·trace·생성 HWPX는 Git에 포함하지 않는다.
