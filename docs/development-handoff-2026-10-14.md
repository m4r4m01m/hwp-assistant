# HWP Assistant 누적 개발 인수인계 · 2026-10-14

문서 편집·저장, AI 후보 검토, 작성 정보, 실시간 보고서, 표준 서식과 표·흐름도, 템플릿 마켓, 기본 블록 삽입, 일반 편집기의 실제 쪽수 목차까지 누적 구현했다. 최신 TM-10의 보고서 프로필 선택·생성 단계 자동 목차도 코드와 중간 시험이 있으나, 같은 최종 변경셋의 전체 회귀와 서비스 반영은 아직 완료하지 않았다.

2026-10-14 사용자는 누적 작업을 **새 브랜치에 commit/push**하도록 승인했다. 대상 브랜치는 `codex/hwp-assistant-progress-2026-10-14`, 기준 commit은 `1e0538781e02ac85d095bb3c72bf844943c305b3`다. 이전 문서의 당시 미커밋·푸시 금지 기록은 역사적 사실로 보존한다. 이번 승인은 개발 변경의 공유 범위이며 **main 병합·운영 배포·새 모델 호출을 승인하거나 그 완료를 뜻하지 않는다.** 실제 commit과 push 성공은 최종 전달 및 Git 이력으로 확인한다.

오늘 TypeScript·node 37개·추적 변경의 `git diff --check`로 공유 준비를 점검했다. 새 전체 pytest/E2E 회귀, 실제 모델 호출, 한컴 검수, 확인용 서비스 반영은 수행하지 않았다. 아래 준비 점검을 전체 제품 회귀 완료로 해석하지 않는다.

## 읽는 순서와 기준

- 요구사항 기준선: [계획 v0.4](internal-doc-assistant-plan-v0.4.md). 완료 보고서로 고치지 않는다.
- 누적 기능과 사용법: [2026-10-06 기능](features-implemented-2026-10-06.md), [사용자 확인 순서](features-user-test-2026-10-06.md).
- 직전 전체 검증 완료 상태: [TM-09 구현](toc-2026-10-11.md), [TM-09 WBS](wbs-progress-toc-2026-10-11.md).
- 최신 미완료 구현: [TM-10 프로필·자동 목차](report-template-toc-2026-10-12.md), [TM-10 WBS](wbs-progress-report-toc-2026-10-12.md).

누적 개발의 출발 HEAD는 `1e0538781e02ac85d095bb3c72bf844943c305b3`다. 직전 TM-09의 최종 실행 소스 집합은 `dc20addeba04c978a4dcbcfdd5ab3fc651140a9c260b5646f8a068665acaaa2f`이며 [manifest](evidence/toc-manifest-final-2026-10-11.json)에 소스·lock·시험 145개, 새 진단 엔진 82개, 이전 엔진 83개와 native core 4개를 각각 기록했다. 이후 TM-10 변경은 이 집합과 다르다. 새 Git commit이 생겨도 직전 manifest의 시험 결과를 새 commit 전체의 결과로 해석하지 않는다.

기존 날짜별 문서·JSON·manifest·검토 ZIP을 덮어쓰지 않는다. `remaining-work.md` 등 오래된 문서의 당시 “미구현” 표시는 해당 시점의 상태다. 후속 구현 여부는 이 문서와 각 기능의 날짜별 기록을 함께 읽는다.

## 누적 기능과 구현 범위

| 작업 묶음 | 구현한 기능과 안전 경계 | 현재 검증 상태 |
|---|---|---|
| 문서 편집·저장·버전 | HWPX 편집, 저장/재열기·다운로드, 불변 버전, 복원 시 새 버전, 저장본 사본, 권한·임대·기준 버전 검사. 저장 응답 유실 시 고정 출력/키 재확인과 후속 편집 보존 | 이전 전체 회귀와 실제 rhwp 기록 있음. 최신 TM-10 최종 소스 회귀 대기 |
| AI 문단 개선 | 미저장 문단 스냅샷 → 후보 2개 → 비교·사용자 승인 → exact 적용 영수증 → 서버 저장. 페이지 수 거부 복구, 실행 슬롯·실행 설정 고정, 최신 ORM/권한/임대 검사, 같은 문서의 이전 요청 늦은 성공/4xx 보호 | 이전 안전 경계·source/built 회귀 기록 있음. 실제 사내 모델·업무 품질 검수는 별도 |
| 작성 정보·문맥 | 목적·사용자 확인 사실·출처·작성 기준 저장, 리비전 충돌 비교, 실제 사용 값과 입력 해시 고정, 새 세션 조회, 저장/생성 응답 유실 재확인 | 기능 구현·이전 회귀 기록 있음. 전체 대화 자동 투입·선택 메시지 연결은 미구현 |
| 보고서 요청·실시간 표시 | 요청 창과 작업 목록, Codex app-server의 실제 text delta 표시, 생성·내용 적용·서식 적용 단계 표시, 완료된 ReportDraft만 별도 편집 가능한 HWPX 초안에 적용 | 실제 Codex의 이전 합성 생성과 실제 rhwp/모의 이벤트 증거 있음. 오늘 새 호출 없음 |
| 기본 문서 표준 | 고정 기준서·profile 재사용, 제목 파란 위/아래선, 계층 들여쓰기, 15pt 본문, `□ → ㅇ → -`, 괄호 요약 표지, 네모 항목 약 2~3줄을 목표로 분할 | 기존 표준/가이드 반영 기록 있음. 모든 문서의 시각적 완성이나 한컴 호환 완료를 뜻하지 않음 |
| 보고서 표·흐름도·강조 | bounded 표와 편집 가능한 흐름도, 괄호 표지 bold, 실제 본문에 존재하는 문구의 bold/blue/bold_blue 강조. 셀·문구를 exact 적용/검증한 뒤 rich layout. 과거 frozen preview의 정책 보존 | 이전 실제 생성·기록 결과의 실제 엔진 검증 및 자동 회귀 있음. 임의 이미지 생성/사진 검색·일반 차트 편집기는 미구현 |
| TM-01~06 템플릿 마켓 | 현재 권한 기반 검색·5종 분류·정렬·페이지·개인 즐겨찾기, 저장 버전 고정 등록, 리비전 기반 정보 수정, SHA 검사한 실제 서식 미리보기, 새 문서 사본 생성, 응답 유실 재확인 | 1차 구현·이전 자동 검증 완료. 원본 후속 편집과 양식 고정 버전을 분리하며 임의 마켓 HWPX의 AI 보고서 양식 변환은 미구현 |
| TM-07~08 기본 블록·변수 | 표준 제목·요약·조치표 3종, 선언 변수 직접 입력·누락/길이 검사. 순수 HWPX 합성 → 별도 실제 rhwp 검증 → 현재 버퍼 fence 뒤 stable host 채택. 변경 없을 때 삽입 취소 | 기본 범위 구현·f38 단계 자동 검증 완료. 임의 조각·이미지/필드 병합·표 셀/복합 위치 확대는 후속 |
| TM-09 일반 편집기 목차 | 사용자가 고른 제목 UUID·2계층, 같은 iframe의 실제 물리 쪽수, 표준 회색 상자와 native 점선/right tab, 최대 3회 수렴, 편집 후 stale와 명시적 갱신, 삭제/중복/손상·스타일 한도 거부 | 직전 동일 최종 변경셋의 전체 자동 검증·당시 확인용 서비스 반영 완료. 사람/한컴/운영 검증 후속 |
| TM-10 보고서 프로필 선택 | 표준 업무보고·경영진 의사결정·대응/추진계획 3종. 같은 고정 서식에서 목적/작성 기준을 선택하고 ID·리비전·실제값·표준 SHA·profile SHA를 입력에 고정. 접수 응답 유실은 같은 입력 재확인 | backend/UI 연결 및 중간 계약·실제 rhwp/모의 보고서 시험 완료. 최종 전체 회귀·서비스 반영 미완료 |
| TM-10 생성 단계 자동 목차 | exact 영수증·rich layout 뒤 plan 제목 UUID를 연결하고 최초 ready 전 실제 쪽수 수렴. 자동 제목은 1계층. stable live/staging, 현재 버퍼·owner fence, 수동 편집 뒤 갱신, 단계별 응답 유실·중단·저장 재시도 보호 | backend/UI 구현·중간 시험 통과. 긴 제목/10쪽 경계, 새 최종 manifest, 같은 변경셋 전체 회귀·새 앱 검증·서비스 반영 남음 |

템플릿 마켓·블록·일반 목차·보고서 프로필은 서로 다른 범위다. 저장된 임의 HWPX를 등록/복사할 수 있다는 사실만으로 그 파일의 스타일·이미지·필드를 모든 보고서와 안전하게 병합할 수 있다고 표현하지 않는다.

기본 AI는 **Codex**다. 로컬·사내 OpenAI 호환 `llm`은 사용자가 선택하는 제공자 경로이며 기본 엔진을 대체하지 않는다. 모델 설치·다운로드·유료 API 대체를 추가하지 않았다. 문단+지시만 쓰는 기본 요청은 `context_used=false`이고, 사용자가 저장된 작성 정보를 명시적으로 선택하여 실제 입력에 포함한 후보 작업만 `context_used=true`다. 보고서의 프로필 선택을 전체 대화 사용이나 사실 검증 완료로 해석하지 않는다.

## 검증 근거: 실행별로 분리

### 오늘의 브랜치 공유 준비 점검

| 2026-10-14 명령 | 결과 | 범위 |
|---|---|---|
| `node frontend/node_modules/typescript/bin/tsc --noEmit --project frontend/tsconfig.json` | exit 0 | 현재 TypeScript 확인 |
| `node --test scripts/engine-build.test.mjs scripts/rewrite-request.test.mjs scripts/context-save.test.mjs scripts/text-diff.test.mjs` | 일괄 37 passed / 0 failed / 0 skipped | 엔진 보호 13개 + callback/context/diff 24개. 아래 역사적 37개와 별개 실행 |
| `git diff --check` | exit 0 | 추적 변경 대상의 whitespace 검사. 미추적 신규 파일 포함 검사나 제품 동작 검증을 뜻하지 않음 |

오늘 source snapshot manifest는 공유할 실제 파일 목록·해시를 기록하는 자료다. 아직 수행하지 않은 TM-10 전체 회귀의 실행 manifest나 성공 증거를 대신하지 않는다. 오늘 전체 pytest·source/built E2E는 실행하지 않았다.

### 직전 TM-09 최종 변경셋의 역사적 결과

아래 결과는 **2026-10-11의 dc20 변경셋**에 연결된다. 오늘 브랜치에 포함하는 최신 TM-10 전체의 통과 수가 아니다.

| 실행 | 수집/결과 | 의미 |
|---|---|---|
| 전체 pytest 단독 R2 | 371 passed / 0 failed / 0 skipped, 119.49초, exit 0 | 기존 Starlette 경고 1개, Windows native 진단 없음 |
| node 일괄 | 37 passed / 0 failed / 0 skipped | 엔진 보호 13개 + 콜백/저장/표시 24개를 한 번에 실행 |
| TypeScript·기본 앱 빌드 | TypeScript 통과, 기본 빌드 exit 1 | 기존 provenance 가드가 진단 엔진을 배포 엔진으로 쓰는 것을 차단. 가드 유지 |
| 일반·인증 source 전체 E2E | 56 collected: 46 passed / 0 failed / 10 skipped, 4.0분, script exit 0 | skip은 장시간/별도 보고서 환경이며 통과로 세지 않음 |
| 새 진단 앱 bundle·관련 E2E | 17 collected: 17 passed / 0 failed / 0 skipped, 1.8분, script exit 0 | 실제 rhwp + 모의 보고서. source 결과와 합산하지 않음 |
| 보존·당시 확인용 서비스 | 보호 파일 69개·두 DB 정규화 행 해시/개수 유지, served 앱/엔진 바이트 대조 | 당시 web/API 200. 오늘 현재 가동 상태나 TM-10 반영을 확인한 증거가 아님 |

세부 명령·해시는 [TM-09 구현 기록](toc-2026-10-11.md), [요약 JSON](evidence/toc-2026-10-11.json), [최종 manifest](evidence/toc-manifest-final-2026-10-11.json)에 있다. 그 이전 f38 블록·마켓·rich/stream 회귀도 각각의 날짜별 문서에서 유지하며 위 수와 더하지 않는다. 기존 602초·1시간 검증은 역사적 장시간 증거이며 짧은 후속 시험으로 재검증 성공을 표시하지 않는다.

### TM-10 중간 소스의 결과

이 실행들은 source freeze와 새 최종 manifest 이전의 부분 검증이다. 서로 합쳐 “이번 전체 시험 N개 통과”로 표현하지 않는다.

| 실행 | 결과 | 확인 범위와 한계 |
|---|---|---|
| 프로필 계약 R3 | 10 passed / 0 failed / 0 skipped, 2.47초, exit 0 | 입력 실제값/해시 고정, 오래된/위조 선택 거부, 과거 3필드 입력 호환, 같은 ID 재확인, 합성 stdio 입력 전달. Windows importlib/access violation 진단과 Starlette 경고를 함께 기록 |
| 보고서 TOC backend R5 | 새 TOC 39개 + 기존 preview/rich 24개를 일괄 실행: 63 passed / 0 failed / 0 skipped, 24.03초, exit 0 | 취소 ID journal·표준 SHA 계산 전후·보존·단계별 재시도·fresh 상태 fence. native 진단 없는 실행. 페이지 evidence는 합성 값 |
| 신규 source E2E R2 | 프로필 1개 + 목차 6개: 7 passed / 0 failed / 0 skipped, 1.2분, script exit 0 | 실제 rhwp + 모의 보고서, 자동 목차 수렴·rich 보존·재열기·편집 후 갱신·응답 유실·commit 뒤 후속 편집·legacy 정책. `remaining_listeners=[]` |
| 취소 경계 E2E R4 | 1 collected: 1 passed / 0 failed / 0 skipped, 14.7초, script exit 0 | 이전 응답 해제 뒤 완료 장벽을 보완한 취소 응답 유실/다른 preview 경계의 단독 재실행. R2의 7개와 합산하지 않음. `remaining_listeners=[]` |

backend R5 로그는 `.cache/report-toc-targeted-r5-2026-10-12.log`, source R2와 취소 R4는 `.cache/report-toc-source-targeted-r2-2026-10-12` 및 `.cache/report-toc-source-targeted-r4-2026-10-12`에 보존돼 있다. `.cache`의 DB·인증·trace 등은 비민감 코드 인수인계의 제공 대상이 아니다.

앞선 fixture/import 오류, Windows native 진단, 표준 목차 제목 `< 목 차 >`의 공백 기대값 오류와 초기 취소 시험의 관측 장벽 보완은 실패/중간 기록으로 남는다. 수정 전 실패를 최종 통과 수에 넣거나 없었던 일로 지우지 않는다. 계산 후 상태 시험은 두 번째 조회에 바뀐 상태를 관측하도록 제어한 시험이며 실제 동시 PostgreSQL writer 경쟁을 검증했다고 표현하지 않는다.

TM-10 중간 시험의 **새 실제 모델 호출은 0회**다. 실제 rhwp 엔진, 모의 모델/합성 입력, 자동 클릭, 실제 사람 승인, 한컴 실행을 구분한다. 이전 stream/rich의 실제 Codex 호출 증거가 있지만 오늘 또는 TM-10에서 새로 호출한 결과가 아니다. 이번 사람 승인·한컴 검수는 미수행이다.

## 유지해야 할 안전 경계

1. HWPX가 기준이다. 원본·과거 blob을 변경하지 않고 복원도 새 버전을 만든다. 서버 저장은 파일 영속화·DB commit 뒤 확인하며 export를 서버 저장으로 표시하지 않는다.
2. 새 보고서는 [문서 작성 표준](document-writing-standard.md)과 hash-pinned `backend/app/assets/document-standard/reference.hwpx`, `profile.json`, `document_standard.py`를 재사용한다. 제목 파란 위/아래선과 본문 계층을 유지한다.
3. 일반 AI 적용은 사용자 승인 뒤 문서/선택/원문 fence를 갖춘 rhwp exact commands로만 수행한다. 승인 ID·영수증·저장 키를 바꿔 강제 적용하거나 자동 축약하지 않는다.
4. 보고서 자동 서식/목차는 별도 임시 preview에 한정한다. 완료·검증된 내용만 적용하고, 사용자 클릭 `초안 확정·서버 저장` 전에는 문서·버전을 게시하지 않는다. 부분 JSON을 확정 HWPX로 쓰거나 생성 진행을 꾸며내지 않는다.
5. 목차/블록 stage 실패 시 현재 live 어댑터와 미저장 버퍼를 유지한다. stable host와 owner·epoch/revision·SDK 상태·전체 바이트 fence를 통과한 경우에만 채택한다. 늦은 응답은 자신이 시작한 작업만 정리한다.
6. 미해결 저장은 고정한 ID·출력·기준을 재확인한다. 후속 편집을 이전 출력에 섞거나 지우지 않는다. 재시도에도 현재 접근 권한을 검사한다.
7. 기본 엔진 provenance 가드를 낮추지 않는다. 진단 bundle 통과를 정상 배포·인터넷 차단·한컴 호환 완료로 바꾸지 않는다. 신규 작업 큐·자동 병합·자동 모델 대체는 추가하지 않는다.

## 다음 작업과 완료 조건

| 우선순위 | 남은 작업 | 완료로 표시할 근거 |
|---|---|---|
| 1 | TM-10 긴 제목·두 자리 물리 쪽수/10쪽 경계 마감 | 실제 report mock 경로에서 최초 ready 전 수렴·현재 native 페이지 일치·편집 후 갱신을 확인. 기존 TM-09 10쪽 증거로 대체하지 않음 |
| 2 | TM-10 최종 source·lock·시험·사용 엔진 manifest 고정 | 미추적 신규 파일을 포함한 실제 파일 해시 목록과 이전 dc20 대조. Git HEAD만으로 미커밋 실행 집합을 식별했다고 하지 않음 |
| 3 | 같은 최종 변경셋의 전체 회귀 | 전체 pytest, TypeScript, 엔진 보호/콜백, 기본 빌드 가드, 인증 포함 일반 source E2E, 현재 소스로 새로 만든 별도 진단 앱 E2E를 명령별 수집/pass/fail/skip으로 기록 |
| 4 | 보존·확인용 서비스 반영 | 원본/과거 blob·표준 자산·엔진·기존 두 DB를 대조하고 시험한 새 앱과 실제 served 파일 해시 확인. 사용자 미저장 내용을 먼저 보존하며 열린 탭을 임의 새로고침하지 않음 |
| 5 | 실제 업무·한컴 검수 | 사람이 보고서 내용/표준/목차/표·도식을 확인하고 한컴 왕복 보존을 시험. 자동 클릭과 모의 입력으로 사람 승인 성공을 표시하지 않음 |
| 후속 | 임의 마켓 양식의 보고서 프로필 변환·복합 블록/자원 병합 | 스타일/이미지/필드 충돌·비대상 보존·실제 엔진/한컴 호환을 별도 설계/구현/검증 |
| 후속 | 정상 배포·격리·운영 | 기존 보류 범위인 Docker/PostgreSQL/nginx·최적화 provenance·실제 행 잠금 경쟁·백업/복구·SSO/운영 계정·격리 실행 검증. 브랜치 공유로 완료되지 않음 |

오늘의 commit/push에는 누적 소스·lock·시험·규칙·비민감 문서/JSON·합성 PNG와 필요한 표준 원본 자산을 선별한다. `reports/`와 `docs/evidence/*-review-*.zip`은 `.gitignore`에 추가해 로컬에 보존하고 이번 업로드에서 제외한다. 실문서·DB·토큰·인증 파일·PC별 설정·전체 `.cache`도 제공하지 않는다. 기존 사용자 파일·미저장 버퍼·날짜별 증거를 보존하고 새 브랜치만 공유한다. 실제 stage 목록·commit/push 성공 여부는 수행 결과로 확인한다.

개인 PC 절대경로가 있는 2026-10-06 manifest 3개와 이전 GPT 인수인계 2개는 원본을 수정하지 않고 로컬에 보존한다. 공개용 사본은 [publication index](evidence/public/publication-index-2026-10-14.json)에 원본/사본 SHA와 치환 필드를 기록했고, 나머지 내용은 유지했다. 공개 manifest의 치환 필드는 `$.engine_source`다. 공개 사본은 새로운 시험 결과가 아니다. 이번 브랜치의 파일 해시와 준비 점검은 [branch snapshot](evidence/branch-snapshot-2026-10-14.json)에 기록한다. 이 snapshot도 아직 수행하지 않은 TM-10 전체 회귀의 최종 manifest를 대신하지 않는다.

다음 구현자가 처음부터 제공자·후보 계약·실행 슬롯·ORM/권한/임대·frozen 저장을 다시 만들 필요는 없다. 최신 TM-10 변경을 읽고 남은 경계와 같은 변경셋의 검증부터 이어간다. 개발 브랜치의 공유, 기능 구현, 자동 검증, 실제 모델/사람 시험, 운영 반영은 각각 다른 완료 상태다.
