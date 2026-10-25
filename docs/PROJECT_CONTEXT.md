# HWP Assistant 프로젝트 컨텍스트

갱신일: **2026-10-25 (한국 시간)**. 코드 기준: [main `95eb0dab99f1ac3f9aa55a56c35bb20908ea71c5`](https://github.com/m4r4m01m/hwp-assistant/commit/95eb0dab99f1ac3f9aa55a56c35bb20908ea71c5), [PR #33](https://github.com/m4r4m01m/hwp-assistant/pull/33). 이 파일은 ChatGPT Work와 Codex가 함께 읽는 인수인계 기준이다. 이후 작업에서는 실제 최신 main·CI·운영 상태를 다시 확인한다.

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
| 보고서 | 실제 Codex app-server delta, 초안 내용 검토·별도 보완 작업, 문서 표준/별첨 1~3/실제 렌더링 쪽수 목차, 표·편집 가능한 흐름도. 완료·검증된 draft만 임시 미리보기에 적용하고 사용자가 확정·서버 저장한다. |
| 문서 대화·컨텍스트 | 권한 있는 문서의 사실·작성 기준·선택 원문과 대화/후보/적용 이력. 문서 독자도 모든 작성자의 대화를 읽을 수 있으나 승인·편집·저장은 불가하다. 작성자 전용 비공개 대화로 오해하지 않는다. |
| 양식·문서 표준 | HWPX 양식 등록·검색·새 문서 생성, 제한된 제목/요약/담당·기한 블록, 표준 편집·검증된 예시·등록. 고정 기준서와 과거 표준/초안은 덮어쓰지 않는다. |
| 계정 | 선택적으로 비밀번호 로그인/가입/guest, 기존 토큰·만료/폐기 가능한 세션과 공존. AI는 별도 ACL이다. guest는 하나의 공용 문서 주체다. |
| 반응성 | 정적 압축/해시 캐시·검색·모바일 패널, 로그인 화면의 낮은 우선순위 SRI 자원 다운로드. 다운로드 완료가 WASM 컴파일/문서 배치 완료는 아니다. |

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

## 5. 검증된 상태와 아직 확인할 것

**최신 확인 증거:** #33 병합 후 [HWP deployment 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174658302), [frontend build path](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174657544), [deployment-only 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174657560)는 성공했다. 일반 push에서는 게시/운영 배포 job이 건너뛰어지는 것이 정상이다.

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

1. 마지막 보고에서 미확인인 **서버 Codex device login·앱 ID 허용·환경 반영**을 구분해 확인하고, 합성 자료로 세 실제 AI 경로를 검수한다. 인증파일이나 private env를 수집하지 않는다.
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
