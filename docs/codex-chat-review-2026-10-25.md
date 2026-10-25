# 기존 스레드 chat·AI review의 Codex 연결

2026-10-25 후속 개발. 기준 main은 `0a92807ab810c9f45c83efe752ddb1eae0a2024a`, 작업 브랜치는 `codex/research-warning-drafts-2026-10-25`다. 최초 검토 코드 `8ec1b906c4a773686bef935f875be7b0aa9c3281`의 독립 검토를 반영한 **최종 제품 코드는 [`773da9a4c3adde85d9fbb70edff5ad6592a8037b`](https://github.com/m4r4m01m/hwp-assistant/commit/773da9a4c3adde85d9fbb70edff5ad6592a8037b)**이며 최종 문서 head의 CI와 구분한다. 사용자는 기존 두 HTTP 기능을 홈서버의 Codex로 연결할 수 있게 개발하도록 명시했다. 새 코드의 main 병합·운영 설정 변경·이미지 게시·배포는 별도 승인 대상이며, 현재 운영 이미지 `49dda2c8de06288111216a61ba720dc148834257`는 바꾸지 않았다. 검증 결과는 [PROJECT_CONTEXT 12절](PROJECT_CONTEXT.md#12-2026-10-25-공개자료-조회-미완료-초안-정책)과 [검증 영수증](evidence/research-warning-drafts-2026-10-25.json)을 따른다.

## 1. 기능과 provider 선택

| 대상 | API | 선택 변수 | 값과 기본값 |
|---|---|---|---|
| 기존 스레드 문서 대화 | `POST /api/threads/{thread_id}/chat` | `HWP_CHAT_PROVIDER` | `codex` 또는 `llm`, native·Compose 기본 `llm` |
| 기존 AI 검토 | `POST /api/ai/review` | `HWP_REVIEW_PROVIDER` | `codex` 또는 `llm`, native·Compose 기본 `llm` |

기존 보고서·문단 수정·문서 assistant 설정과 별개인 두 selector다. 기본값을 `llm`으로 유지해 기존 HTTP 환경의 전송 경로를 조용히 바꾸지 않는다. 운영자가 Codex를 선택하려면 아래 두 비밀 아닌 값을 private env에 명시해야 한다. 이 예시는 실제 운영 설정을 변경한 기록이 아니다.

```dotenv
HWP_CHAT_PROVIDER=codex
HWP_REVIEW_PROVIDER=codex
```

Codex는 기존 API 컨테이너의 고정 CLI·ChatGPT 서버 로그인·`HWP_CODEX_MODEL` 선택을 사용한다. 별도 모델 서버나 유료 API key를 자동 설치·생성하지 않는다. 기존 HTTP를 쓰려면 해당 selector를 `llm`으로 두고 기존 `LLM_PROVIDER`/`LLM_BASE_URL`/`LLM_MODEL` 등 설정을 보존한다. 오류가 나더라도 반대 provider로 자동 전환하거나 같은 요청을 추가 호출하지 않는다.

`GET /api/ai/models`는 기존 HTTP 모델 목록 경로다. 이 경로의 503을 Codex 모델 목록이나 Codex chat/review 실패로 설명하지 않는다. 기능별 실제 selector와 해당 provider의 준비·권한 상태를 구분한다.

## 2. Codex 이용·권한·실행 경계

Codex 선택 시 기존 `HWP_CODEX_ALLOWED_USERS` 또는 단일 `HWP_CODEX_USER`의 앱 ID ACL, 서버 ChatGPT 인증 확인, 공통 작업 용량, 사용자 AI budget, PostgreSQL 영속 일일 cap을 재사용한다. HTTP의 `HWP_LLM_ALLOWED_USERS`와 다른 권한이다. 계정을 만들거나 selector를 바꾸는 것만으로 AI 사용 권한이 생기지 않는다.

두 기능도 임시 빈 작업 디렉터리·ephemeral thread·read-only·승인 없음으로 실행한다. 웹 검색·shell·MCP·파일 수정 등 불필요한 도구를 차단하며 기존 보고서의 검색 경로를 이 작업으로 변경하지 않는다. 로그인·사용량 제한·모델 실패·취소의 결과가 불확실할 때 숨은 재호출을 하지 않는다.

결과는 최종 정상 turn과 완료 메시지의 schema 검증을 함께 요구한다. delta/commentary/async 단독은 결과가 아니며 같은 완료 item ID가 terminal summary에 다시 나타나면 text·phase·delivery의 fingerprint가 같아야 한다. 본문이 같아도 phase/delivery를 바꿔 nonfinal 메시지를 최종 결과처럼 되살리는 충돌은 거부한다. 다른 ID의 후속 commentary는 최종 결과로 승격하지 않는다. 이 방어의 회귀는 합성 protocol 시험이며 실제 upstream에서 충돌을 관측했다는 뜻은 아니다.

스레드 chat은 질문 작성자·문서 접근/편집 권한·문서 버전·컨텍스트 revision을 추론 전후 확인한다. 완료된 같은 질문 결과의 재조회는 추가 모델 호출이나 Codex 한도 소비를 만들지 않는다. 응답 도중 권한·버전·컨텍스트가 바뀌면 기존 fence에 따라 거부하며 HWPX나 문서 버전을 수정하지 않는다.

AI review는 기존 문서·편집 임대·base version·선택 원문 경계를 유지한다. 모델은 검토 결과 `Suggestion`을 반환하며 기존 제안·비교·사용자 승인·저장 단계를 건너뛰지 않는다. 서버가 검토 응답을 반환한 것과 승인한 내용이 문서에 적용·저장된 것은 다른 상태다. 이 기존 review API에는 요청 ID를 통한 결과 재조회 계약이 없으므로 새로 접수한 검토는 새 모델 호출과 사용량이다. 응답이 불확실하다고 브라우저/서버가 자동 반복하지 않는다.

Codex로 선택된 두 UI 요청만 브라우저 대기를 명시적으로 **250초**로 둔다. 브라우저만 늘려도 실제 서버 응답이 길면 기존 nginx/호스트 proxy의 **240초 read timeout**을 먼저 만날 수 있어, 새 basic chat/review runner의 모델 deadline은 **`min(settings.timeout, 210초)`**로 제한한다. 더 짧은 기존 제한은 존중한다. 인증 준비 확인 최대 5초와 제한된 프로세스 정리를 위한 여유를 모델 실행 밖에 남기는 계약이며 private 호스트 proxy 설정을 변경하지 않는다. 일반 보고서·문단 수정의 기존 timeout은 바꾸지 않는다. HTTP 경로의 기존 쓰기 요청 기본 대기 **180초**도 유지한다. 시간 초과 안내가 나타나도 모델 결과를 추정해 게시하거나 추가 호출하지 않는다.

## 3. 운영에 적용할 때

아래는 **승인 후 사용할 절차**이며 이번 개발 중에는 실행하지 않았다.

1. 새 PR의 정확한 head 검사·검토·merge commit 승인과 새 이미지의 게시/배포 승인을 확인한다. 기존 `49dda2c` 이미지에는 이 후속 selector 코드가 없으므로 환경변수 두 줄만 추가해 기능이 바뀐다고 가정하지 않는다.
2. 기존 DB/문서의 일관 백업과 private env의 별도 보호 백업을 유지한다. 파일 전체·확장 Compose·token·auth.json을 출력하지 않는다. 기존 HTTP 값·계정·암호·AI ACL·문서 소유권·세 운영 볼륨을 보존하고 필요한 두 selector만 명시적으로 선택한다.
3. 승인·검증·게시된 **실제 source SHA의 새 API/web 이미지**를 기존 운영 배포 절차로 적용한다. 그 뒤 환경변수만 추가 변경하는 경우에도 `restart`만으로 반영되지 않으므로 [현재 이미지로 API 재생성 절차](PROJECT_CONTEXT.md#64-환경변수만-바꿀-때-현재-이미지로-api-재생성)를 따른다. 해당 절차의 실행 이미지/checkout 일치 검사·`--no-build --pull never`·진행 중 작업 확인을 생략하지 않는다. 개발 Compose를 운영 Compose에 합치거나 API/DB/RPC 포트를 노출하지 않는다.
4. 서버 Codex 로그인 상태와 두 selector, 로그인한 앱 ID의 Codex ACL을 비밀 아닌 최소 값으로 확인한다. 소유자의 기존 Codex 인증을 수집·복사·초기화하지 않는다. `/api/ai/models`가 HTTP 미설정으로 503이어도 Codex 준비 여부는 따로 확인한다.
5. 허용된 계정에서 합성 자료로 chat 한 건과 review 한 건을 각각 시험한다. chat은 완료 후 같은 질문 재조회·문서/버전 불변을, review는 후보/비교·권한/임대/원문 fence와 명시적 승인·저장 경계를 확인한다. 기존 실제 문서·과거 버전·다른 사용자 컨텍스트를 시험 자료로 사용하지 않는다.

롤백은 마지막 성공 배포 영수증의 호환 이미지와 기존 설정을 기준으로 한다. 기존 HTTP 경로를 명시적으로 유지할 때는 두 selector를 `llm`으로 되돌리되 필요한 기존 HTTP 설정을 보존한다. DB schema·문서·버전·계정·Codex 인증·운영 볼륨을 초기화하지 않는다. 코드 검사·오프라인 CLI 시험·합성 모델 시험을 실제 운영 Codex 응답 성공이나 새 배포 완료로 보고하지 않는다.

같은 릴리스에서 생성된 `research_notice` 보고서 초안은 구버전 `49dda2c`가 거부할 수 있다. provider를 되돌리는 판단과 신규 초안의 호환성을 별도로 확인하고 [경고 초안 롤백 경계](report-research-warning-2026-10-25.md#6-롤백과-신규-초안-호환성)를 따른다. DB migration이 없다는 이유로 경고 metadata를 삭제하거나 구버전 호환성을 보장하지 않는다.
