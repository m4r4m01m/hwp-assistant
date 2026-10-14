# HWP Assistant 기능 구현 및 검증 — 2026-10-06

사용자의 “기능 구현부터 1시간 이상 진행, 이후 직접 테스트” 요청에 따라 기존 미커밋 구현에 기능을 추가했다. 앞선 설계 문서와 검증 기록은 역사적 자료로 보존했다. 실제 사용자 시험 순서는 [새 기능 테스트 안내](features-user-test-2026-10-06.md)에 있다.

## 기준과 식별

- 로컬 HEAD: `1e0538781e02ac85d095bb3c72bf844943c305b3`, 브랜치 `main`.
- 시작 시 미커밋 추적 파일 26개, 미추적 파일 35개. 이전 실행 변경셋 `46a208cd44a030f34fda0ad53032b8d0ae6178f68c82cda7717e12caa7d7dd09`를 실제 파일 해시와 대조했다.
- 원격 소스로 교체하거나 commit/push/merge/reset/clean하지 않았다.
- 최종 실행 소스 V2: `44360c0793b4ae0c789755dd8d97eda28e26d1fdf21845309fe6ce69ec65d55d` — 소스·시험·스크립트·lock 102개와 명시한 진단 엔진 7개 파일의 집합 해시.
- 먼저 고정한 V1: `d3d310a3eb1c10f981b859e380e2c6a41a60cf26f8f7b4be59db1b1c8ac8e019`. V2와의 차이는 `frontend/tests/local-rewrite.spec.ts`의 새 재확인 시험에서 백그라운드 생성 완료를 기다리는 조건 한 곳이다. 앱·서버·빌드 스크립트·장시간 시험 함수·엔진의 바이트는 같다.
- 문서와 요약 JSON은 실행 소스 집합 해시에서 제외하며 검토 묶음에서 별도로 해시를 기록한다. HEAD만으로 미커밋 상태를 식별하지 않는다.

## 이번에 사용할 수 있게 된 기능

### 확인한 작성 정보를 후보 생성에 연결

작성 목적, 최대 20개의 확인 사실, 작성 기준을 저장할 수 있다. 사실별로 사용자 입력·문서 버전·사용자가 확인한 AI 문장 중 출처를 지정한다. 서버가 확인자와 확인 시간을 기록한다. AI 출처를 외부 검증된 사실로 바꾸지 않는다.

사용자가 “저장한 작성 정보 사용”을 선택하면 작업 접수 트랜잭션 안에서 컨텍스트 ID·리비전·문서 기준·실제 값·출처·확인 기록을 고정한다. 선택 문단 및 지시와 함께 직렬화한 실제 모델 입력과 SHA-256을 기록한다. 나중에 작성 정보가 바뀌어도 이미 접수한 작업의 입력은 바뀌지 않는다.

기본 생성 경로는 Codex다. 선택 문단과 지시만 사용하는 기본 요청은 `context_used=false`다. 새 기능을 명시적으로 선택하고 실제 입력에 작성 정보를 넣은 작업만 `context_used=true`다. 선택형 `llm` 경로도 같은 입력 계약을 사용한다. 전체 대화, 과거 AI 답변, 기존 자유 메모는 자동으로 넣지 않는다.

새 세션에서는 DB에 저장된 컨텍스트와 작업 입력을 다시 읽을 수 있다. 이 동작은 자동 생성이나 재실행을 일으키지 않는다. 최신 리비전·문서 기준과 맞지 않는 컨텍스트는 새 생성에 사용할 수 없다. 작성 정보가 바뀐 이전 후보는 새로 승인할 수 없으며, 이미 승인한 출력의 영수증·저장 재확인은 계속 허용한다.

### 저장 응답 유실과 동시 편집 복구

작성 정보 저장마다 고정된 요청 ID와 본문을 보관한다. commit 뒤 응답이 유실되면 같은 저장을 재확인하며 리비전과 확인 기록을 중복 생성하지 않는다. 응답 유실 뒤 입력한 내용은 별도의 미저장 편집으로 남긴다. 이후 다른 저장이 이루어진 오래된 재시도는 충돌로 거부한다.

다른 창에서 먼저 저장한 경우 현재 입력과 최신 서버 값을 비교할 수 있다. 사용자가 최신 리비전을 확인한 뒤 자신의 입력을 계속 편집할 수 있으며 자동 병합하지 않는다. 미저장 작성 정보가 있는 상태에서 대화 전환·패널 닫기·문서 종료·페이지 이탈에는 확인 절차가 있다.

생성 접수 응답이 유실되면 “보관한 생성 요청 재확인”으로 기존 ID·문단·지시·작성 정보 리비전을 재사용한다. 이후 새로 입력한 지시나 작성 정보를 지우지 않는다. 이전에 구현한 같은 문서의 A 중단 → B 요청 → A 늦은 성공/4xx → B 응답 유실 → B 동일 요청 재확인도 유지한다.

### 후보 비교와 복사

후보에서 추가된 표현을 강조하고, 원문에서 빠진 표현을 따로 볼 수 있다. 한글 결합 문자·이모지 등은 grapheme 단위로 비교하며 긴 문단은 작업량을 제한한다. 표시는 승인 대상 문자열을 정규화하거나 자르지 않는다.

“후보 복사”는 정확한 후보 문자열만 클립보드로 복사한다. 적용·영수증·저장을 만들지 않는다. 이전 개선 지시를 입력칸으로 다시 불러올 수도 있다. 기존 로컬 모델 검토 패널에는 기본 Codex 후보 화면으로 이동하는 안내를 추가했다.

### 저장본 사본과 문서함 정렬

문서함의 “저장본 복제”는 선택한 저장 버전의 바이트를 새 문서 첫 버전으로 복사한다. 사본의 소유자는 요청자이며 원본의 권한·대화·이력은 복제하지 않는다. 원본 문서나 과거 파일을 변경하지 않는다.

사본 ID·원본 버전·요청 ID·이름을 고정해 응답 유실 뒤 같은 요청을 재확인한다. 사본이 이미 편집된 뒤 재확인해도 이전 내용으로 되돌리지 않는다. 재시도에서도 현재 원본 접근 권한과 사본 소유권을 확인한다. 최근 등록순·오래된 등록순·이름순 정렬과 목록 새로고침을 추가했다.

### 별도 사용자 시험 실행

`scripts/start-user-review.ps1`은 기존 진단 엔진을 별도 경로에 복사·검증하고 현재 소스의 앱을 새로 빌드한다. 기본 주소는 `http://127.0.0.1:15180`, 문서함은 `.cache/user-review/data`다. 기존 `data`, 인증 설정, 기본 엔진은 사용하거나 변경하지 않는다. 시작 시 AI를 호출하지 않는다. 사용자가 후보를 요청할 때 기존 Codex CLI 경로를 사용한다.

## 재현된 문제와 수정 범위

| 대상 | 수정 전 근거 | 수정 | 수정 후 |
|---|---|---|---|
| 새 작성 정보 편집기의 늦은 저장 완료 | 실제 TSX 저장 콜백 시험 3개 실패: 성공·4xx·네트워크 실패가 이미 닫힌 편집기에서 부모 상태를 변경 | 마운트 생존 여부를 API 응답·상위 새로고침 후 및 catch/finally에서 검사 | 같은 3개 통과 |
| 새 생성 재확인 E2E의 호출 수 판정 | 작업 접수는 성공했으나 비동기 제공자가 시작하기 전에 호출 수 0을 읽어 실패 | 해당 작업이 `ready`가 될 때까지 관찰한 뒤 최종 호출 수 1을 검사 | V2 source와 built 모두 통과 |
| 진단 번들 첫 일괄 실행의 연결 중단 | 첫 6개 통과 뒤 preview 접속 ECONNRESET/CONNECTION_REFUSED, 총 17개 실패 | 앱 코드는 변경하지 않음. 새 번들과 별도 실행 디렉터리에서 다시 실행 | 23개 통과, 1개 장시간 시험 제외. 첫 연결 중단의 원인은 확정하지 않음 |

기존 실행 슬롯·설정 고정·최신 ORM 상태·권한 회수·임대 처리와 승인 저장 의미를 다시 구현하지 않았다. 안전 경계 35개는 기존 파일 그대로 전체 pytest에 포함됐다. 페이지 수 거부 복구, 승인·영수증·저장 응답 유실, 문서 전환, 같은 문서의 늦은 성공/4xx 시험은 source/built 회귀에서 통과했다.

기존 보호 조건과 이번 전체 실행의 연결은 다음과 같다. 기존 시험을 새로 구현한 것으로 계산하지 않는다.

| 보호 조건 | 보존한 시험과 재현 방식 | 이번 결과 |
|---|---|---|
| A. 중단·권한·임대 변경과 후보 게시 경쟁 | `test_event_ordered_authority_transitions_do_not_revive_pending_work`의 이벤트 장벽, `test_publication_lock_refreshes_preloaded_task_and_document`의 미리 읽은 ORM 상태, `test_published_success_survives_late_cancel_and_failure`의 게시 후 중단/실패 | 전체 pytest 통과. 기존 안전 경계 파일 해시 유지 |
| B. 페이지 수 거부 뒤 제품 복구 | E2E `page-count rejection after approval preserves bytes and permits recovery`: 자동 승인 후 실제 rhwp 거부 → 영수증 없음·버전 1개·출력 바이트 유지 → 비교 종료·수동 저장·새 요청 | source와 built 모두 통과 |
| C. 생성 재확인과 승인 저장 분리 | E2E `lost approved-save response...`, `lost approval response...`, `lost receipt response...`, `late document A...`, `same-document late success/4xx...`; 응답 장벽과 유실을 이용 | source와 built 모두 통과. 새 작성 정보 편집/재확인 시험도 별도 통과 |
| D. 실제 실행 자원과 설정 고정 | `test_event_held_workers_keep_slots_after_logical_cancel`, `test_codex_and_llm_share_capacity_until_ready_worker_cleanup_finishes`, `test_codex_dispatch_freezes_model_and_endpoint_at_task_acceptance`, `test_local_model_endpoint_key_timeout_fixed_at_acceptance`; 실제 합성 CLI 자식 프로세스 종료 시험 2개 | 전체 pytest 통과. 실제 모델 호출은 없음 |

조건별 정확한 매개변수 사례와 이전 수정 전 증거는 기존 `docs/rewrite-boundaries-verified-2026-10-06.md` 및 `docs/evidence/rewrite-boundaries-verified-2026-10-06.json` 기록과 함께 읽는다. 이번 결과는 새 기능 추가 뒤 다시 실행한 결과이며 과거 시험 개수를 합산한 숫자가 아니다.

## V2 일괄 회귀

각 행은 독립 실행 결과이며 서로 더해서 단일 시험 실행 수로 표시하지 않는다.

| 실행 | passed | failed | skipped | 결과 |
|---|---:|---:|---:|---|
| 전체 pytest | 210 | 0 | 0 | 52.93초, 의존성 DeprecationWarning 1개 |
| TypeScript `tsc --noEmit` | — | — | — | exit 0 |
| 엔진 빌드 보호 시험 | 11 | 0 | 0 | exit 0 |
| 실제 TSX 콜백 및 텍스트 비교 Node 시험 | 24 | 0 | 0 | 기존 요청 9 + 작성 정보 저장 3 + 비교 12 |
| 인증 포함 일반 source E2E | 35 | 0 | 1 | 36개 수집, 기본 실행에서 1시간 시험 제외 |
| 현재 소스로 새로 빌드한 진단 앱 E2E | 23 | 0 | 1 | 복제 및 후보 흐름 관련 24개 수집 |
| 기본 배포 빌드 보호 | — | — | — | 예상대로 exit 1, `Missing pinned core/bundle provenance` |

새 backend 시험은 작성 정보 21개, 저장본 복제 16개다. 전체 210개에 이미 포함된 숫자이며 별도로 더하지 않는다.

주요 명령은 다음과 같다. 아래 출력 경로는 이번 실행의 실제 경로이며, 재실행할 때는 기존 증거를 덮어쓰지 않도록 새로운 `OutDir`를 지정한다.

```powershell
.venv/Scripts/python -m pytest -q --junitxml=.cache/context-features-2026-10-06/final-pytest-v2.xml
node frontend/node_modules/typescript/bin/tsc --noEmit --project frontend/tsconfig.json
node --test scripts/engine-build.test.mjs
node --test scripts/rewrite-request.test.mjs scripts/context-save.test.mjs scripts/text-diff.test.mjs
powershell -NoProfile -File scripts/test-isolated.ps1 -EnginePublic .cache/engine-release-2026-09-27/public -OutDir .cache/context-features-2026-10-06/final-source-e2e-v2
powershell -NoProfile -File scripts/test-isolated.ps1 -EnginePublic .cache/engine-release-2026-09-27/public -OutDir .cache/context-features-2026-10-06/final-built-e2e-v2 -BuiltApp -TestPattern 'MOCK HTTP local provider|MOCK provider|MOCK delayed provider|saved document copy'
```

진단 앱 JS는 `assets/index-D8C2twMN.js`, CSS는 `assets/index-CNUrtNfP.css`다. HTTP로 받은 10개 파일을 각 실행의 실제 번들과 해시 대조했다. 진단 엔진은 rhwp 0.8.6 / `e8800c8def63449808a4092798442652ed460552`, WASM SHA-256은 `0e81cdeb4a61de8dd12be6d79d3bf06a8c1153525883e4112f4dcb1257d68195`다. 기본 배포 가드는 낮추지 않았다. Vite preview에 실제 nginx 설정의 헤더를 적용했으며 nginx 실행 검증으로 계산하지 않는다.

## 장시간·실행기·사람 시험 구분

- V1에서 실제 API 프로세스 종료·재시작 후 602.45초를 관찰했다. 602.4초 시점에 `expired`를 확인하고 최종 수신 중단까지 통과했다. 모의 HTTP 모델 호출은 1회, 문서 버전은 1개, 원본 바이트는 동일했다. 이 시험은 합성 저장 파일을 사용하며 실제 편집기 시험이 아니다.
- V1에서 확인 작성 정보를 포함한 실제 rhwp 비교 화면의 3600초 유지 시험을 시작했으나, 사용자가 보고서 작성 흐름으로 작업을 전환하도록 요청하여 중단했다. 마지막 완료 체크포인트는 2774.548초(약 46.2분), 임대 갱신 91회, 오류 0개, 저장 버전 1개다. 종료 후 복구 단계는 실행하지 않았으므로 1시간 시험 통과로 계산하지 않는다. 중단 기록은 `.cache/context-features-2026-10-06/soak-user-stop.json`이며 남은 시험 리스너는 없다. 짧은 180초 사전 시험도 1시간 성공으로 계산하지 않는다.
- V1/V2의 실행 소스 집합 ID는 서로 다르다. V2 차이는 장시간 시험 외 다른 E2E 한 곳의 대기 조건뿐이며, 장시간 시험에서 사용한 제품과 장시간 시험 함수는 V2와 같다. V1 장시간 결과를 V2 일괄 실행 수에 합치지 않는다.
- rhwp 적용·저장·다운로드·재열기와 브라우저 클릭은 자동 시험이다. 모델 응답은 합성이며 실제 Codex 또는 실제 로컬 모델의 문장 품질을 검증하지 않았다. 사람의 승인 클릭 시험도 수행하지 않았다.
- 실제 모델 호출, 모델 다운로드, 유료 API 전환, Docker/PostgreSQL/nginx 실행은 하지 않았다.

## 보존과 남은 확인

시작 시 362개 파일을 해시로 기록했다. 기존 추적/미추적 파일의 삭제는 없고, 보호 대상 265개(기존 DB·blob 56개, 기본 엔진 86개, 원래 진단 엔진 82개, 기존 문서 41개)는 동일하다. 기존 저장소 읽기 전용 감사는 문서 33개·버전 55개·blob 55개를 확인했고 불일치와 고아 파일이 없었다. 실제 문서 내용·DB·인증 파일은 검토 묶음에 포함하지 않는다.

남은 사용자 동작은 실행 명령을 켜고 별도 문서함에서 실제 파일을 업로드한 뒤, 목적·확인 사실·기준을 입력하고 Codex 후보를 직접 비교·승인하는 것이다. 실제 문장 품질, 원문의 이름·금액·날짜·부정 의미, 한컴 왕복 표시를 확인한다. 기본 배포 엔진의 provenance 충족과 실제 PostgreSQL/사내 네트워크 검증은 이번 진단 결과로 대체하지 않는다.
