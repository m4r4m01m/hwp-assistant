# HWP Assistant 프로젝트 컨텍스트

갱신일: **2026-10-25 (한국 시간)**. 후속 작업의 통합 기준은 [main `0a92807ab810c9f45c83efe752ddb1eae0a2024a`](https://github.com/m4r4m01m/hwp-assistant/commit/0a92807ab810c9f45c83efe752ddb1eae0a2024a), [문서 PR #36](https://github.com/m4r4m01m/hwp-assistant/pull/36)의 merge commit이다. **운영 이미지 기준은 `49dda2c8de06288111216a61ba720dc148834257` 그대로**이며 #35의 제품 변경과 19:45:45 KST 배포 성공은 [11절](#11-2026-10-25-승인-후-병합과-배포-검증)에 보존한다. 이후 사용자 운영 재시험에서도 `research_unavailable`·완료 검색 0건이 관측돼 실제 검색 복구를 주장하지 않는다. 사용자는 새 요청의 경고 초안 정책과 기존 chat/review의 Codex 연결 개발을 명시적으로 요청했다. 이 후속 작업은 `codex/research-warning-drafts-2026-10-25`의 별도 변경이며 새 main 병합·운영 설정 변경·배포는 아직 승인·실행하지 않았다. 최신 정책·인터페이스·검증 한계는 [12절](#12-2026-10-25-공개자료-조회-미완료-초안-정책)을, 앞선 구현·시험은 [10절](#10-2026-10-25-클라우드-신뢰성-점검)을 따른다. 이 파일은 ChatGPT Work와 Codex가 함께 읽는 인수인계 기준이다. 이후 작업에서는 실제 최신 main·CI·운영 상태를 다시 확인한다.

## 1. 읽는 순서와 증거 기준

1. [루트 AGENTS.md](../AGENTS.md)의 작업·보존 규칙을 읽는다.
2. 이 문서에서 현재 상태·미완료·운영 절차를 확인한다.
3. 해당 기능의 코드와 아래 문서·PR·정확한 커밋의 Actions를 대조한다.

현재 코드/잠금 파일/워크플로와 실행 증거를 우선한다. 날짜가 있는 설계·대화·검증 보고는 당시의 기록이다. 대화의 assistant 완료 보고, CI 성공, 이미지 게시, 서버 배포, 실제 모델 성공, 사람의 호환성 검수는 서로 다른 증거다. 첨부파일 원본이나 로컬 캐시가 없는 기록은 재현 확인한 것으로 표현하지 않는다. 이 문서는 접근 가능한 저장소·이슈/PR·Actions·관련 대화를 조사한 요약이며, 모든 과거 대화/첨부의 완전한 사본은 아니다.

## 2. 목표와 환경 제약

- 바이브 코딩 챌린지/PoC에서 출발한 HWPX 문서함과 AI 작성·편집 지원 도구다. 문서 보존, 비교·승인 후 수정, 기존 한글 작업과의 왕복이 우선이다. [계획 v0.4](internal-doc-assistant-plan-v0.4.md)는 요구사항 기준으로 보존하며 완료 보고로 바꾸지 않는다.
- 개인/공중망 개발·홈서버 PoC와 회사 폐쇄망은 분리한다. 두 망을 연결하거나 내부 문서를 외부 모델에 보내는 전제를 두지 않는다. 외부 개발·시험은 합성 자료를 사용한다.
- 회사 모델 명칭 **“Qwen 3.8”은 사용자 전달 정보**다. 정확한 모델 ID·API·크기·라이선스는 미확인이다. 다른 모델명으로 추정해 바꾸지 않는다. 홈서버 Codex는 인터넷 의존 경로이므로 폐쇄망 AI 검증을 대체하지 않는다.
- 초기 운영은 본인·소수 사용자 시험과 심사 시연을 염두에 뒀다. 최소 비용을 지향하고 유료 AI 자동 대체는 하지 않는다. 하드웨어 증설·처리량·모든 브라우저의 로딩 시간 개선은 확인 없이 보장하지 않는다.
- 실행 환경이 서버의 사설망에 접근하지 못할 수 있다. 접근 실패를 서버 고장으로 단정하거나 SSH 개인키·인증파일 공유로 해결하지 않는다.

## 3. 현재 기능과 아키텍처

| 영역 | 현재 main의 동작과 경계 |
|---|---|
| 문서함·편집 | HWPX 업로드/새 문서/수동 편집/자동 저장/다른 이름 저장/다운로드. 원본·과거 버전은 불변이며 복원도 새 버전이다. HWPX가 정본이다. |
| 저장·권한 | FastAPI + SQLAlchemy + 파일 저장소. 문서 행 잠금, ACL, 편집 임대·기준 버전, 멱등 키를 검사하고 파일 영속화와 DB 확정 후 저장 완료를 알린다. 다운로드는 서버 저장과 다르다. |
| 버전 비교 | 실제 페이지와 문단·서식·표 변경, 수정 계정·중간 저장 이력 비교. 두 버전을 순서대로 선택한다. 비교가 자동 병합을 제공하지는 않는다. |
| AI 문단 수정 | 미저장 원문을 포함한 고정 스냅샷 → 두 후보 → 비교/승인 → 문서·선택·원문 fence 재검사 → rhwp 명령 적용 → 새 버전 저장. 안전 적용 가능 범위 밖은 거부한다. |
| 보고서 | 실제 Codex app-server delta, 초안 내용 검토·별도 보완 작업, 문서 표준/별첨 1~3/실제 렌더링 쪽수 목차, 표·편집 가능한 흐름도. final/legacy 완료 메시지와 turn 완료 summary를 검증하며 async/commentary·부분 delta는 표시 전용이다. 실패 종류·단계·완료 검색 수를 구분한다. 현재 main/운영의 research=true·완료 검색 0건은 실패 정책이다. 새 요청의 경고 초안 변경은 미병합 후속 작업인 12절을 따른다. 완료·검증된 draft만 임시 미리보기에 적용하고 사용자가 확정·서버 저장한다. 실제 운영 웹 검색 복구는 미확인이다. |
| 문서 대화·컨텍스트 | 권한 있는 문서의 사실·작성 기준·선택 원문과 대화/후보/적용 이력. 문서 독자도 모든 작성자의 대화를 읽을 수 있으나 승인·편집·저장은 불가하다. 작성자 전용 비공개 대화로 오해하지 않는다. |
| 기존 스레드 chat·AI review | 현재 main의 두 경로는 기존 HTTP provider를 사용한다. 후속 작업은 `HWP_CHAT_PROVIDER`/`HWP_REVIEW_PROVIDER`로 Codex를 명시 선택할 수 있도록 개발한다. native·Compose 기본은 `llm`이며 기존 HTTP 선택을 유지한다. 후속 개발을 운영 활성화로 표현하지 않는다. |
| 양식·문서 표준 | HWPX 양식 등록·검색·새 문서 생성, 제한된 제목/요약/담당·기한 블록, 표준 편집·검증된 예시·등록. 고정 기준서와 과거 표준/초안은 덮어쓰지 않는다. |
| 계정 | 선택적으로 비밀번호 로그인/가입/guest, 기존 토큰·만료/폐기 가능한 세션과 공존. 운영자가 지정한 샘플 계정 선택은 기억한 ID와 별개이며 기본 목록은 비어 있다. 선택 후 해당 계정 비밀번호가 필요하다. AI는 별도 ACL이고 같은 guest/샘플 ID 이용자는 문서 주체를 공유한다. 운영 계정 설정은 이번에 변경하지 않았다. |
| 반응성 | 정적 압축/해시 캐시·검색·모바일 패널, 로그인 화면의 낮은 우선순위 SRI 자원 다운로드와 제한된 WASM 사전 컴파일. 로그인은 준비를 기다리지 않는다. 다운로드·컴파일·실제 엔진 초기화/문서 배치를 구분하며 브라우저 코드 캐시 재사용이나 최초 문서 지연 개선을 보장하지 않는다. |

`backend/app`가 인증·저장·AI 작업을, `frontend/src`의 React/TypeScript와 `EditorAdapter`가 UI·편집기를 담당한다. rhwp 0.8.6은 [engine lock](../scripts/engine-lock.json), 소스 패치와 [bridge](../scripts/assistant-bridge.ts.txt)로 고정한다. npm 원본 WASM이나 출처 없는 빌드로 바꾸지 않는다. 기본 release 빌드는 패치·해시·실제 엔진 검사·고지·출처를 확인하며 진단 코어를 거부한다.

네이티브 기본 저장소는 SQLite `data/metadata.db`와 `data/files/`다. 실행기가 별도 경로를 지정할 수 있으므로 항상 실제 `DATABASE_URL`/`STORAGE_DIR`을 확인한다. 과거 Windows `.cache`·포트·바로가기를 새 PC에 존재하는 경로로 가정하지 않는다. 운영은 PostgreSQL과 파일 볼륨, **db/api/web 3개 서비스·API 1 worker**다. worker/서버를 늘리려면 프로세스별 로그인 제한·작업 용량 제어를 공유 저장소로 옮기는 설계가 필요하다.

## 4. 주요 연혁과 결정 근거

아래 날짜는 KST 기준이다. 세부 구현·증거는 링크한 원문을 보존한다.

| 시점 | 작업·결정·검증 |
|---|---|
| 2026-09-23~27 | 요구사항·망 분리·비용/보존 원칙 확정, [PR #19](https://github.com/m4r4m01m/hwp-assistant/pull/19) 초기 구현. 실제 Codex+MCP의 사람 승인·저장과 한컴 2018 단순 문단/2×2 표 일부 왕복 확인. [9/27 검증](verification-2026-09-27.md). |
| 2026-10-06 | 로컬/사내 OpenAI 호환 HTTP 모델을 문단 두 후보 흐름에 연결, 늦은 응답/재시작/원문 경계 보강. 실제 rhwp + **모의 HTTP 모델**, 웹 저장·재가져오기 3회 확인. 실제 사내 모델은 미연결. [10/06 검증](verification-2026-10-06.md). |
| 2026-10-07~18 | 보고서 기본 서식·실제 스트리밍·표/흐름도·양식/블록·목차·문서 표준·개발 실행기 확장, [PR #20](https://github.com/m4r4m01m/hwp-assistant/pull/20). [10/17 인수인계](development-handoff-2026-10-17.md), [10/18 검수 안내](implementation-status-and-user-review-2026-10-18.md). |
| 2026-10-19~23 | 버전 비교·수정자 추적, 내용 검토 후 별도 보고서 보완, 문서함/표준 메모. [버전 비교](version-comparison.md), [검토 흐름](report-review-workflow-2026-10-20.md), [메모 인수인계](development-handoff-library-notes-2026-10-23.md). |
| 2026-10-22 | [#21](https://github.com/m4r4m01m/hwp-assistant/pull/21) Funnel 설계 → [#22](https://github.com/m4r4m01m/hwp-assistant/pull/22) 운영 Compose/23000/하위 경로 파이프라인 → [#23](https://github.com/m4r4m01m/hwp-assistant/pull/23) programmatic Vite build의 base 누락 회귀 수정. 개발 루트 경로와 운영 접두 경로를 각각 검사한다. |
| 2026-10-23 | [#24](https://github.com/m4r4m01m/hwp-assistant/pull/24) 반응성·세션·모바일·보안, [#25](https://github.com/m4r4m01m/hwp-assistant/pull/25) QA 통합 및 권한/예산/비동기 회귀 보완. [별도 로컬 환경 검증](development-handoff-main-adoption-2026-10-23.md)은 실제 모델 3회/자동 승인 시험을 기록하며 사람 검수와 구분한다. |
| 2026-10-23 운영 관측 | “서버 통신 성능 점검” 대화에서 사용자는 호스트 nginx upstream을 HTTP/1.1로 바꾸고 재시작했다고 보고했다. 이후 압축 전달이 개선됐다는 첨부 기반 분석은 과거 관측이며 이번에 원 첨부를 재검증하지 않았다. 압축 전송량·WASM 초기화·문서 배치 시간을 구분하며 캐시/prewarm 효과를 측정한다. |
| 2026-10-24 | [#26](https://github.com/m4r4m01m/hwp-assistant/pull/26) 로컬 runner 자료 제외, [#27](https://github.com/m4r4m01m/hwp-assistant/pull/27) 검증·게시된 이미지 재빌드 없는 배포. 최신 main 태그가 아니라 실제 게시 run의 SHA를 사용한다. |
| 2026-10-24 | [#29](https://github.com/m4r4m01m/hwp-assistant/pull/29) 표준 편집/공통 업무 예시/검증 미리보기, 당시 백엔드 903개. 사용자는 브랜치 계보가 보이는 merge commit을 선호한다. squash 뒤 [동일 tree 이력 연결](https://github.com/m4r4m01m/hwp-assistant/commit/1b4e1a054c47e4ee4087b3f8cea1b0cfadf032e5)을 했으며 후속 통합은 이력을 보존한다. |
| 2026-10-24 | [#30](https://github.com/m4r4m01m/hwp-assistant/pull/30) 재시작 후 HTTPS 502: gateway overlay/Docker DNS 재조회/readiness 보완. readiness는 읽기만 재시도하고 가입·업로드 POST를 통째 재실행하지 않는다. |
| 2026-10-25 | [#31](https://github.com/m4r4m01m/hwp-assistant/pull/31), `0c65905`: Codex runtime·비밀번호 계정·로그인 시 WASM 사전 다운로드, 백엔드 **941개** 및 병합 후 CI 통과. 해당 작업의 실제 모델 호출은 0회. |
| 2026-10-25 | [#32](https://github.com/m4r4m01m/hwp-assistant/pull/32), `cbeb74f`: #31과 **파일/tree 동일**, 기존 구현 브랜치의 이력 연결. 새 제품 변경으로 중복 계산하지 않는다. |
| 2026-10-25 | [#33](https://github.com/m4r4m01m/hwp-assistant/pull/33), `95eb0da`: QA와 최신 계정/권한/한도 통합, AI 연결 안내·디자인 조각 소유권/동시 저장·요일 보존·등록 재시도·표지/목차/표준 설정 보강. 백엔드 **1,021개**로 증가. |
| 2026-10-25 17:42 | [수동 배포 run 40177050099](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099)에서 #33 SHA의 검증·이미지 게시·홈서버 배포·공개 경로 검사 성공. 서버 device login/실제 모델 응답 완료를 뜻하지 않는다. |
| 2026-10-25 | [#34](https://github.com/m4r4m01m/hwp-assistant/pull/34), `295b6e1`: Work/Codex 공통 인수인계 문서만 통합. 당시 제품 코드는 #33과 동일했다. [수동 run 40180482900](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40180482900)은 18:33:29 KST에 배포 job 성공으로 종료했다. |
| 2026-10-25 19:34 | [#35](https://github.com/m4r4m01m/hwp-assistant/pull/35)의 검토 head `f7a1da1`을 사용자 승인 후 merge commit `49dda2c`으로 통합했다. 보고서 완료/실패 진단·프로세스 정리·WASM 준비·선택적 샘플 계정 변경이다. source 브랜치를 유지한다. |
| 2026-10-25 후속 | 정확한 main `49dda2c`의 push 검사 3개 성공. 별도 승인된 [수동 run 40185409373](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185409373)을 19:35:34 KST에 한 번 dispatch했다. 이 단계에서 이미지 게시·운영 배포 완료는 미확정이며 [11절](#11-2026-10-25-승인-후-병합과-배포-검증)로 이어진다. |
| 2026-10-25 19:45 | 같은 수동 run의 이미지 게시·홈서버 배포가 성공했고 deploy-home이 19:45:45 KST에 종료했다. 운영 이미지 source는 `49dda2c`다. 실제 검색 복구와 구분한다. |
| 2026-10-25 후속 문서 | [#36](https://github.com/m4r4m01m/hwp-assistant/pull/36)의 head `4f5df8d12e1d0a2756289461e105fe75b7c434dc`를 merge commit `0a92807ab810c9f45c83efe752ddb1eae0a2024a`으로 통합했다. context·배포 영수증·샘플 JSON 예시의 문서 3개만 변경했으며 운영 이미지를 재게시·재배포하지 않았다. |
| 2026-10-25 후속 요청 | 사용자 운영 재시험에서 research=true·완료 검색 0건·`research_unavailable`이 지속됐다. 새 요청의 경고 초안 기본 정책과 기존 chat/review의 홈서버 Codex 연결 개발을 명시적으로 요청해 별도 브랜치에서 진행한다. [12절](#12-2026-10-25-공개자료-조회-미완료-초안-정책)의 새 작업이며 새 배포 승인은 아니다. |

## 5. 검증된 상태와 아직 확인할 것

**후속 작업의 기준 main 검증:** 문서 PR #36의 merge commit `0a92807ab810c9f45c83efe752ddb1eae0a2024a`에서 [HWP deployment 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40186938780), [frontend build path](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40186938847), [deployment-only 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40186938745) **3개 workflow 성공**을 확인했다. 새 경고 초안/chat·review 변경을 이 기준 SHA의 성공으로 검증했다고 계산하지 않는다. 해당 변경의 정확한 code head·시험·CI는 12절에 별도로 기록한다.

**직전 제품 main 검증:** #35 merge commit `49dda2c8de06288111216a61ba720dc148834257`의 [HWP deployment 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185311735), [frontend build path](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185311749), [deployment-only 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185311706)가 성공했다. [Python job](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185311735/job/121144749249)은 **1,071 passed, 2 warnings, 316.99초**다. 일반 push에서 이미지 게시·운영 배포 job 2개는 정상 skip됐다. 정확한 PR head의 별도 컨테이너 통합 성공과 승인된 수동 배포 run 상태는 [11절](#11-2026-10-25-승인-후-병합과-배포-검증)을 따른다.

**과거 #33 검증:** [HWP deployment 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174658302), [frontend build path](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174657544), [deployment-only 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174657560)는 당시 성공했다. 아래 #33/#34 수치·운영 기록을 현재 main의 시험 수나 새 배포 완료로 대체해 읽지 않는다.

**과거 #34 배포 확인:** 정확한 `295b6e1`에서 [수동 run 40180482900](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40180482900)의 검사·게시·홈서버 배포 5개 job이 성공했고, 배포 job 종료는 **2026-10-25 18:33:29 KST**였다. Python 1,021개, frontend unit 26개, mocked UI 35개, TLS 1개, 배포 브라우저 2 passed/1 skipped가 로그에 기록됐다. 같은 SHA의 push 검사도 성공했으며 게시·배포 2개 skip은 정상이다. 아래 #33 기록 이후의 운영 증거이며 실제 Codex 모델 성공을 증명하지 않는다.

후속 [수동 run](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099)의 [Python job](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099/job/121119882634)에서 **1,021 passed, 2 warnings**를 확인했다. 경고는 의존성의 httpx/TestClient·anyio portal 폐기 예정 API다. [게시 job](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099/job/121120768129)과 [홈서버 배포 job](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099/job/121121722084)도 성공했다. 이는 해당 시점/SHA의 기록이며 이후 서버 설정까지 보장하지 않는다.

| 구분 | 확인된 범위 / 제한 |
|---|---|
| #33 PR 기록 | frontend unit 26, Node 계약 82, mocked UI 35. 실제 고정 rhwp 시나리오 고유 74개 통과/조건부 skip 6개. 모의 AI와 실제 모델을 혼동하지 않는다. |
| 계정·실제 컨테이너 | [PR 통합 run](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174233009): PostgreSQL/API/web/신뢰한 HTTPS, guest·개인 문서 저장/재열기, 바이트 무결성·격리, 세션/재로그인·API 재시작, 실제 자원 캐시 재사용. CI에서 Codex 로그인이나 모델 호출은 하지 않는다. |
| 실제 모델 | 9/27과 10/23의 별도 로컬 기록은 존재한다. **최신 홈서버 계정/세 기능 조합의 실제 모델 성공은 미확인**이다. |
| 한컴·브라우저 | 과거 한컴 2018 단순 문서/표 일부 왕복만 확인. 125% 편집 화면 표 외곽선 시각 차이, 복잡한 그림/필드/반복 왕복, 최신 Windows·한컴·Safari/Firefox 실기기는 추가 검수 대상이다. |
| 폐쇄망·복구·부하 | 실제 사내 모델·외부 차단 신규 세션 전체 경로, 격리 환경 복원 리허설, 디자인 조각 동시 저장의 PostgreSQL 부하 검증은 미완료다. |

조사 시점 standalone issue는 반환되지 않았고, 열린 [PR #28](https://github.com/m4r4m01m/hwp-assistant/pull/28)은 **Draft·미병합**이다. 외부 LLM adapter/HTTP 보고서 provider를 main 기능으로 설명하거나 그 코드가 배포됐다고 가정하지 않는다.

## 6. 운영: 계정·Codex·환경변수

### 6.1 마지막 사용자 보고와 원인

이 절의 ACL/login 관측은 #34 문서화 당시 기록이다. 후속 2026-10-25 사용자 인계에서는 운영 Codex 로그인·허용이 확인됐고 보고서 검색 완료 0건 문제가 관측됐다. [10절](#10-2026-10-25-클라우드-신뢰성-점검)의 후속 근거를 우선하며, 아래 미완료 표현을 현재 로그아웃 상태로 단정하지 않는다.

사용자는 운영 API에서 `HWP_CODEX_USER=local`, `HWP_CODEX_ALLOWED_USERS` 빈 값과 “서버에서 Codex ChatGPT 로그인이 필요합니다” 안내를 확인했다. 이어 **`/opt/hwp-assistant/production.env`에는 두 ACL 키 자체가 없다**고 정정했다. 로그인/허용목록 변경 완료 보고는 없다. 이번 문서화 작업에서 서버의 private env·인증파일을 열거나 설정을 변경하지 않았다.

이는 [Compose](../compose.deploy.yaml)의 `${HWP_CODEX_USER:-local}`, `${HWP_CODEX_ALLOWED_USERS:-}` 기본값과 일치한다. **파일에 키가 있어야 컨테이너에 값이 생기는 구조가 아니다.** 셸 환경변수도 Compose 보간에 영향을 준다. 파일 전체나 expanded `docker compose config`를 출력하지 말고 필요한 비밀 아닌 항목만 점검한다.

[실제 ACL 코드](../backend/app/codex_runtime.py): 허용목록이 비면 `HWP_CODEX_USER` 하나, 비어 있지 않으면 쉼표 목록만 사용한다. `local`도 유지하려면 목록에 포함한다. `*`/끝 쉼표는 유효하지 않다. ACL은 **앱 ID**이며 ChatGPT 이메일·회원 생성·Codex 로그인이 아니다. `guest` 허용 시 동일 guest로 접속하는 모든 체험자에게 적용된다.

### 6.2 저장 위치와 계정 관리

| 정보 | 위치·보존 원칙 |
|---|---|
| 비밀번호 회원·guest | PostgreSQL의 `hwp_assistant` DB → `public.user_accounts` 테이블, salted scrypt 해시. 개별 계정 JSON 파일이 아니다. |
| DB·브라우저 세션·사용량 | `hwp-assistant-prod-metadata` → DB `/var/lib/postgresql/data`. `auth_sessions`, `codex_usage`도 DB에 영속화된다. |
| 기존 자동화 토큰 | private env의 `HWP_AUTH_TOKENS`. 비밀번호 계정/세션과 별개다. 토큰 원문은 기록하지 않는다. |
| 원본·버전 파일 | `hwp-assistant-prod-documents` → API `/data/files`. DB와 함께 백업한다. |
| Codex ChatGPT 인증 | `hwp-assistant-prod-codex-state` → API `/home/app/.codex` (`CODEX_HOME`). device login 후 private `auth.json` 생성. 내용·일회용 코드·인증 URL의 비밀값을 공유하지 않는다. |
| 배포·백업 | `/opt/hwp-assistant/state/last-success.json`, `/opt/hwp-assistant/backups/`. env와 Codex 인증은 DB/파일 자동 백업에 포함하지 않는다. |

계정 기능/가입은 각각 `HWP_ACCOUNTS_ENABLED`/`HWP_REGISTRATION_ENABLED`로 명시적으로 켜며 기본값은 0이다. 공개 가입은 guest/local·토큰/AI 허용 ID·과거 문서 주체를 예약해 선점을 막는다. 사전 허용 ID는 운영자 CLI로 만든다. `local`은 예약 ID라 비밀번호 계정으로 만들 수 없다. 아래 예시는 실제 인적정보가 아닌 합성 계정이다.

```sh
docker exec -it hwp-assistant-prod-api-1 python -m app.account_admin create \
  --user sampleuser --name '테스트 사용자' --employee TEST-001
docker exec -it hwp-assistant-prod-api-1 python -m app.account_admin password --user sampleuser
docker exec -it hwp-assistant-prod-api-1 python -m app.account_admin disable --user sampleuser
```

비밀번호는 터미널에서 두 번 입력하며 명령줄 인자로 주지 않는다. `create`는 기존 계정을 덮어쓰지 않는다. 재설정/활성 상태 변경은 비밀번호 세션을 무효화한다. 비밀번호 재설정은 별도 토큰을 교체하지 않으며, 비활성화는 해당 계정의 토큰 접근도 막는다. 사번 입력은 재직 확인/SSO가 아니다. 비밀번호 분실 복구 웹 UI는 없다.

호스트 경로가 필요하면 `docker volume inspect <위 볼륨명> --format '{{.Name}} -> {{.Mountpoint}}'`로 해당 볼륨만 확인한다. 실행 중 PGDATA나 인증파일을 직접 편집하지 않는다. 회원 점검은 필요한 최소 컬럼/집계만 조회하고 이름·사번·해시를 결과물로 옮기지 않는다.

### 6.3 서버 Codex 로그인과 실제 AI 확인

서버의 해당 컨테이너에 로그인해야 한다. 개인 PC/호스트의 Codex 인증이 자동 공유되지 않는다.

```sh
docker exec -it hwp-assistant-prod-api-1 codex login --device-auth
docker exec hwp-assistant-prod-api-1 codex login status
```

일회용 코드는 본인이 브라우저에서 처리하고 대화/PR에 붙이지 않는다. 앱은 `Logged in using ChatGPT` 상태를 확인하며 점검은 최대 5초/캐시 15초다. 인증파일 변경만으로는 이미지 재빌드가 필요하지 않다. 로그인 확인 후 새로고침하고 **합성 자료로 보고서·문서 대화·문단 수정**을 각각 완료까지 시험한다. 문단 수정은 후보 비교/승인·저장·재열기, 보고서는 검토·서식·확정 저장·재열기, 대화는 문서/버전 불변을 확인한다.

서버 인증 하나를 명시적으로 허용한 앱 계정들이 사용한다. 사용자별 Codex 인증 분리는 미구현이다. 앱 ACL은 서비스 이용 조건에 대한 허가를 대신하지 않는다. [기존 PoC 설계](poc-codex-accounts-prewarm-2026-10-25.md)의 이용 범위 구분도 확인한다.

운영 Compose 기본은 공통 동시 AI 작업 **1건**, DB 일일 cap **전체 합산 60건**, UTC 자정=한국 09:00 갱신이다. 구독 잔량이나 1인당 보장량이 아니다. 접수 후 실패/불확실 결과도 사용량을 소비하고 같은 작업 결과 재조회는 새 호출로 세지 않는다. 네이티브 기본 cap 0과 구분한다. 인증·한도·네트워크 실패에도 편집/저장은 유지한다. `HWP_CODEX_MODEL`/실행 설정을 확인하기 전 특정 모델이 선택됐다고 단정하지 않는다.

### 6.4 환경변수만 바꿀 때: 현재 이미지로 API 재생성

운영 파일을 보호된 위치에 백업하고 원하는 ACL 키를 **추가 또는 기존 줄 수정**한다. 예를 들어 `HWP_CODEX_ALLOWED_USERS=local,guest`는 두 앱 ID의 실행을 허용하지만 계정을 만들지는 않는다. 운영자가 원하는 실제 ID를 정하고 계정 존재/이용 범위를 확인해야 한다. 기존 DB 비밀번호·토큰·소유권·포트는 보존한다.

환경변수 변경은 `docker restart`로 적용되지 않는다. 진행 중 작업·미저장 탭을 정리하고 [백업 절차](deployment-runbook.md)를 수행한 뒤, **현재 배포에 사용한 Compose 파일**과 **실행 중인 이미지 SHA**로 API만 재생성한다. 새 main SHA를 이미지 태그로 추정하지 않는다. 아래는 기존 운영 이름/태그 형식의 서버에서 실행하는 절차이며 이 문서화 작업에서 실행한 명령이 아니다.

```sh
(
  set -eu
  api_container=hwp-assistant-prod-api-1
  deployed_dir="$(docker inspect "$api_container" --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}')"
  deployed_image="$(docker inspect "$api_container" --format '{{.Config.Image}}')"
  deployed_image_id="$(docker inspect "$api_container" --format '{{.Image}}')"
  cd "$deployed_dir"
  test -f compose.deploy.yaml
  export HWP_IMAGE_PREFIX="${deployed_image%-api:*}"
  export HWP_IMAGE_TAG="${deployed_image##*:}"
  test "${#HWP_IMAGE_TAG}" -eq 40
  case "$HWP_IMAGE_TAG" in *[!0-9a-f]*) exit 1;; esac
  test "$deployed_image" = "$HWP_IMAGE_PREFIX-api:$HWP_IMAGE_TAG"
  test "$(docker image inspect "$deployed_image" --format '{{.Id}}')" = "$deployed_image_id"
  # 현재 checkout이 실행 중인 이미지의 배포 source와 일치해야 한다.
  test "$(git rev-parse HEAD)" = "$HWP_IMAGE_TAG"
  git diff --quiet HEAD -- compose.deploy.yaml
  unset HWP_CODEX_USER HWP_CODEX_ALLOWED_USERS
  docker compose --env-file /opt/hwp-assistant/production.env \
    -p hwp-assistant-prod -f compose.deploy.yaml \
    up -d --no-deps --no-build --pull never --force-recreate \
    --wait --wait-timeout 180 api
)
```

파일/checkout/태그가 다르면 중단하고 배포 영수증의 source와 일치하는 보존 checkout을 찾는다. 다른 셸 변수나 `.env`가 추가로 덮어쓰는지도 확인한다. 계정 기능 등 다른 키를 변경한다면 그 키의 셸 override도 제거한다. 재생성 후 아래 두 비밀 아닌 ACL 값과 로그인 상태, 앱의 권한/실제 작업을 확인한다.

```sh
docker exec hwp-assistant-prod-api-1 sh -c \
  'printf "HWP_CODEX_USER=%s\nHWP_CODEX_ALLOWED_USERS=%s\n" "$HWP_CODEX_USER" "$HWP_CODEX_ALLOWED_USERS"'
docker exec hwp-assistant-prod-api-1 codex login status
```

### 6.5 배포와 장애 복구

- [게시 이미지 배포](deploy-published-images.md)는 성공한 게시 run과 main 계보를 검증하고 그 **원래 source SHA**를 체크아웃한다. 문서 PR 병합만으로 이미지를 다시 만들거나 서버를 바꾸지 않는다.
- 기존 서버에서 `scripts/deploy/init.py`를 재실행하지 않는다. `deploy.sh`는 필요한 새 Codex 볼륨만 보충하며 기존 DB/문서를 보존한다. 운영 Compose와 개발 Compose는 합치지 않는다. API/DB/RPC 포트를 외부에 열지 않는다.
- 백업은 앱 writer를 멈추고 DB custom dump·문서 archive·해시·영수증을 일관된 세트로 만든다. private env는 별도 보호 백업, Codex 인증 소실은 재로그인으로 복구한다. 브라우저 미저장 버퍼는 서버 백업 대상이 아니므로 복구용 다운로드로 보존한다.
- 백업 실패 시 교체를 중단하고 기존 앱을 재개한다. 기동 실패 시 DB 삭제/자동 downgrade를 하지 않는다. 이전 이미지의 스키마 호환성을 확인하거나 **별도 새 볼륨/DB**에 복원을 검증한 후 전환한다. 원본 운영 볼륨 삭제·전체 prune·실행 중 PGDATA 복제는 금지한다.
- 재시작 후 502는 먼저 해당 gateway/하위 경로/readiness·DNS를 점검한다. 로그인·업로드·가입을 반복 POST해 복구하려 하지 않는다. [#30](https://github.com/m4r4m01m/hwp-assistant/pull/30)의 실제 수정과 현재 workflow를 기준으로 판단한다.

## 7. 다음 작업과 알려진 한계

1. 후속 사용자 운영 재시험에서도 research=true 검색 복구는 확인되지 않았다. [12절](#12-2026-10-25-공개자료-조회-미완료-초안-정책)의 새 경고 초안과 chat/review Codex 연결을 검증한 뒤 실제 운영 적용 여부를 별도로 판단한다. 내용 검토·서식·명시적 저장 경계와 기존 보고서·문서 대화·문단 수정의 권한을 유지하며 합성 자료로 검수한다. 인증파일이나 private env를 수집하지 않는다.
2. 최신 Windows/한컴·Safari/Firefox 실제 장치, 복잡한 문서 왕복과 표 외곽선 시각 차이를 검수한다. 일반 HWPX 왕복 결함이 블록 보호 기능으로 해결됐다고 표현하지 않는다.
3. 개발자 엔진 kit와 현재 bridge의 불일치를 해소·게시하고 fresh clone 설치를 재검증한다. 기존 kit로 모든 환경에서 원클릭 실행된다고 보장하지 않는다.
4. 사내 모델의 정확한 계약을 확인하고, 의존성/이미지/폰트 사전 반입 → 외부 통신 차단 → 새 브라우저 → 내부 실모델 → 편집/승인/저장/복원 전체 흐름을 별도로 검증한다.
5. 백업의 다른 장치 보관·격리 복원 리허설과 PostgreSQL 동시 저장 부하를 확인한다. 다중 worker, SSO, 운영 계정 웹 관리·분실 복구, 실시간 공동편집/자동 병합은 완료 범위가 아니다.
6. [PR #28](https://github.com/m4r4m01m/hwp-assistant/pull/28)은 별도 검토를 유지한다. 원래 사용자가 요청한 Draft 분리를 존중하고 이 문서화 PR에 제품 코드를 섞지 않는다.

AI 출력 계약/원문 검사는 사실성·회사 규정 적합성의 자동 판정을 뜻하지 않는다. 표 셀 수동 편집과 AI 안전 적용은 범위가 다르다. 일반 저장 실패 큐는 브라우저 종료 후 완전 복원된다고 보장하지 않는다.

## 8. 기존 문서 인덱스와 상충 해석

| 주제 | 우선 참조 |
|---|---|
| 요구사항·전체 상태 | [계획 v0.4](internal-doc-assistant-plan-v0.4.md), [10/18 검수 안내](implementation-status-and-user-review-2026-10-18.md), 이 문서 |
| 보고서·표준 | [문서작성 표준](document-writing-standard.md), [내용 검토/보완](report-review-workflow-2026-10-20.md), [실제 streaming](report-stream-2026-10-08.md), [표/흐름도](rich-report-2026-10-08.md), [양식/목차](report-template-toc-2026-10-12.md) |
| 후속 정책·연결 | [공개자료 조회 미완료 경고 초안](report-research-warning-2026-10-25.md), [기존 chat/review의 Codex 선택](codex-chat-review-2026-10-25.md). 기준 main 이후 별도 작업으로, 해당 exact head의 검증·승인·배포 상태를 확인한다. |
| 권한·AI·저장 | [협업/MCP](collaboration-and-mcp.md), [로컬 모델](local-model-rewrite.md), [원문 경계](rewrite-boundaries-verified-2026-10-06.md), [버전 비교](version-comparison.md), [저장소 점검](storage-audit.md) |
| 설치·엔진 | [개발자 quickstart](developer-quickstart.md), [엔진 패치 빌드](engine-empty-fill-fix.md), [엔진 통합](engine-integration.md) |
| 운영 | [runbook](deployment-runbook.md), [첫 연결](deployment-first-activation.md), [게시 이미지 배포](deploy-published-images.md), [Codex/계정/prewarm](poc-codex-accounts-prewarm-2026-10-25.md), [반응성/보안](responsiveness-security-2026-10-22.md) |
| 과거 증거 | [9/27](verification-2026-09-27.md), [10/06](verification-2026-10-06.md), [10/23 로컬 실제 모델](development-handoff-main-adoption-2026-10-23.md), [호환성](compatibility.md), [커밋된 evidence](evidence/) |

- [remaining-work.md](remaining-work.md)의 10/06 배포 보류·PostgreSQL 미검증·보고서/블록 미구현은 **그 당시** 상태다. 위 PR/CI로 확인한 구현·배포 현황이 현재 기준이다.
- README·PoC 설계의 “검토 브랜치/미병합”과 초기 runbook의 “배포 미실행” 범위는 해당 작성 시점 기록이다. #31~33 및 후속 실제 배포 Actions를 함께 읽는다.
- 협업/MCP 문서의 과거 “가입/블록 없음”·MCP 선택 설치 설명을 현재 Docker 동작으로 복제하지 않는다. native에서 선택 설치였던 MCP는 현재 API 이미지에 포함된다. production의 토큰 최소 길이도 native와 다를 수 있어 실제 검증 코드를 따른다.
- 날짜가 붙은 Windows 경로·포트·성능 수치·테스트 수는 당시 환경의 증거다. 이 문서의 최신 source 밖에 자동 적용하지 않는다.

조사한 관련 대화(접근 권한 있는 계정에서만 조회 가능, 공개 GitHub 증거와 구분):

- [세 기능 통합 검증](https://chatgpt.com/c/6adcd8a9-e108-83ec-9b15-faccb3e497b9): 서버 로그인 필요·ACL 결과·env 키 부재의 사용자 보고, 장기 인수인계 요청.
- [브랜치 검증 및 병합](https://chatgpt.com/c/6addaa65-8a5c-83e8-8c4a-14606abdd70d), [브랜치 병합 및 테스트](https://chatgpt.com/c/6adc41d2-2da4-83ee-9e41-6ec9ee14d8b5): 이력 보존 선호와 #29~33 통합 맥락.
- [LLM API 연동 방향 제안](https://chatgpt.com/c/6adbc8c3-bb98-83e8-be28-519872884aef), [성능 보안 UI 점검 & 자동 배포 환경 구성](https://chatgpt.com/c/6ad9c305-73d0-83e8-9d10-b8c506dbd87b), [작업 재개 요청 & 보완사항 제안 요청](https://chatgpt.com/c/6adb14e2-3898-83ee-90f0-a8551bc1c9bb): 외부 adapter 초안과 배포/QA 분리.
- [서버 통신 성능 점검](https://chatgpt.com/c/6adb043c-89e4-83ee-8d95-584199ed26ea), [최근 작업 확인](https://chatgpt.com/c/6ad8c09b-819c-83ec-ae06-7488be0e9ea5), [깃헙 레포 작업사항 읽기](https://chatgpt.com/c/6abb24a0-a634-83ee-844a-234f4be1708d): 초기 운영/네트워크 관측. 원 첨부를 이번에 재검증한 것으로 보지 않는다.

## 9. ChatGPT Work와 Codex에서 다시 사용하는 방법

이 파일 저장은 **ChatGPT 계정 Memory를 강제로 저장하거나 다른 도구로 자동 공유하는 작업이 아니다.** 이 환경에는 계정 메모리 쓰기 도구가 없고, Codex가 ChatGPT의 저장된 기억을 자동 상속한다고 가정하지 않는다. 대신 버전 관리되는 두 파일을 공통 근거로 사용한다.

**Codex:** 최신 main을 보존 방식으로 가져온 저장소를 프로젝트로 열고 새 작업을 시작한다. 루트 `AGENTS.md`는 Codex의 프로젝트 지침 발견 대상이며, 여기서 이 파일을 먼저 읽도록 연결했다. 새 세션에서 “적용 중인 지침 파일과 PROJECT_CONTEXT의 기준 SHA, 현재 branch/HEAD를 먼저 확인해 줘”라고 요청한다. 상위/하위 `AGENTS.override.md`나 개인 지침이 있으면 우선순위를 확인한다. 상세 문서가 자동 전부 주입된다고 가정하지 않는다. [공식 AGENTS.md 발견/확인 안내](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

**ChatGPT Work:** 이 private GitHub 저장소에 권한이 있는 연결 도구로 최신 main의 `AGENTS.md`와 `docs/PROJECT_CONTEXT.md`를 먼저 읽도록 요청한다. 연결이 없는 환경에서는 두 파일을 Work 프로젝트/대화의 파일로 첨부하고 **기준 SHA도 함께 전달**한다. 첨부본은 자동으로 GitHub 최신본과 동기화되지 않으므로 다음 작업 때 갱신한다. 단순 로컬 파일 경로나 private 링크만으로 접근할 수 있다고 가정하지 않는다. [Work의 파일·프로젝트 활용](https://learn.chatgpt.com/docs/web), [컨텍스트 접근 범위](https://learn.chatgpt.com/docs/enterprise/work-admin-faq).

재개 요청 예시:

> `m4r4m01m/hwp-assistant` 최신 main의 `AGENTS.md`와 `docs/PROJECT_CONTEXT.md`를 먼저 읽고 기준 SHA와 실제 HEAD를 대조해 줘. 현재 코드·정확한 Actions 결과를 우선하고, 과거 검증/현재 운영/미확인을 구분해 이어서 작업해 줘. 비밀정보와 실제 회사 자료는 가져오지 마.

의미 있는 결정·병합·실행 검증 후에는 이 문서의 갱신일·source SHA·PR/Actions·관측 환경·미완료·다음 단계를 함께 갱신한다. 테스트 숫자만 올리거나 오래된 운영 상태를 그대로 복제하지 않는다.

## 10. 2026-10-25 클라우드 신뢰성 점검

이 절은 **PR #35를 준비하고 검토하던 시점**의 구현·검증·운영 판단 기록을 보존한다. 이후 승인된 병합과 배포 진행은 [11절](#11-2026-10-25-승인-후-병합과-배포-검증)을 우선한다. [클라우드 검증 영수증](evidence/cloud-reliability-2026-10-25.json)의 미병합/미배포 경계와 PostgreSQL/HTTPS/restart 미검증 항목도 당시 기록이며, 후속 정확한 head의 PR CI 성공은 11절에서 별도로 확인한다.

### 10.1 범위와 증거 구분

사용자는 맥북을 계속 켜둘 수 없어 클라우드에서 점검·수정·검증·PR 준비까지 수행하도록 요청했다. 최신 main `295b6e19a81738cb0156889020f21d691df5db27`과 AGENTS/이 문서 전체를 확인한 뒤 독립 브랜치를 만들었다. 로컬 원 대화의 체크아웃·미저장 작업·과거 HWPX·운영 볼륨·계정·인증파일은 수정하지 않았다. GitHub 연결로 source를 확보했고, 큰 과거 로드맵 PNG는 다운로드 도구 제한으로 로컬에 가져오지 않았지만 **원격 기준 tree를 보존하여 변경 대상에서 제외**했다.

클라우드에서 공개 HTTPS GET으로 `/hwp-assistant/` 200, `/api/health`의 `ok=true`, `/api/auth/status`의 accounts/registration/guest 모두 false를 직접 확인했다. 저장소의 `scripts/deploy/smoke.py --url https://pseudowasabi-svr.taile7b6c4.ts.net/hwp-assistant/`도 경로·보호 API 401·앱/편집/비교 정적 자원·WASM 무결성 검사를 통과했다. 로그인·쿠키·업로드·문서 수정은 요청/실행하지 않았다. 운영 자격은 클라우드에 제공되지 않았으며 기존 토큰을 추정하거나 인증파일을 수집하지 않았다.

사용자가 별도 로컬 read-only 인계로 제공한 관측은 **2026-10-25 KST 사용자 관측**이다: 운영 Codex 세 기능 configured/allowed 및 ChatGPT 로그인 확인, HTTP chat/review 미설정, `/ai/models` 503. 이는 별도 HTTP LLM의 미설정이며 Codex 보고서 실패와 혼동하지 않는다. 클라우드에서 해당 인증 후 검사를 직접 성공시킨 것으로 쓰지 않는다.

후속 사용자 인계에서 **research=false 합성 회의실 예약 보고서 1건**이 실제 운영 Codex app-server의 `gpt-6-astra`로 completed=true·status=ready가 됐다. startup 1,847ms/first text 6,330ms/generation 19,347ms/text 13,017ms, 검증된 ReportDraft·SHA 생성 → 내용 검토 정지 → 사용자의 문서 만들기 → 실제 fixed rhwp 미리보기 2쪽까지 확인했다. approval=null이고 확정/서버 저장 버튼은 누르지 않아 기존 문서·버전이 불변이었다. **기본 모델·JSON·서식 경로의 1건 성공 근거이며 research=true 웹 검색 복구나 서버 저장 성공은 아니다.** 클라우드 직접 실모델 시험으로 계산하지 않는다. 사용자 로컬 Python 3.14.7의 main 1,021 passed/기존 warning 2개/197.14초, 배포 계약 75개, frontend 합산 143개+tsc 성공도 별도 인계 기록이다. 코드 수정·커밋·새 배포는 하지 않았다는 인계를 유지한다.

### 10.2 보고서 원인과 수정

사용자 관측의 실패 4건은 research=true, 실제 소요 99.5/126.43/111.23/112.6초, 마지막 writing·research_calls=0·draft 없음이었다. 두 출력에 웹 검색 도구 오류가 명시됐다는 인계를 받았다. **240초 timeout 전에 끝났고, 완료된 공개자료 조회가 없는 연구 요청을 거부하는 코드 경로와 일치한다.** upstream 웹 검색 자체가 왜 사용 불가였는지는 실제 모델 시험 없이 확정·해결 완료로 표현하지 않는다.

후속 코드의 변경:

**검색 실행 command·도구 설정·web_search=live 경로 자체는 기존 main과 동일하다.** 아래 변경은 진단·완료 판정·정리의 개선이며 운영 웹 검색 기능의 실제 복구를 뜻하지 않는다. 사용자에게 원래 운영 오류 전체가 해결됐다고 안내하지 않는다. [최소 검색 진단 절차](codex-research-diagnostics-2026-10-25.md)에 모델 호출 없는 설정/제공자 메타데이터 점검과 명시적인 합성 검색 시험의 구분을 남겼다.

- `execution.failure`에 고정 code·stage·완료 검색 수와 제한된 검증 경로/공식 upstream 오류 종류/HTTP 상태만 기록한다. 원문 예외·모델 본문·자격정보는 진단에 기록하지 않는다. 기존 실패 작업에 원인을 추정해 소급 저장하지 않는다.
- 검색 완료 없음, 로그인/확인 지연, 모델 실패, 연결 단절, 출력 계약·크기·내용 검증 오류, 시간 만료를 서로 다른 한국어 안내로 표시한다. 실패 후 부분 내용은 표시 전용이며 preview·서식·승인·저장 대상으로 승격하지 않는다. 복구 버튼은 요청 화면만 열며 연구를 자동 해제하거나 생성 POST를 재실행하지 않는다.
- 고정 Codex 0.153.0의 완료 계약에 맞춰 final/legacy 완료 메시지와 `turn/completed.items`의 최종 메시지를 사용한다. async/commentary·delta만 있는 출력은 완료 draft가 아니다. 재시도 알림은 같은 turn의 최종 상태를 기다리며 앱이 별도 모델 호출을 자동 생성하지 않는다.
- 표·흐름도·본문 길이의 기존 validator 제한을 신규 생성 schema에도 전달한다. 역사 draft/서식 정책을 완화하거나 바꾸지 않는다.
- 별도 세션의 자식이 파이프를 보유하면 deadline 후 정리가 무기한 대기하는 POSIX 결함을 재현해 비차단 입출력·DefaultSelector·유한 정리로 수정했다. FD 1024 초과, UTF-8 분할, 막힌 stdin, 상속 파이프 회귀를 시험했다. Windows Job 경로는 보존했으며 이번 환경에서 Windows 실기기를 검수한 것은 아니다.

연구 자동 해제·유료 fallback·추가 연구 preflight turn·저장 경계 우회는 도입하지 않았다. owner/status fence, 동일 요청 결과 재조회, 독립 새 요청 ID, 내용 검토 → 서식 → 사용자 확정 저장을 유지한다.

### 10.3 로그인 자원 준비와 샘플 계정

`totalBytes += await consume(...)`가 병렬 worker 사이에서 공유 합계를 덮어쓰는 결함을 수정했다. 청크마다 동기적으로 공통 합계를 확인하고 64MiB 초과/실패 즉시 모든 worker를 취소한다. 한 자원 32MiB·24개·SRI·정확한 하위 경로·저속/절약/비활성 보류·제한된 재시도를 유지한다.

SRI 다운로드 후 실제 `WebAssembly.compile`을 수행하고 다운로드·컴파일·후속 엔진 초기화/문서 배치를 구분한다. WASM 인스턴스나 숨은 편집기를 만들지 않으며 로그인은 기다리지 않는다. 대기 45초 뒤 늦은 native 결과를 무시한다. iframe에서 브라우저 코드 캐시 재사용 및 실제 최초 문서 지연 개선은 보장하지 않는다.

기존 기억한 ID 최대 5개는 사전 구성된 샘플 계정 선택과 별개였다. 새 `HWP_SAMPLE_ACCOUNTS=[]`는 기본 비공개/비활성이며 운영자가 공개 합성 ID·라벨을 최대 8개 지정해야 한다. 기존 활성 member 비밀번호 계정만 카드에 표시하고 미생성 ID의 공개 가입 선점을 막는다. 비밀번호·계정 자동 생성/초기화·AI ACL 확대는 하지 않는다. 서로 다른 ID의 문서는 기존 ACL로 격리되고 같은 샘플 ID 이용자들은 문서함을 공유한다. [설정·보존 절차](poc-codex-accounts-prewarm-2026-10-25.md)를 따른다. **10.1의 공개 점검 시점에는 운영 계정 기능이 꺼져 있어 토큰 화면이 정상**이었다. 새 코드 배포와 운영 계정 설정 활성화는 별개다.

사용자가 확정한 실제 구성은 **체험 계정 2~3개 선택 → 각 계정 비밀번호 로그인**이다. 최대 8개는 코드의 안전 상한이며 실제로 8개를 만들거나 공개하도록 정한 것이 아니다. 운영 문서에 2개(선택적 세 번째)의 공개 설정, 기존 ID·토큰·과거 소유권·가상 사번 충돌 사전 확인, 대화형 계정별 암호 생성과 격리 확인 절차를 준비했다. 실제 비밀번호·ACL·private env 적용은 미실행이며 운영자 판단 대상이다.

### 10.4 실행 환경·검증·남은 판단

검증한 제품 코드 SHA는 `8a8c9afae4bd809362b8f2eb87507a10e44e1ad3`이며 이후 이 인수인계 갱신은 문서 변경이다. 클라우드 Linux, CPython **3.14.0**과 두 requirements lock, Node **24.15.0**과 package lock을 사용했다. Playwright 1.56.1의 Chromium 141 build 1194 headless-shell을 사용했다. 기본 Chromium 채널은 이 클라우드의 socket 권한 제한으로 시작하지 못했고, 설정에서 지원하는 headless-shell로 실행했다.

| 검사 | 확인된 결과 / 실제 범위 |
|---|---|
| backend 전체 | **단일 전체 실행 1,071 passed, 기존 warning 2개, 244.27초**. subprocess exit=0·JUnit 1,071건·실패/오류/skip 0건·수집 case 일치 확인. 별도 676개+396개 분할의 고유 1,071개도 대조했다. backend 137개 파일 전후 해시 불변 |
| Python 배포 계약 | 75개 통과: deployment 13, isolated host preflight 4, CI 16, smoke 12, developer download 6, published run 24 |
| 보고서 집중 회귀 | 76 passed, 기존 의존성 warning 2개. 합성 실제 stdio이며 실제 모델 호출 0회 |
| frontend | tsc 통과, unit 30/30, mock UI 40/40 |
| Node 계약·build path | 82/82 통과, public path 감사·배포 셸 문법 통과 |
| 실제 고정 Codex | 공식 0.153.0 archive SHA-256 확인 후 CLI flags·app-server initialize/account-read·packaged MCP smoke 통과. 로그인/모델 호출 0회 |
| 최적화 rhwp | 공개 운영 build receipt의 upstream/패치/bridge/바인딩과 7개 정적 자산 SHA-256을 확인. diagnostic=false, WASM `3eb52da9c5602c8036fd70ce0f5f759588224be0069eafb04aed39845310667b` |
| 실제 엔진 build/serve | 검증된 배포 엔진을 재사용한 현재 앱 `/hwp-assistant/` 빌드·served bytes 검사 통과. Docker/Rust 부재로 엔진 소스 재빌드는 하지 않음 |
| 실제 엔진 Node | fill 보존/재열기, 선택·줄바꿈·다중 페이지, 블록 배치/왕복 차이 감지 통과 |
| 실제 엔진 browser | 8 passed / 1 skipped. 격리 SQLite·임시 정적/proxy, 합성 AI. 생성/편집/업로드/버전/재열기, 승인·stale·undo, 응답 유실 재시도, 내용 검토·서식·확정 저장, rich 표/흐름도, 스트림·취소 확인. skip은 실제 Codex 기록물 부재 |

운영 엔진의 해시 검증 재사용은 소스 재빌드나 새 운영 배포를 뜻하지 않는다. manifest에 없는 폰트 자산은 임의로 내려받지 않았으며 완전한 폰트/시각/한컴 검수로 일반화하지 않는다. 이번 로컬 통합은 PostgreSQL·실제 nginx·HTTPS 재시작을 검증한 것이 아니므로 PR의 일회용 컨테이너 CI와 구분한다. 실제 모델 upstream 검색·운영 토큰/샘플 로그인, Safari/Firefox/Windows·한컴·폐쇄망 모델은 미검증이다.

초기 단일 backend 실행은 exit 0이지만 약 60% 뒤 최종 요약이 없어 통과 증거로 계산하지 않았다. 원인은 재현되지 않아 단정하지 않는다. 이후 **분할 두 실행 및 최종 단일 전체 실행 각각의 JUnit·정상 종료·요약과 수집 목록**을 확인했다. 최종 단일 실행이 앞선 불완전 출력을 대체한다. PR CI는 그 정확한 head의 Actions를 함께 확인한다. 비밀정보 없는 수치·환경·한계는 [검증 영수증](evidence/cloud-reliability-2026-10-25.json)에 기록했다.

PR 준비 당시 main 병합·이미지 게시·운영 배포는 별도 사용자 결정을 기다렸으며 실행하지 않았다. **이후 승인과 merge commit 통합·수동 배포 요청은 11절에 기록한다.** 계정 설정을 적용하려면 공개할 합성 ID·비밀번호 계정·별도 AI ACL을 운영자가 선택한다. 보고서 연구 기능은 새 이미지에서 합성 주제로 실제 검색 완료와 최종 draft → 내용 검토 → 서식 → 확정 저장·재열기를 추가 확인해야 한다. 공개 검색 불가가 계속되면 새 진단 종류로 운영 upstream을 점검하고, 연구 옵션 해제는 사용자가 직접 제공 자료 기반 요청을 선택할 때만 사용한다.

DB schema migration은 추가하지 않았다. 적용 전 기존 일관 백업과 실제 게시 SHA 확인을 유지한다. 롤백은 새 이미지/환경을 덮어 추정하지 말고 마지막 성공 배포 영수증의 기존 이미지로 되돌리며 `HWP_SAMPLE_ACCOUNTS=[]`를 사용한다. 기존 계정·문서·버전·세션/사용량 테이블과 세 운영 볼륨은 삭제·초기화하지 않는다.

## 11. 2026-10-25 승인 후 병합과 배포 검증

### 11.1 승인과 정확한 병합 대상

사용자는 검토 가능한 코드·검증·한계가 준비된 뒤 **PR #35의 main 병합과 새 운영 배포를 승인**했다. 승인 전 기록인 10절의 미실행 표현은 그 시점의 경계다. [PR #35](https://github.com/m4r4m01m/hwp-assistant/pull/35)의 정확한 head `f7a1da1bfa2f32a9034282fdb81a59c791bc0298`을 **2026-10-25 19:34:06 KST**에 [merge commit `49dda2c8de06288111216a61ba720dc148834257`](https://github.com/m4r4m01m/hwp-assistant/commit/49dda2c8de06288111216a61ba720dc148834257)으로 통합했다. 부모는 기존 main `295b6e19a81738cb0156889020f21d691df5db27`과 검토한 PR head다. squash/rebase·source 브랜치 삭제·기존 데이터 초기화는 하지 않았다.

제품 코드는 `8a8c9afae4bd809362b8f2eb87507a10e44e1ad3`이며 PR의 후속 두 커밋은 문서만 변경했다. 이 절의 인수인계 갱신도 제품 코드·DB schema·운영 설정을 변경하지 않는다.

### 11.2 PR head와 병합 후 main의 검사

| 대상 | 정확한 SHA의 검증 근거 |
|---|---|
| PR #35 최종 head `f7a1da1` | [HWP deployment 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40182984927), [build path](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40182984900), [deployment-only 계약](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40182984998), [PoC 계정·Codex 컨테이너 통합](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40182984978) **4개 workflow 성공**. 이미지 게시·운영 배포 job은 PR 조건에 따라 정상 skip됐다. |
| PR 컨테이너 통합 범위 | 일회용 PostgreSQL/API/web·HTTPS, 고정 CLI 오프라인 smoke, 실제 browser 3개, 재시작 후 비밀번호 로그인·세션·HWPX 해시·계정 간 양방향 격리·로그아웃을 검증했다. 10절의 로컬 SQLite 시험과 별도이며 실제 모델 호출은 0회다. |
| 병합 후 main `49dda2c` | [HWP deployment 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185311735), [build path](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185311749), [deployment-only 계약](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185311706) **3개 workflow 성공**. HWP deployment의 검사 job 3개 성공, 게시·배포 job 2개 정상 skip. |
| main Python 전체 | [job 121144749249](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185311735/job/121144749249): **1,071 passed, 2 warnings, 316.99초**. 기존 의존성 warning이며 cloud 단일 실행 244.27초와 다른 환경의 실행이다. |

### 11.3 승인된 단일 수동 실행과 실제 운영 배포

원 대화의 실행 주체가 main **`49dda2c8de06288111216a61ba720dc148834257`**의 `hwp-deploy.yml`을 `deploy=true`로 **한 번만 dispatch**했다. [수동 run 40185409373](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185409373)은 **2026-10-25 19:35:34 KST**에 생성됐고 클라우드는 같은 run을 감시한다. 원 대화와 클라우드가 중복 dispatch하거나 서로의 브랜치를 동시에 수정하지 않는다.

이 run은 **전체 성공**했으며 [build-verify-publish job 121146097092](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185409373/job/121146097092)에서 격리 PostgreSQL/API/web·HTTPS 업로드/영속 바이트·재시작·실제 브라우저 검증 후 이미지 게시를 완료했다. [deploy-home job 121146882648](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40185409373/job/121146882648)도 **2026-10-25 19:45:45 KST 성공 종료**했다. 원 대화의 로컬 Mac이 아니라 GitHub-hosted 빌드와 홈서버 runner에서 진행됐다. 클라우드의 중복 dispatch·재실행은 0회다.

| 단계 | 직접 확인한 근거 |
|---|---|
| 수동 run 검사 | Python **1,071 passed, 2 warnings, 288.04초**. frontend tsc·unit 30/30·Node 계약 46/46·mock UI 40/40·TLS/Service Worker 1/1 성공 |
| 게시 이미지 | API와 web 모두 태그 `49dda2c8de06288111216a61ba720dc148834257`. 게시 digest는 아래 검증 영수증에 기록 |
| 운영 보존·백업 | 실제 job 로그의 환경 검증·`Backup complete`와 성공 종료를 확인. writer 중지 후 custom DB dump·읽기 전용 문서 archive·목록/해시 검증, 기존 볼륨 유지 계약과 대조 |
| 운영 교체 | DB/API/web Healthy, 정확한 SHA의 `Local deployment checks passed`, 로컬 경로·보호 API·편집/비교 자원·WASM 무결성 PASS 확인. image revision fence와 last-success 영수증 생성은 해당 배포 스크립트 성공 경로로 확인했으며 private 파일 원문은 수집하지 않음 |
| 공개 Funnel | workflow의 credential 없는 공개 HTTPS smoke step 성공. 로컬과 공개 smoke의 PASS 로그를 각각 확인 |
| 클라우드 독립 확인 | 배포 종료 후 저장소 공개 smoke도 별도로 통과. `/api/health` ok=true, 인증 필요=true, accounts/registration/guest=false, 새 sample_accounts 필드 존재·0개 확인. 로그인·쿠키·문서 쓰기 검사는 실행하지 않음 |

API 게시 digest: `sha256:4098a871f464d8581241643ee75dc998c1f527e565f6989944c8983307ae2a0a`. web 게시 digest: `sha256:93747143ebb68bce29a7c7ad853890bc0c73218a8bdde44af6c7c2cfd6304bf8`. 이는 registry 게시 증거다. 기존 배포는 SHA 태그·revision label을 검증하며 digest 고정 배포로 바꾼 것은 아니다. 비밀정보 없는 [배포 검증 영수증](evidence/deployment-pr35-2026-10-25.json)을 함께 확인한다.

운영 private env·비밀번호 계정·`HWP_SAMPLE_ACCOUNTS`·Codex AI ACL은 변경하지 않았다. 새 코드 배포만으로 계정 기능이나 샘플 카드·AI 사용 허용이 자동 활성화되지 않는다. 계정별 암호 생성·공개할 체험 ID 선택·충돌 확인과 명시적 AI ACL은 기존 운영 절차와 사용자 판단을 유지한다. 원본·과거 HWPX·문서/계정 소유권·기존 토큰·세 운영 볼륨·다른 서비스는 보존한다.

후속 기록 변경은 문서 전용이다. 샘플 계정 JSON 예시는 배포 환경 파서가 내부 큰따옴표를 보존하도록 전체 값을 작은따옴표로 감싸고 합성 env 파일로 확인했다. 실제 private env·계정·허용목록에 적용하지 않았다. 문서가 후속 main에 반영돼도 이번 운영 이미지 태그는 위 `49dda2c8de06288111216a61ba720dc148834257`이며 문서만을 이유로 이미지 재게시/재배포하지 않는다.

이 배포 직후의 다음 목표인 **research=true 실제 검색·완료된 ReportDraft/SHA 검증·내용 검토 정지**는 원 대화의 인증된 운영 세션에서 시험할 항목으로 인계했다. 서식 미리보기는 별도 사용자 선택, 서버 저장은 표시된 내용을 검토한 사용자의 명시적 확정이 필요하며 배포 승인만으로 실행하지 않는다. 기존 research=false 1건의 사용자 성공 기록이나 CI의 합성 AI가 실제 검색 시험을 대신하지 않는다. 클라우드는 인증정보를 복제·추정하지 않으며, 실제 연구 복구가 확인되기 전에는 검색 장애가 해결됐다고 표현하지 않는다. **배포 완료와 실제 웹 검색 복구는 별개다. 후속 인계에서도 검색 미완료가 지속됐으며 이후 사용자 정책 변경은 12절을 따른다.**

## 12. 2026-10-25 공개자료 조회 미완료 초안 정책

### 12.1 최신 사용자 관측과 승인 범위

기준 main은 문서 PR #36까지 통합된 **`0a92807ab810c9f45c83efe752ddb1eae0a2024a`**이고 제품·운영 이미지는 **`49dda2c8de06288111216a61ba720dc148834257`**다. 새 작업은 `codex/research-warning-drafts-2026-10-25`에서 분리한다. 기존 source 브랜치·사용자 체크아웃·과거 HWPX·계정/토큰 소유권·운영 볼륨과 다른 서비스를 보존한다.

사용자는 운영 49dda2c에서 다시 **`research_unavailable`, 마지막 `writing`, 완료 검색 0건, 약 115초, 연결 준비 1,931ms/첫 글 21,738ms**를 관측했다고 인계했다. protocol 오류 종류와 HTTP 상태는 없었다. 이는 사용자 인증 세션의 결과이며 클라우드 직접 실모델 시험이 아니다. CLI 구성·도구 노출·모델 선택·upstream 실패 가운데 하나를 이 정보만으로 확정하지 않는다. 앞선 배포 성공이 검색 복구를 증명했다는 해석을 하지 않는다.

사용자는 **새 요청부터 자료 조회가 미완료이거나 일부 실패해도 최종 정상 완료·유효 ReportDraft가 있으면 경고를 남겨 내용 검토·서식·편집할 수 있도록 개발**하도록 명시했다. 기존 정책이 실패를 요구했던 기록은 당시 계약으로 보존하며 과거 실패/초안에 소급하지 않는다. 새 UI는 경고 허용 정책을 명시해 고정하고 공개자료 조회는 기본 켬을 유지한다. 추가 허용 gate·숨은 prompt 교체·research 자동 해제·추가 모델 호출·유료 fallback을 도입하지 않는다.

또한 사용자는 **기존 스레드 chat와 AI review 두 기능을 현재 홈서버 Codex로 연결할 수 있도록 개발**하도록 명시했다. 앞선 기록의 “HTTP 미설정은 별도”라는 진단은 과거 실패 구분으로 유지하되, 이번 개발을 HTTP 안내만 하는 범위로 축소하지 않는다. 기존 HTTP 명시 선택과 권한·문서 경계를 보존하는 새 selector를 구현한다. **개발 승인과 새 PR 병합·운영 env 변경·이미지 게시·배포 승인은 별개이며 후자는 아직 실행하지 않았다.**

### 12.2 새 요청의 동결 정책과 경고 초안

입력 `research_policy`는 `require_completed`/`allow_unverified`이며 새 UI는 `allow_unverified`를 명시한다. 필드가 없는 기존 요청은 엄격 정책과 원래 입력 hash를 유지한다. 같은 ID 재조회는 원래 결과이며 정책 변경을 핑계로 새 모델 호출을 하지 않는다. 보완 작업은 고정한 source draft/SHA와 원래 정책·기존 미검증 경고를 상속하고 source·수동 편집 HWPX·과거 preview를 바꾸지 않는다.

최종 정상 종료·완료 메시지·유효 ReportDraft만 경고 초안이 될 수 있다. partial JSON/delta·취소·timeout·모델 실패·invalid 출력은 계속 실패/취소 경계다. 서버가 `research_notice`를 최종 검증/hash 전에 삽입하며 모델 출력 schema에는 노출하지 않는다. 내용 검토·서식 편집·결과/출처와 HWPX 확인 사항에 **자료 조회 미완료·최신 정보 미검증** 경고를 이어 간다. 기존 최대 6개 limitations·인용·사실·출처 후보 URL을 삭제·절단하지 않는다. 모델 기억의 URL을 확인된 출처로 표시하지 않으며 완료 검색 횟수도 개별 출처/사실 검증을 보증하지 않는다.

경고를 포함해 동결한 draft/SHA로 내용 검토 → 사용자 선택의 서식 → 편집 → 명시적 확정 저장을 유지한다. 저장 바이트를 서버가 뒤늦게 경고 삽입 명목으로 변경하거나 자동 저장하지 않는다. 구체적인 notice 필드·상태·경고 code와 레거시 경계는 [정책 문서](report-research-warning-2026-10-25.md)를 따른다.

### 12.3 chat·review의 선택적 Codex 연결

새 selector는 `HWP_CHAT_PROVIDER`/`HWP_REVIEW_PROVIDER`이며 값은 `codex`/`llm`, native·Compose 기본은 **`llm`**이다. 기존 HTTP 환경을 조용히 다른 전송 경로로 바꾸지 않는다. 홈서버에서 두 기능을 Codex로 쓰려면 승인된 새 코드 이미지와 두 값을 `codex`로 선택한 env의 API 재생성이 필요하며 별도 모델 서버는 필요하지 않다. 현재 private env·계정·AI ACL은 변경하지 않았다.

Codex 선택 시 기존 서버 ChatGPT 인증·앱 ID Codex ACL·공통 용량·사용자 AI budget·DB 일일 cap을 재사용한다. HTTP의 LLM ACL과 혼동하지 않는다. chat은 질문 작성자/문서 권한·context revision·문서 버전·durable idempotency를, review는 임대·base version·원문 hash·Suggestion 검증과 제안/승인/저장 경계를 보존한다. 모델 실패 시 HTTP로 자동 전환하지 않는다. chat의 완료 응답 재조회는 추가 모델·일일 사용량을 만들지 않는다. 기존 review API는 별도 재조회 요청 ID가 없어 새 접수는 새 호출로 계산하며 자동 반복하지 않는다. UI의 Codex 두 요청만 250초로 대기하고 HTTP 기본 180초는 유지한다. 새 basic runner는 모델 실행 `min(settings.timeout, 210초)`·인증 준비 최대 5초·제한된 정리를 구분해 기존 proxy 240초보다 여유를 둔다. 보고서·문단 수정 timeout이나 private 호스트 proxy는 변경하지 않는다. 실제 운영 적용과 API 재생성의 선행 조건은 [Codex 연결 문서](codex-chat-review-2026-10-25.md)를 따른다.

### 12.4 검증 기록과 아직 실행하지 않은 일

기준 `0a92807`의 push workflow 3개 성공은 5절의 기존 source 증거다. 최초 검토 제품 코드 [`8ec1b906c4a773686bef935f875be7b0aa9c3281`](https://github.com/m4r4m01m/hwp-assistant/commit/8ec1b906c4a773686bef935f875be7b0aa9c3281), tree `74e832a235f8d7217dadb092da0fad46e85e3d5d`를 별도 브랜치에 push해 원 대화의 read-only 검토에 공유했다. 독립 검토의 P2/basic 완료 계약과 시험 assertion 보완 후 **최종 제품 코드 [`773da9a4c3adde85d9fbb70edff5ad6592a8037b`](https://github.com/m4r4m01m/hwp-assistant/commit/773da9a4c3adde85d9fbb70edff5ad6592a8037b)**, tree `a459e2594327b21528f5756fa7aefebee6301beb`를 push했다. 부모는 `8ec1b906`이며 변경한 4개 파일의 범위는 basic runner·그 회귀/fixture·실제 엔진 시험 assertion이다. 최종 문서/시험 head의 PR CI는 아직 확인 전이다. 실행 환경·backend/frontend/고정 엔진 결과·그 head의 CI는 완료된 결과만 [후속 검증 영수증](evidence/research-warning-drafts-2026-10-25.json)에 기록한다. 구현 중인 결과를 기준 SHA의 기존 통과 수로 대체하거나 예상 시험 수를 완료로 쓰지 않는다.

제품 source 동결 단계의 중간 집중 검증은 보고서 **100 passed/기존 warning 2개/11.92초**, Codex helper·chat·기존 관련 묶음 **71 passed/7.02초**, root review/capability 묶음 **15 passed/기존 warning 2개/2.59초**다. 앞선 59개 관련 실행과 이 묶음들은 일부 겹치므로 합산 고유 시험 수로 표현하지 않는다. 다음 표는 이후 확인된 frontend·엔진의 최종 결과이며 backend 전체와 exact-head CI를 대신하지 않는다.

| 후속 검사 | 확인된 결과와 한계 |
|---|---|
| 실행 환경 | Python 3.14.0·backend/MCP locks, Node 24.15.0·package lock, Chromium 141 build 1194 headless-shell. 합성 자료만 사용 |
| frontend 정적·단위 | tsc 정상 종료, unit **30 passed** |
| frontend mock UI | 최종 통합 **57 passed/41.7초**. 새 보고서 9개/chat·review 8개 포함. 별도 focused 보고서 **9 passed/5.2초**, chat·review **8 passed/6.2초**는 중복 검증이며 57개에 더하지 않음 |
| 실제 엔진 build/serve | 현재 앱 `/hwp-assistant/` 빌드 및 영수증 포함 제공 바이트 **8개** 대조 통과. 검증한 최적화 엔진 재사용이며 Rust/Docker 소스 재빌드가 아님 |
| 기존 실제 엔진 회귀 | **8 passed/1 skipped**. skip은 실제 모델 기록물 부재. 합성 모델/격리 SQLite·임시 서버 시험 |
| 새 경고 초안 실제 엔진 | 별도 실행 **1 passed/8.9초**, JUnit 1 case·failure/error/skip 0·exit 0 확인. 완료 검색 0건의 유효 draft/SHA → 내용 검토 정지 → 수동 서식 준비 → 경고와 기존 6×400자 확인 사항·출처 URL 보존 → 수동 편집 → 유실 저장 응답의 같은 바이트 재시도 → 한 버전 → 같은 draft SHA → 재열기 출력 보존 확인 |
| backend 전체·최초 제품 head | `8ec1b906` 기준 **단일 전체 1,159 passed**, JUnit 1,159 case·failure/error/skip 0·수집 목록 일치·exit 0 확인. JUnit 시간 **259.955초**, 프로세스 경과 262.802초. backend 154개 파일·locks/workflow 불변. stdout의 마지막 요약이 없어 전체 warning 수는 미확정이며 집중 회귀의 warning 2개를 복제하지 않음. P2 후속 head의 전체 CI와 구분 |
| 배포·Node 계약 | Python 배포 계약 **75 passed**. Node 24.15.0 광범위 계약 **82/82**에 build path 6개 포함, JUnit·exit 0 확인. public path 감사·배포 셸 4개 문법 정상. locks 불변 |
| 최종 head CI | 최종 시험·문서 커밋의 정확한 SHA에서 확인할 후속 항목. 기준 main 또는 최초 code head의 증거로 대체하지 않음 |

실제 엔진 두 실행을 합산하면 고유 결과는 9 passed/1 skipped지만 **단일 전체 실행 결과가 아니다**. 새 시험의 초기 XML split-run 문자열 비교·문단 snapshot assertion 두 문제를 시험 코드에서 수정했으며 해당 시험의 보고서 제품 코드는 최초 검토 SHA와 같다. 아래 basic runner의 P2 후속 변경과 구분한다. 중간 exit 0에 최종 시험 요약이 없던 실행은 성공으로 세지 않고 후속 JUnit·요약·정상 종료가 있는 결과를 사용했다. 폰트는 고정 manifest 밖에서 가져오지 않았고 한컴·Safari/Firefox·실제 운영 모델·검색 복구를 검증한 것으로 일반화하지 않는다.

사용자의 독립 검토에서 basic chat/review의 모델 제한 240초에 인증·정리를 더하면 proxy read timeout 240초를 넘을 수 있는 P2를 확인했다. 후속 제품 보완은 **basic runner만 210초 상한**을 적용하며 더 짧은 제한·기존 HTTP 180초·UI Codex 250초·보고서/문단 수정 제한은 유지한다. 추가 독립 검토에서는 같은 item ID/text의 완료 summary가 phase/delivery를 다르게 주장하면 거부하도록 fingerprint를 보강했다. 이 충돌은 합성 protocol 회귀이며 실제 upstream에서 발생했다는 주장은 아니다. 최종 helper·chat/review API 집중 검사는 **67 passed/5.59초**이며 helper 42개 안에 deadline probe 3개와 phase/delivery 충돌 4개를 포함한다. 앞선 63개 중간 통과와 합산하지 않는다. 실제 210초 추론을 실행한 것은 아니고 적용된 제한값·더 짧은 제한 존중과 기존 stdio timeout/자식 정리/반복 취소·용량 회수 경계를 확인한 합성 시험이다. 이 보완은 제품 SHA `773da9a4`에 포함되며 최종 head의 단일 전체 CI는 별도로 확인한다. 원 검토의 보고서 완료·provenance·legacy·HWPX·보완 보존 경계에서 차단 결함이 없었다는 사용자 검토도 실제 모델 성공과 구분한다.

현재 새 작업의 실제 모델 호출은 **0회**이며 클라우드에 운영 인증정보가 없다. 실제 검색 복구·운영 Codex chat/review 응답·후속 새 배포·private env 반영·계정 활성화는 검증 완료로 주장하지 않는다. 실제 실행 시험에는 합성 자료만 쓰고 자료/원문 오류·토큰·암호·인증파일은 기록하지 않는다. 기존 고정 엔진·문서 표준과 검토/승인/저장 경계를 유지한다. 새 코드 검증이 끝나면 변경·위험·롤백을 구체화해 필요한 최종 승인만 요청한다.

**롤백 한계:** DB migration은 없지만 새 `ReportDraft.research_notice`는 구버전 `49dda2c`의 엄격한 계약에 없는 필드다. 새 경고 초안이 생긴 뒤 구버전으로 되돌리면 그 초안의 보완·서식 준비가 거부될 수 있다. 저장된 HWPX 원본·과거 blob은 불변이다. 롤백은 새 접수를 멈추고 신규 draft/notice/실행 기록을 보존한 뒤 호환 버전과 이어서 작업할 경로를 검토해야 한다. metadata 삭제·hash 재작성·자동 schema downgrade·운영 볼륨 초기화로 해결하지 않는다. [정책 문서의 롤백 절](report-research-warning-2026-10-25.md#6-롤백과-신규-초안-호환성)을 함께 따른다.
