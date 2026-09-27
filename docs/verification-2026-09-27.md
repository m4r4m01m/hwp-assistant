# 실행 검증 기록 · 2026-09-27

기존 작업 폴더를 확장했다. 작업 시작 시 첫 Git 커밋은 없고 모든 코드가 미추적 상태였다. reset/clean/새 앱 교체를 하지 않았다. 기존 `data/`와 원본·과거 blob은 작업 대상으로 변경하지 않았고, 브라우저 시험에는 `.cache/verification-d5a85737dc154da29828349695476d24` 아래 분리된 SQLite DB/파일과 합성 문서를 사용했다. 일정·공수는 추가하지 않았다.

| 실행 | 결과 | 범위 |
|---|---|---|
| 작업 전 `.venv/Scripts/python -m pytest -q` | 18 passed | 기존 저장·모델 계약 기준 |
| 변경 후 `.venv/Scripts/python -m pytest -q` | 28 passed, 경고 1건 | 기존 18개 + 권한/대화/컨텍스트/변경분/템플릿/마이그레이션 9개 + 실제 MCP 프로세스 1개 |
| `cd frontend; npm run build` | 성공 | TypeScript + Vite production build |
| `npm run test:e2e` (아래 두 URL 설정) | 7 passed, 53.3s | 기존 실제 엔진 4개 + 템플릿/제안 2개 + 사용자별 인증 브라우저 1개 |
| `.venv/Scripts/python -m pip check` | No broken requirements found | 기본 + 선택적 MCP lock 설치 환경 |
| `codex mcp get hwp_assistant --json` | enabled=true, 설정 인식 | 프로젝트의 stdio 경로와 전달 환경변수 확인; 모델 호출 없음 |

Python 경고는 기존 Starlette의 anyio BlockingPortal deprecated alias다. 브라우저 콘솔 시험 도구에는 NO_COLOR/FORCE_COLOR 환경 경고가 있었고 시험 실패는 아니다.

## 실제 rhwp / API / 브라우저

기존 `engine.spec.ts`의 업로드·한글 문자열 입력·표/서식·직렬화·과거 버전 불변·재열기·저장 중 입력·응답 유실 재시도를 통과했다. 기존 AI 승인 시험의 모델 응답은 명시적 mock이다.

`collaboration.spec.ts`:

- 실제 `samples/paragraph-and-table.hwpx` 업로드 → 화면에서 양식 등록 → “회의” 추천 → 새 문서 적용 → 최초 복사본과 원본 바이트 일치 → 실제 엔진 편집 → 바이너리 변경분 요청 확인 → 새 HWPX에 수정 내용/표 존재 → 원본·직전 버전 바이트 유지 → 변경분 API에 section0.xml 기록.
- 실제 엔진 문단 선택 → DB 컨텍스트·대화 저장 → **외부 제안 fixture를 HTTP API로 등록** → 승인 전 원문 유지 → 승인 시 exact command 적용 → 서버 저장 → 후속 문단 입력 뒤 오래된 선택 적용 거부. 실제 Codex 모델 생성 시험은 아니다.

`permissions.spec.ts`: 분리된 인증 서버에서 Alice/Bob 브라우저 로그인 → 소유자 화면에서 reader 부여 → Bob의 공유 대화 열람 → 개인 대화 404·편집 임대 403 → Alice의 권한 회수 → Bob의 대화·파일 API 404·목록에서 사라짐 확인.

화면 증거: [대화·Codex 제안 적용](evidence/collaboration-2026-09-27.png).

## 백엔드 / 실제 MCP 프로세스

권한 없는 문서/버전/대화 조회, reader 쓰기, 개인 대화 노출, 타 사용자 임대 사용을 차단했다. 컨텍스트 경쟁 저장은 1건 성공/1건 충돌, 메시지 멱등 재시도는 중복 없음, DB commit 실패 시 컨텍스트 불변을 확인했다. 원본·제거 구간·결과 해시 불일치와 오래된 기준으로 변경분 저장을 거부했다. 템플릿 권한 상속·기준 버전 고정·새 문서 복사·멱등 재시도·권한 회수 후 새 적용 차단을 확인했다.

기존 `test_real_server_process_restart`를 확장해 uvicorn 실제 종료/재시작 후 문서 바이트뿐 아니라 개인 대화 메시지와 자체 컨텍스트도 유지됨을 확인했다. 초기 스키마 001에 대한 추가형 마이그레이션과 명시적 기존 문서 소유자 요구도 시험했다.

`test_mcp.py`는 공식 MCP SDK 클라이언트가 별도 Python stdio 서버를 실행한다. initialize/tools/list/read_context/append_reply/propose_text, 개인 대화 접근 거부, 권한 회수 후 기존 연결의 조회·제안 거부를 실제 인증 HTTP API와 함께 확인했다. 문서 버전 수가 증가하지 않아 제안 접수와 서버 저장이 구분됨도 확인했다.

## mock AI 시험

기존 모델 JSON/timeout/연결 오류 계약 시험은 mock이다. 새 대화 시험은 mock 공급자에 앱 컨텍스트와 저장된 대화가 전달되는지, 응답 중 권한을 회수하면 답변을 게시하지 않는지를 확인했다. 실제 로컬/사내 LLM 호출 성공이나 모델 품질·사실성 검증을 뜻하지 않는다.

## 재현 환경과 시험 중 수정

기존 기본 8000/5173 포트의 실행 중인 앱이 구버전 API를 제공해 처음 브라우저 실행은 `/api/auth/me` 404로 실패했다. 기존 프로세스를 변경하지 않고 별도 포트의 새 API/Vite로 전환했다. 시험의 오래된 제안 클릭 선택자가 과거의 비활성 버튼을 선택해 한 번 실패했고, 최신 제안을 선택하도록 수정한 뒤 전체 7개를 다시 실행해 통과했다. 실패 실행을 성공으로 계산하지 않았다.

- 일반 시험 API `127.0.0.1:18000`, Vite `127.0.0.1:15173`; `HWP_E2E_URL=http://127.0.0.1:15173`.
- 인증 시험 API `127.0.0.1:18001`, Vite `127.0.0.1:15174`; `HWP_E2E_AUTH_URL=http://127.0.0.1:15174`.
- Vite의 `HWP_API_PROXY`를 각각의 API로 설정한다. 인증 서버는 시험 파일에 명시된 합성 Alice/Bob 토큰을 사용한다. 이 공개 시험 토큰을 실제 운영에 쓰지 않는다.
- 기본 개발 명령은 기존 8000/5173을 유지한다. 인증 시험 URL을 지정하지 않으면 해당 시험은 skip되며, 기본 실행만으로 7개 통과라고 보고하면 안 된다.
- MCP 시험을 실행하려면 `backend/requirements-mcp.lock.txt`를 먼저 설치한다. 설치하지 않은 환경에서는 MCP 시험이 skip된다.
- 시험용 API/Vite 프로세스는 종료했다. 기존 기본 포트의 프로세스는 유지했으므로 실제 사용 시 API를 새 코드로 다시 시작해야 한다.

## 미검증·남은 범위

- PostgreSQL 실제 행 잠금·경쟁·이관, Docker Compose 빌드·실행.
- 실제 로컬/사내 LLM 및 Codex 모델이 문장을 생성하고 도구를 선택하는 품질 시험. 현재 채팅의 MCP 도구 실시간 재로드.
- 한컴오피스 한글에서의 편집·서식·배치 왕복, 한국어 OS IME 전체, 인터넷을 차단한 격리망 전체 흐름.
- SSO/운영 계정 관리/운영 인증 보안 검토. 현재는 관리자가 설정한 사용자별 토큰 방식이다.
- 기존 문서에 양식 블록 삽입, 변수 치환, 전체 대화 페이지 탐색, 브라우저 종료 뒤 저장 요청 큐 복원.

기준 계획 파일은 수정하지 않았다. 합성 HWPX의 SHA-256은 기존 기록과 같은 `98cf53fc1b906fe322ad575051d8c3abcbcce86d07cc61c7f5cb6acec689b813`이다.

---

## 추가 검증: 실제 Codex 선택 문단 수정·저장 (2026-09-27)

이 절은 위의 과거 검증 기록을 보존하며 최신 정책과 새 시험을 추가한다. 이전의 “개인 대화 404”는 과거 정책의 결과다. 현재는 **문서 권한자가 모든 작성자의 대화·미적용 후보·적용 이력을 열람**한다. 개인에게 문서를 공유하는 기능과 공유 문서 안의 작성자 전용 비공개 대화는 구분한다. reader의 쓰기 권한은 넓히지 않았다.

### 시작 상태와 변경 범위

- `git status --short`에서 기존 파일 전체가 untracked였다. `git diff`/`git diff --cached`가 비어도 기존 코드가 없는 것은 아니다. 소스만 `.cache/codex-flow-before/`에 비교용으로 보관했다. reset/clean, 기존 DB 초기화, 파일 저장소 삭제, 인증 파일 복사, 커밋을 하지 않았다.
- 유지: FastAPI/SQLAlchemy/파일 저장소, HWPX 불변 버전, rhwp exact command, 변경분 저장, 임대·기준 검사, 템플릿 등록·추천·고정 버전의 새 문서 적용. 기준 계획 문서는 변경하지 않았다.
- 수정: 작성자/legacy private 조건으로 대화를 숨기는 로직 제거. 기존 행·작성자 보존. 새 private 생성 요청은 document로 정규화. 일반 MCP `read_context`에서 제안·후보·적용 이력도 현재 문서 ACL로 조회.
- 추가: 요청 시점의 미저장 스냅샷 → 실제 Codex/MCP 후보 2개 → 명시적 승인 → rhwp 영수증 → 저장 버전 연결. 기존 게시 트랜잭션 안에서 버전과 적용 이력 동시 확정. 비교 중 자동 저장 보류.
- 변경 파일: `backend/app/{collaboration,db,main,mcp_server,schema}.py`, `frontend/src/{collaboration,editor,main}.tsx/ts`, 관련 권한/MCP 시험, `.env.example`, 이 기록과 구현 안내. 추가 파일: `backend/app/{rewrite,codex_runner,rewrite_mcp}.py`, `backend/migrations/003_rewrite.sql`, `frontend/src/rewrite.tsx`, `backend/tests/test_rewrite.py`, `frontend/tests/rewrite-protection.spec.ts`, 비민감 증거 JSON.

### 설치된 기능과 실행 환경 확인

로컬 `@rhwp/editor`/core 0.8.6, self-hosted build commit `e8800c8def63449808a4092798442652ed460552`. 설치 d.ts와 `.cache/rhwp/rhwp-studio/src/document-agent/controller.ts`를 직접 읽었다. `getSelectionContext`의 body_paragraph, `applyTextCommand`의 문서/리비전/원문/서식/인접 문맥 fence, commandId journal, 기존 bridge의 `exportSnapshot`을 사용한다. upstream 설명만으로 지원을 가정하지 않았다.

`codex --version`은 `codex-cli 0.158.0-alpha.2`, `codex login status`는 `Logged in using ChatGPT`였다. `codex exec --help`, `codex features list`로 설치 옵션을 확인했다. 실행 명령은 `codex exec --ignore-user-config --ephemeral --skip-git-repo-check --sandbox read-only --json --color never -C <빈 임시 폴더>`와 제한 MCP 설정이다. shell/unified_exec/apps/plugins/browser/computer/image_generation/multi_agent/skill_search/hooks/memories를 끄고 두 MCP 도구만 허용한다. 설정에서는 모델 이름만 유지한다. 인증 파일이나 API 키를 복사·공유하지 않고 기존 계정으로 실행한다. 실패 시 유료 API 대체가 없다.

### 안전한 재시작과 검증 명령

기존 기본 서버 PID 9160(uvicorn), 17040(부모 런처), 27988(Vite)의 명령줄이 이 프로젝트를 가리키는지 확인했다. PID 9160과 27988만 명령줄을 재검증한 뒤 종료했다. Python 부모는 자식 종료 후 사라졌다. 다른 Codex/개발 프로세스는 종료하지 않았다. `Start-Process -WindowStyle Hidden`으로 같은 프로젝트 서버를 재시작했다.

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
# frontend 작업 디렉터리
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort
```

이후 `/api/health`와 Vite 프록시의 `/api/auth/status`는 200. 기본 DB/파일 저장소와 인증 미설정 local 모드는 유지했다. 실제 AI 시험은 `.cache/codex-flow/test.db`, `.cache/codex-flow/files`를 사용하는 별도 합성 인증 서버에서 실행했다. API 18001, Vite 15174(`HWP_API_PROXY=http://127.0.0.1:18001`), `HWP_CODEX_USER=alice`, `HWP_CODEX_API_URL=http://127.0.0.1:18001`. 토큰 값은 기록하지 않는다. 기존 문서의 인증 소유자는 변경하지 않았다.

마지막 서버 수정 반영 시에도 listen 포트 소유 PID와 프로젝트 명령줄을 확인하고 API PID 23652(8000), 27272(18001)만 재시작했다. 8000/5173/15174의 auth/status 200과 실제 후보 작업의 ready 상태 보존을 확인했다. 승인 대기 브라우저는 미저장 버퍼와 자동 저장 보류 상태를 유지한다.

```powershell
.venv/Scripts/python -m pytest -q
# frontend 작업 디렉터리
npm run build
$env:HWP_E2E_AUTH_URL='http://127.0.0.1:15174'
npm run test:e2e
npx playwright test tests/rewrite-protection.spec.ts
# 실제 UI 키 입력/버튼, 명시적 사용자 선택을 기다리는 로컬 harness
node .cache/codex-flow/verify.mjs
```

결과: 백엔드 **34 passed**(12.34초, dependency deprecation warning 1개), frontend build 통과, 기존 브라우저 **7 passed**(21.7초), 추가 실제 엔진 보호 시험 **1 passed**(2.6초). 마지막 시험 최초 실행은 한국어 오류 메시지를 영문 코드로 기대한 assertion 때문에 실패했다. 실제 반환 메시지에 맞춰 시험만 수정한 뒤 통과했다. 고정 후보/영수증 시험을 실제 모델 실행으로 계산하지 않는다.

마지막 API 재시작 후 `npm run test:e2e` 전체 재실행도 **8 passed (23.6초)**였다. 이는 위 7+1과 같은 시험의 최종 통합 실행이며 총 16개로 중복 계산하지 않는다. 마지막 저장소 확인에서도 기존 원본·과거 파일 25/25개 해시가 동일했다.

### 실제 정상 흐름: 후보 생성 성공, 사용자 승인 대기

[실행·작업·후보 연결 JSON](evidence/codex-flow-2026-09-27.json)에 비민감 증거를 보관한다.

- 웹 rhwp 키 입력으로 합성 문서 생성·최초 저장 후 목록에서 다시 열었다. 같은 문단에 후속 문장을 직접 입력하고 **저장하지 않은 상태**로 실제 Codex 요청 버튼을 눌렀다. API로 고정 후보를 주입하지 않았다.
- 문서 `20a47965-0f42-4d69-926f-1f0fefb61684`, 기준 `dd75da78-f648-4ae7-aeae-0588513ae8b1`, 대화 `6b5001af-99c9-4cfc-8cb5-4f76096a5ad4`, 작업 `44ed30fc-ac37-4590-96bd-0f472397b24a`.
- 실제 Codex 세션 `01a0e21c-4d8a-7710-8118-9e71dd71f118`, exit 0, turn.completed. `hwp_document.read_rewrite_task` completed, `hwp_document.submit_rewrite_candidates` completed. shell/file 변경 이벤트 없음.
- AS-IS: “회의 자료는 금요일까지 준비합니다. 담당자는 준비가 끝나면 팀에 알려 주면 좋겠습니다.” 저장본에는 첫 문장만 있었으며, 요청 스냅샷과 모델 입력은 후속 미저장 문장을 포함했다.
- 후보 1 `6d0df313-dbf6-55f9-bd45-7cf58cd7f4bb`: “회의 자료는 금요일까지 준비합니다. 담당자는 자료 준비가 완료되면 팀에 알려 주시기 바랍니다.”
- 후보 2 `d8097c05-3d99-5ba0-9277-494033941773`: “회의 자료는 금요일까지 준비하며, 준비를 마친 담당자는 팀에 완료 사실을 알려 주시면 좋겠습니다.”
- 후보 생성 후 에디터 스냅샷 불변, 확정 버전 수 1개 유지. 실제 UI의 AS-IS/TO-BE 비교와 후보 ID 확인.
- **사용자의 명시적 후보 선택 대기 중. 실제 후보 승인·적용·저장·재열기는 아직 실행하지 않았다.** 구현 경로 존재나 보호 시험 통과를 정상 흐름 완료로 계산하지 않는다.

### 보호 동작과 미실행 범위

| 항목 | 근거 / 결과 |
|---|---|
| 타 작성자/legacy private 대화 열람, reader 쓰기 거부, 외부인 404 | 인증 API, 실제 stdio MCP, 실제 인증 브라우저 시험 통과 |
| 타 작성자 후보·적용 이력 열람 | 고정 후보/영수증 API 시험 통과; 일반 MCP의 후보 열람·reader 쓰기 거부·권한 회수 통과 |
| 원문 변경 후 적용 거부·미저장 입력 보존 | 설치된 실제 rhwp + 고정 텍스트 자동 시험 통과 |
| 같은 commandId 재전송 | 실제 rhwp: 같은 영수증·리비전 불변. 이후 입력 뒤 재전송 거부 |
| 오래된 기준의 승인/저장 거부 | 고정 후보/영수증 백엔드 시험 통과. 후보·스냅샷·최신 버전 유지 |
| 저장 commit 실패 / 중복 요청 | 고정 후보/영수증 시험: 실패 시 버전·적용 이력 미확정, 같은 요청 재전송 시 버전 1개만 추가 |
| 원본·과거 파일 보존 | 시작 시 기존 파일 25개 SHA-256 기록, 변경 후 25개 모두 동일 |
| 템플릿 | 기존 등록/추천/고정 버전 적용 시험 통과. 실제 문단 흐름에는 추천이 등장하지 않음 |
| 다른 사람의 Codex 후속 요청 / 세션 없는 독립 컨텍스트 복원 | 범위 제외·미실행. DB 저장만으로 복원 성공이라고 표시하지 않음 |
| 한컴 왕복 / 격리망 / 실제 Qwen / SSO / PostgreSQL 실기동 | 미실행. 웹 재열기나 파일 생성으로 한컴 호환성을 판정하지 않음 |

다음 가장 작은 단계: 사용자가 실제 후보 하나를 선택하면 해당 버튼으로 적용·저장한 뒤 새 버전·적용 이력·웹 재열기를 확인한다.

### 후속 확인: 기존 실제 후보의 승인 이후 검증 (2026-09-27 18:34 KST)

사용자 지시를 후보 승인으로 해석하지 않고 현재 상태를 먼저 조회했다. [후속 상태 증거](evidence/codex-flow-followup-2026-09-27.json)는 해당 합성 작업의 식별자·해시·상태만 포함한다. 전체 DB·인증 파일·토큰을 출력하지 않았다.

- 작업 `44ed30fc-ac37-4590-96bd-0f472397b24a`는 여전히 `ready`. 후보 1/2의 기존 ID와 내용은 그대로다.
- 현재 버전과 작업 기준 버전은 모두 `dd75da78-f648-4ae7-aeae-0588513ae8b1`. 확정 버전 수는 **1개**, 새 버전 추가는 **0개**다.
- 해당 작업의 승인/적용 행은 **0개**. 승인 후보·승인자·rhwp 적용 영수증·새 저장 버전 연결은 아직 없다. 기존 검증 harness의 사용자 선택 파일과 완료 기록도 없다.
- 요청 당시 보관한 대상은 `section=0, paragraph=1, charOffset=0, length=48`, 편집 리비전 1, 원문 SHA-256 `827bad7430c5424fe294d2c50b9f5a6b45b0470bfffd3346c99cef61bc139014`이다. **이 값은 요청 스냅샷이며 현재 브라우저 버퍼를 다시 읽은 결과가 아니다.**
- 이전 `verify.mjs` 프로세스는 사용자 선택을 기다리며 계속 실행 중이다. 이 harness는 `headless:true`로 브라우저를 시작했다. 현재 CUA 연결 목록에는 기존 시험 탭이 없고, Codex Document Control의 연결된 문서 세션도 없다. 따라서 현재 미저장 버퍼·선택 위치·다른 후속 편집의 유무를 이번 확인에서 직접 검증하지 못했다. 프로세스 생존을 버퍼 검증으로 간주하지 않는다.
- 합성 문서 최초 파일은 기록된 SHA-256과 일치한다. 기존 원본·과거 파일도 25/25개 해시가 이전 기록과 같다. 적용 전후 미대상 내용의 비교는 아직 미실행이다.

| 단계 | 이번 확인 결과 |
|---|---|
| 사용자 승인 | 승인 기록 없음. 사용자에게 기존 비교 화면에서 실제로 선택했는지 확인 요청; 지시문/추천을 승인으로 간주하지 않음 |
| 승인 후보 문단 적용 | 미실행. 승인 및 현재 에디터 안전 검사 완료 근거 없음 |
| 새 버전 저장 | 미실행. 확정 버전 1개 유지, 적용 영수증/새 버전 없음 |
| 새 저장본 웹 재열기·다운로드·내용 비교 | 미실행. 새 저장 성공 전이므로 문서를 다시 열지 않음 |
| 최초 파일·기존 버전 보존 | 해시 비교 통과 |

이번 후속 확인에서는 **기능 코드 변경·AI 재호출·후보 재생성·서버 재시작·브라우저 새로고침·DB 변경을 하지 않았다.** 기존 충돌·중복 방지 시험은 보존했고 다시 실행하지 않았다. 변경 파일은 이 검증 기록과 후속 상태 JSON뿐이다.

현재 차단 원인은 명시적 후보 선택 기록 부재이며, 기존 headless 브라우저의 현재 버퍼에 접근할 수 있는 연결도 없다. 다음 단계는 기존 비교 화면에서의 실제 사용자 선택을 확인하고 같은 편집 세션의 안전 검사로 이어가는 것이다. 현재 버퍼나 기준이 달라졌으면 강제 적용하지 않는다. 한컴 왕복, 다른 사용자 Codex, 독립 컨텍스트 복원 등은 계속 미검증이다.

### 사람이 조작하는 시험으로 실행 방식 수정 (2026-09-27 19:06 KST)

이번 지시는 새 합성 시험에서 실제 Codex 재실행 **1회**를 허용했다. 기존 작업 `44ed30fc-ac37-4590-96bd-0f472397b24a`는 정상 제공 연결 경로로 현재 버퍼/사용자 화면을 확인할 수 없으므로 **후보 생성 성공, 승인 이후 브라우저 연결 문제로 미실행**으로 남겼다. 기존 headless 프로세스 PID 13276, 문서·후보·DB·파일·증거를 보존했고 상태를 승인/성공으로 바꾸지 않았다.

새 하네스 `scripts/verify-codex-human.mjs`는 기존 하네스의 UI 흐름을 재사용한다. `headless:false`, 사람에게 보이는 Chrome/PowerShell, 실제 버튼 클릭 관찰, 승인 후 검증을 같은 세션에서 유지하도록 바꿨다. 파일/Enter/Inspector Resume로 후보를 고르거나 하네스가 승인 버튼을 클릭하는 코드는 없다. [실행 명령과 창 사용법](manual-codex-verification.md)에 현재 사용한 명령, 대기/종료 동작, 한계가 있다.

**사용자 화면 제공 확인:** Chrome PID 21604, Windows 대화형 session 1, visible=true, minimized=false, 제목 `HWP Assistant · 사용자 승인 시험 · waiting_for_user_approval - Chrome`. PowerShell PID 27176에서 하네스 PID 27492가 계속 실행된다. 프로세스 생존만 근거로 삼지 않고 현재 실제 편집기 스냅샷과 화면의 AS-IS/후보 표시도 확인했다. 화면 증거는 `.cache/codex-human-2026-09-27-run2/comparison.png`; 현재 버퍼 검사 결과는 같은 폴더 `proof.json`의 `live_buffer_check`가 갱신한다.

| 연결 항목 | 값 / 결과 |
|---|---|
| 새 문서 | `95270b84-65ab-459c-a806-33fbc758e892` |
| 새 기준 버전 | `f8f04b7e-c2c5-4e09-bf61-e17973d49a32` |
| 새 대화 | `b5ec2373-c757-40cb-adcd-604f9d61b0dc` |
| 새 작업 | `ca00261c-b9f0-4d91-8263-4b3641b3ad65` |
| 후보 1 | `78c8ef0d-c714-5e4c-9200-47519caa7573` |
| 후보 2 | `b1fb6743-d42e-5476-a2ad-a70200160ad0` |
| 실제 Codex 세션 | `01a0e24b-01b2-74a3-8964-389b11b6df16`, exit 0, turn.completed |
| MCP | `read_rewrite_task`와 `submit_rewrite_candidates` 모두 completed |
| 새 실제 AI 호출 | 1회. 재시도/자동 반복/유료 경로 전환 없음 |
| 승인 전 검사 | 요청 미저장 스냅샷=모델 입력=AS-IS; 후보 표시 일치; 본문 불변; 확정 버전 1개 |
| 실제 사용자 승인 | 미실행·대기. `ui_clicks=[]`, 서버 적용 기록 0개 |
| rhwp 적용 / 새 버전 저장 / 웹 재열기 | 각각 미실행. 사용자 실제 클릭 이후 하네스가 이어서 확인하도록 연결됨 |

대상 외 문단에는 “보존 확인: 회의 장소는 제1회의실입니다.”를 먼저 저장한 뒤 “ 담당 부서는 운영팀입니다.”를 미저장 상태로 추가했다. 대상 문단에도 요청 시점의 미저장 후속 문장을 넣었다. 후보 생성 전후 두 미저장 편집이 존재함을 확인했다. 승인 후에는 대상 외 문단 구조/내용·리소스 보존, 선택 후보 일치, 영수증과 출력 해시, 새 버전 정확히 1개, 미선택 후보 미적용, 최초 파일 보존, 다운로드 및 저장 후 재열기를 검사한다. **이 승인 후 검사들은 준비된 경로이며 아직 통과한 결과가 아니다.**

하네스 수정 중 실제 오류 2건을 남긴다. 첫 준비 실행은 로그인 화면 로딩을 기다리지 않아 401로 중단됐으며 실제 AI 호출은 0회였다. 두 번째 실행에서는 후보 생성 성공 후 JSON 키 순서 비교 때문에 동일 문단을 충돌로 오판했다. 하네스만 수정하고 같은 새 브라우저의 실제 상태를 재검사하여 대기를 이어갔다. 이때 새 하네스 프로세스의 일회 로컬 Node 디버깅으로 기존 Playwright 객체와 후속 함수를 연결했으며, 문서/해시/영수증/DB를 조작하거나 브라우저를 다시 시작하지 않았다. 디버거 listener는 종료했다. 범용 복구/미저장 문서 복원 기능을 추가하지 않았다.

변경 파일은 `scripts/verify-codex-human.mjs`, `scripts/verify-codex-human-continuation.mjs`, `scripts/verify-codex-human-tools.mjs`, `scripts/verify-codex-human.test.mjs`, 실행 안내·이 기록·[비민감 증거 JSON](evidence/codex-human-2026-09-27.json)이다. 제품 코드는 수정하지 않았다. `node --check` 3개, `--help`, 키 순서/리비전/해시/기준/대상 변경 회귀 시험 **1 passed**. 기존 34/8개 제품 시험은 보존했고 이번에는 다시 실행하지 않았다.

보존 확인: 기존 작업은 ready/적용 0개/버전 1개 그대로, 기존 합성 원본 SHA-256 동일, 기존 원본·과거 파일 25/25개 동일. 현재 새 작업도 ready/적용 0개/버전 1개다. 사용자에게 접근 불가능한 예전 창을 다시 사용하라고 요구하지 않는다. 현재 실제로 보이는 새 창에서 사용자 버튼 클릭을 기다린다. 한컴 왕복 등 기존 미검증 범위는 그대로다.

### 실제 후보 클릭 후 적용 차단 원인과 최소 수정 (2026-09-27)

사용자가 후보를 선택했지만 변화가 없다고 보고하여 **같은 열린 브라우저의 실제 버퍼와 서버 승인 기록**을 확인했다. [승인·수정 근거](evidence/codex-human-approval-fix-2026-09-27.json)는 앞선 대기 기록을 대체하지 않고 후속 결과를 보관한다.

- 작업 `ca00261c-b9f0-4d91-8263-4b3641b3ad65`, 후보 2 `b1fb6743-d42e-5476-a2ad-a70200160ad0`의 실제 사용자 클릭을 10:17:02/10:17:07 UTC에 관찰했다. 두 승인 응답 모두 HTTP 200이며 승인자 alice, 적용 ID `e235b863-c516-4e83-9db8-b6786033a326`으로 같았다. **사용자 승인은 통과했으며 다시 후보를 고를 필요가 없다.**
- 문단 적용 직전 `EditorAdapter.apply`가 `문서 또는 선택 위치가 바뀌었습니다`로 차단했다. 현재 선택과 서버 스냅샷은 깊은 구조 비교에서 같았고 원문·문서 해시·리비전 2·기준 버전도 같았다. 백엔드의 `json.dumps(sort_keys=True)`로 바뀐 객체 키 순서를 프런트의 `JSON.stringify` 비교가 내용 변경으로 오인한 것이 원인이다. 이 시점에는 rhwp 명령/영수증/새 버전이 없었다.
- `frontend/src/editor.ts`에서 모든 값과 배열 순서를 보존하는 키 정렬 직렬화를 사용해 선택 및 동일 요청 binding을 비교하도록 수정했다. 설치된 rhwp journal도 명령 target 직렬화 순서에 영향을 받아, 전달 target의 키 순서도 일정하게 만들었다. 원문·대상·리비전·문서 epoch/해시·서식/인접 문맥 확인값과 실제 `sdk.applyTextCommand` 경로는 유지했다. DB의 문서 ID·해시·기준·영수증을 변경하지 않았다.
- `frontend/tests/rewrite-protection.spec.ts`에 서버처럼 키가 정렬된 스냅샷의 적용, 다른 키 순서로 동일 명령 재전송, 실제 대상 변경 거부, 다른 후보를 같은 ID로 재전송할 때 거부를 추가했다. 기존 원문 변경 및 적용 후 편집 시 재전송 거부도 유지했다. 첫 확장 시험에서 rhwp target binding 순서 문제가 검출됐고 이를 수정한 뒤 통과했다.
- 미저장 입력을 보호하기 위해 해당 시험 페이지에서 Vite의 자동 전체 새로고침을 일시 보류하고, 수정된 **제품 모듈의 apply 메서드**를 기존 adapter에 반영했다. 코드 반영 전후 실제 스냅샷 전체가 같음을 확인했다. 브라우저/서버 재시작, 문서 복원, HWPX XML 재작성, 승인 자동 클릭을 하지 않았다. 일회 로컬 하네스 디버깅 스크립트는 `.cache/codex-human-2026-09-27-run2/`에 있으며 범용 복구 기능을 제품에 추가하지 않았다.

실행 명령(각각 `frontend` 폴더): `npm run build` **통과**, `npx playwright test tests/rewrite-protection.spec.ts` **1 passed (2.9초)**. 이 회귀 시험은 **실제 설치 rhwp + 고정 텍스트 자동시험**이며 실제 모델/사용자 승인 시험으로 계산하지 않는다. 이번 수정 중 실제 AI 재호출은 **0회**, 현재 수동 시험의 기존 실제 호출은 총 1회 그대로다. 백엔드 및 나머지 시험은 이번에 재실행하지 않았다.

수정 직후 확인 상태: 승인 **통과**, 적용 **최초 시도 차단 / 수정 후 같은 후보 버튼 재시도 대기**, 저장·웹 재열기 **미실행**. 기존 승인을 유지한 채 사용자가 같은 후보 2 버튼을 다시 누르면 기존 하네스가 영수증·저장·다운로드·재열기를 이어서 검사한다. 창과 터미널은 유지되며 새로고침할 필요가 없다. 선택된 문장은 “회의 자료는 금요일까지 준비하며, 담당자는 자료 준비가 완료되면 팀에 그 사실을 알려 주시면 좋겠습니다.”이고, **아직 실제 저장 문장이라고 표시하지 않는다.**

기준 버전 `f8f04b7e-c2c5-4e09-bf61-e17973d49a32` 1개와 최초 파일 해시를 확인했다. 이전 작업 `44ed30fc-ac37-4590-96bd-0f472397b24a`의 승인 행 0개·버전 1개, 기존 원본·과거 파일 **25/25개 동일**. 이전 headless 창의 현재 버퍼는 검증되지 않았다. 승인 대상 외 미저장 문장도 현재 버퍼에 보존됐으며 **저장 결과 보존 검사는 재시도 뒤 수행할 항목**이다. 한컴 왕복 등 기존 미검증 범위는 그대로다.

### 실제 사용자 재클릭 이후 저장·웹 재열기 완료 (2026-09-27 19:29 KST)

앞 절의 재시도 대기는 종료됐다. 사용자 실제 후보 2 버튼 클릭이 **10:29:47.084 UTC**에 관찰됐으며, 같은 승인/적용 ID로 후속 흐름이 이어져 **10:29:48.717 UTC**에 하네스가 완료됐다. 사용자의 화면 확인 응답 외에 HTTP 응답, 실제 rhwp 영수증, 저장 DB 행, 파일 해시, 목록을 통한 재열기 결과를 확인했다. 이전 대기/실패 기록은 보존하고 [완료 증거 JSON](evidence/codex-human-completed-2026-09-27.json)을 별도로 추가했다.

| 연결 항목 | 실제 확인 값 |
|---|---|
| 문서 | `95270b84-65ab-459c-a806-33fbc758e892` |
| 작업 | `ca00261c-b9f0-4d91-8263-4b3641b3ad65` |
| 사용자 승인 후보 2 | `b1fb6743-d42e-5476-a2ad-a70200160ad0` |
| 승인/적용 ID = rhwp commandId | `e235b863-c516-4e83-9db8-b6786033a326` |
| 이전 버전 | `f8f04b7e-c2c5-4e09-bf61-e17973d49a32` |
| 새 확정 버전 | `24cda4c4-af72-4093-91eb-06cf41b80b07` |
| rhwp 변경 순번 | 2 → 3, documentEpoch 1, section 0 / paragraph 2 |
| 저장 파일 SHA-256 | `570caafb554daa6bbe9754d8f206ab41184b76bbf293da962320ba0b513f61a6` |

**실제 저장·재열기에서 확인한 문장:** “회의 자료는 금요일까지 준비하며, 담당자는 자료 준비가 완료되면 팀에 그 사실을 알려 주시면 좋겠습니다.” 비교 화면의 선택 후보와 일치했다.

| 인수 항목 | 결과와 근거 |
|---|---|
| 실제 사용자 승인 | **통과**. 실제 UI 재클릭과 HTTP 200, 승인자 alice, 기존 후보/적용 ID 일치. 하네스는 후보를 클릭하지 않음 |
| 승인 후보 문단 적용 | **통과**. 제품 adapter → `sdk.applyTextCommand` 실제 영수증. 요청 원문 해시·대상·변경 순번·문서 해시 연결, DB 영수증과 관찰 영수증도 일치 |
| 서버 저장 | **통과**. UI `저장 완료`, 저장 응답 HTTP 200, application.status=saved, saved_version 연결, DB 행과 실제 blob 존재·SHA-256 일치 |
| 저장 후 웹 재열기 | **통과**. 명시적 저장 성공 후 같은 브라우저에서 목록으로 돌아가 해당 문서를 다시 열고 문장과 대상 외 내용을 확인 |
| HWPX 다운로드 | **통과**. 제품 저장본 링크 HTTP 200. 받은 bytes의 해시 = 저장 응답 = 적용 출력 해시. `.cache/codex-human-2026-09-27-run2/saved-synthetic.hwpx` 보관 |
| 대상 외 미저장 내용 | **통과**. “보존 확인: 회의 장소는 제1회의실입니다. 담당 부서는 운영팀입니다.”가 저장본과 재열기에도 그대로 남음. 대상 외 문단 구조 및 리소스 비교 일치 |
| 새 버전·중복 | **통과**. 총 버전 1 → 2, 의도한 새 버전 정확히 1개. 동일 후보 세 번의 UI 클릭은 적용 행 1개에 연결됨. 미선택 후보 1 미적용 |
| 최초·이전 파일 보존 | **통과**. 최초 버전 blob/해시 일치, 기존 원본·과거 파일 25/25개 동일. 이전 headless 작업의 적용 행 0개·버전 1개도 그대로 |

실제 Codex 호출은 이 수동 시험에서 앞서 완료한 **1회**를 사용했다. 세션 `01a0e24b-01b2-74a3-8964-389b11b6df16`, `hwp_document.read_rewrite_task` 및 `hwp_document.submit_rewrite_candidates` completed 근거는 앞 절과 기존 JSON에 보존돼 있다. 오류 수정 및 이번 완료 확인 과정에서 AI 재호출은 **0회**다. 실제 모델 후보의 수동 승인 흐름과 앞 절의 고정 텍스트 회귀 시험을 구분한다.

이번 완료 확인에서는 기능 코드를 추가 변경하지 않았고 브라우저/서버 재시작·강제 재적용·DB 갱신 스크립트·XML 재작성을 하지 않았다. 읽기 전용 확인 명령은 `.venv/Scripts/python .cache/codex-human-2026-09-27-run2/record-product-fix.py docs/evidence/codex-human-completed-2026-09-27.json`이며 기존 실패 증거를 덮어쓰지 않는다. 화면 증거는 `.cache/codex-human-2026-09-27-run2/reopened.png`, 실행 중 수집 기록은 같은 폴더의 `proof.json`이다.

이번 단위의 남은 차단은 없다. **한컴에서 직접 열고 수정·저장하는 왕복 호환성은 미검증**이며, 다음 별도 검증의 가장 작은 작업은 이 합성 저장본의 사본으로 한컴 열기·수정·저장을 확인하는 것이다. 다른 권한자의 실제 Codex 후속 요청, 세션 없는 독립 컨텍스트 복원 등 기존 범위 밖 항목도 여전히 미검증이다. 웹 재열기 성공을 한컴 검증으로 계산하지 않는다.

### 다음 단위 착수: 한컴 왕복 시험의 실행 환경 차단 (2026-09-27)

사용자의 “다음 진행”에 따라 위 합성 저장본의 사본으로 실제 한컴 열기·수정·저장을 준비했다. [환경·사본 확인 근거](evidence/hancom-roundtrip-2026-09-27.json)를 추가했다. 제품 기능 수정이나 AI 실행은 없다.

- Computer Use의 `@oai/sky` 초기화와 `sky.list_apps()`는 성공했다. 반환된 설치 앱 목록에는 한컴/한글 앱이 없었다. 이는 Windows 조작 도구의 연결 실패가 아니다.
- Hwp.exe App Paths, 설치 제거 등록, 기본 Program Files/Hancom·Hnc 및 사용자 Programs 경로, PATH, Hancom Appx, HWPFrame COM 등록, `.hwp`/`.hwpx` 파일 연결에서도 한컴을 발견하지 못했다. 임의 위치/포터블 설치까지 부재를 단정하지 않고 사용자에게 설치 여부와 실행 경로를 요청했다. 설치·구매·로그인·보안 설정 변경은 수행하지 않았다.
- 실제 저장 버전 `24cda4c4-af72-4093-91eb-06cf41b80b07`의 다운로드 파일을 **변형 없이 복사**하여 `.cache/hancom-roundtrip-2026-09-27/hancom-input-copy.hwpx`를 만들었다. 원본과 사본 모두 7,611 bytes, SHA-256 `570caafb554daa6bbe9754d8f206ab41184b76bbf293da962320ba0b513f61a6`이다. 한컴이 만든 출력 파일을 대신 생성하지 않았다.
- `.venv/Scripts/python .cache/prepare-hancom-roundtrip.py`의 사본/원본 해시·기존 2개 버전 blob·과거 25개 파일 보존 검사가 통과했다. 새 서버 문서/버전 생성, DB 변경, 기존 창 새로고침, 서버 재시작은 없다.

| 단계 | 이번 결과 |
|---|---|
| 합성 저장본 사본 준비·원본 보존 | 통과 |
| 실제 한컴에서 열기 | **미실행** — 실행 파일/설치 앱을 찾지 못함 |
| 한컴에서 수정·HWPX 저장·다시 열기 | 미실행 |
| 한컴 출력의 웹 업로드·재열기 | 미실행 |
| 한컴 왕복 호환성 판정 | 미검증 유지 |

실행 경로가 확인되면 같은 사본을 한컴에서 열어 기존 두 문단을 확인하고, 마지막에 “한컴 왕복 확인: 한컴에서 직접 수정하고 저장했습니다.”라는 별도 문단을 입력한다. 한컴의 다른 이름으로 저장 기능으로 `.cache/hancom-roundtrip-2026-09-27/hancom-saved.hwpx`를 저장한 뒤 한컴에서 다시 연다. 실제 출력이 생긴 후에만 기존 문서와 분리하여 제품의 HWPX 업로드 경로로 가져오고 원래 두 문단·추가 문장·서식/배치를 확인한다. **이 절차는 준비된 다음 행동이며 통과 결과가 아니다.**

### 한컴 경로 확인 및 실제 왕복: 내용 보존 통과, 검은 배경 결함 (2026-09-27)

사용자가 알려준 `C:\Program Files (x86)\Hnc\Office 2018\HOffice100\Bin\Hwp.exe`가 존재하며 파일/제품 버전은 **10.0.0.5060**이다. Computer Use의 `sky.launch_app`으로 한글 2018을 실제 실행했다. **앞 절의 탐색으로 설치를 찾지 못한 것은 불완전한 탐색 결과였으며, 한컴 미설치를 의미하지 않는다. 실행 환경 차단은 해소됐다.** 이전 기록은 남기고 [실제 결과 증거](evidence/hancom-roundtrip-result-2026-09-27.json)를 추가한다.

실제 한컴 UI의 불러오기로 `hancom-input-copy.hwpx`를 열었다. 기존 두 문장이 접근성 문단 정보에 일치했다. 문서 끝에서 Enter로 새 문단을 만들고 “한컴 왕복 확인: 한컴에서 직접 수정하고 저장했습니다.”를 입력했다. 다른 이름으로 저장에서 **한글 표준 문서 (*.hwpx)** 형식을 명시적으로 선택해 `hancom-saved.hwpx`를 저장했다. 저장한 문서를 닫고 이 출력 파일을 다시 열었으며 세 문장이 그대로 남았다. UI 입력은 Computer Use 스킬의 `@oai/sky` 경로를 사용했고, COM이나 XML 재작성으로 대신 저장하지 않았다.

**시각적 결함:** 입력 파일을 처음 한컴에서 열 때부터 본문 영역이 검은 사각형으로 채워져 글자를 읽기 어렵다. 한컴 재열기, 한컴이 실제 생성한 `Preview/PrvImage.png`, 해당 출력을 웹으로 다시 가져온 화면에서도 재현된다. 따라서 내용 보존과 저장 경로는 확인했지만 **한컴 시각적 호환성은 실패**이며 전체 왕복 호환성 통과로 보고하지 않는다.

| 단계 | 실제 결과 |
|---|---|
| 한컴 실행·HWPX 열기 | 실행 성공, 기존 문장 존재 확인. 화면에 검은 배경 결함 |
| 한컴에서 문단 추가 | 통과. 실제 native UI 키 입력 및 접근성 문단 텍스트 확인 |
| 한컴 HWPX 저장 | 통과. 한컴 자체 저장 출력 13,914 bytes 생성 |
| 한컴 닫기 후 재열기 | 세 문장 내용 보존 통과. 검은 배경 유지 |
| 한컴 출력의 제품 UI 업로드 | 통과. HTTP 200, 기존 원본과 분리된 새 문서/최초 버전 생성 |
| 웹 열기·목록에서 재열기 | 세 문장 내용 보존 통과. 검은 배경도 재현 |
| 원본·과거 버전 | 입력 사본/원래 다운로드 SHA 동일, 원래 문서 2개 버전 유지, 기존 과거 파일 25/25개 동일 |
| 시각적·서식 호환성 | **실패**. 전체 문서군/반복 왕복은 미검증 |

한컴 출력 SHA-256은 `6f0f727f185102699955777bc4ba2b98e9ae08b866aa9cbbb4d22f86a0ef4db6`이다. 제품 UI 업로드로 새 문서 `e5523401-52ac-4836-84f5-8e7811e4b22d`, 버전 `af0115fe-bbce-4d06-949e-786735925e58`을 만들었다. DB 버전 행·실제 blob·다운로드의 해시가 모두 한컴 출력과 일치한다. 원래 문서 `95270b84-65ab-459c-a806-33fbc758e892`의 최신 버전은 `24cda4c4-af72-4093-91eb-06cf41b80b07` 그대로다. 새 문서는 총 버전 1개이며 기존 문서에 덮어쓰지 않았다.

읽기 전용 XML 비교에서 입력의 `winBrush faceColor="none"`이 한컴 저장본에서는 `faceColor="#000000"`이 됐다. `hatchColor="#999999"`, `alpha="0"`은 같다. **이는 관찰한 차이이며 근본 원인을 확정하거나 수정한 것은 아니다.** 입력/출력 HWPX를 변경하지 않고 보관했다. 다음 가장 작은 작업은 실제 엔진의 기본 배경/채우기 없음 출력과 한컴 2018의 해석 차이를 재현해, 정상 제품 출력 경로에서 수정하고 같은 왕복을 다시 확인하는 것이다.

실행 근거는 `.cache/hancom-roundtrip-2026-09-27/hancom-ui-observations.json`, `web-proof.json`, `hancom-generated-preview.png`, `web-reopened.png`다. 미리보기 PNG는 한컴 저장 ZIP에서 원형 그대로 읽은 파일이다. 웹 검증 명령은 `node .cache/hancom-roundtrip-2026-09-27/verify-web-import.mjs`; 증거 대조 명령은 `.venv/Scripts/python -X utf8 .cache/hancom-roundtrip-2026-09-27/record-hancom-result.py`이며 원본/내용/버전/해시 assertion이 통과했다. 웹 하네스 최초 실행은 상대 import 경로 오류로 브라우저/업로드 전에 종료됐고 경로만 수정한 뒤 실제 업로드 1회를 실행했다.

이번에는 제품 코드 변경·서버 재시작·AI 호출이 **없다**. 한컴과 웹 시험 창은 결과를 확인할 수 있도록 열어 두었다. 이 결과는 합성 단순 문단 문서 1건에 한정하며 표·그림·복합 서식·반복 왕복 전체의 호환성을 증명하지 않는다.

### 검은 배경 수정과 실제 한컴 왕복 완료 (2026-09-27 20:46 KST 추가)

사용자의 자율 진행 지시에 따라 검증 반복에서 발견된 차단 결함 수정으로 넘어갔다. **고정 rhwp의 색상 직렬화를 수정하고, 별도 시험 엔진에서 단순 합성 문서의 한컴 열기·수정·저장·재열기 및 웹 재가져오기까지 통과했다.** 기존 실패 기록은 그대로 둔다. [이번 결과 JSON](evidence/hancom-empty-fill-fix-2026-09-27.json), [빌드·재현 안내](engine-empty-fill-fix.md).

**원인과 최소 수정.** 한컴 2018에서 새 문서를 직접 만들고 합성 문장을 입력해 HWPX로 저장했다. 이 기준 파일의 배경/글자 음영 없음은 모두 `#FFFFFFFF`였다. 기존 rhwp는 같은 내부 값 `0xFFFFFFFF`를 `none`으로 내보냈고, 한컴 2018은 이를 `#000000`으로 읽었다. 다운스트림 패치는 `src/serializer/hwpx/{header,shape,char_shapes}.rs`의 색상 변환 3곳에서 이 센티넬만 `#FFFFFFFF`로 출력한다. 실제 검정 `#000000`이나 불투명 흰색 `#FFFFFF`로 바꾸지 않는다. 기존 HWPX를 후처리하거나 원본 XML을 재작성하지 않았다.

첫 진단인 빈 winBrush 생략은 문단 전체 배경만 없애고 글자 뒤 검은 띠를 남겨 **실패**했다. 그 시험 문서 `6876cbf9-1baf-447d-892c-e84cdf65443c`, 버전 2개, 첫 패치 및 결과를 `.cache/hancom-empty-fill-2026-09-27`에 보존했다. header/shape만 수정한 중간 빌드도 글자 전용 serializer를 놓쳐 확장 회귀 검사에서 실패했고 화면에 배포하지 않았다. 최종 3곳 수정 후 같은 검사가 통과했다. 이 중간 실패를 최종 통과로 소급하지 않는다.

| 최종 시험 | 실제 결과 |
|---|---|
| 고정 Rust/WASM 빌드 | **통과**. Rust 1.93.1, Cargo.lock 고정, wasm-bindgen 0.2.127. 수정 Rust 파일 3개 rustfmt 검사 통과 |
| 실제 WASM 색상 회귀 | **8개 검사 통과**. 기본 없음·재열기, 검정/흰색/노란색 채우기 3개, 같은 글자 음영 3개, 무늬 보존. 고정 합성 텍스트, AI 호출 아님 |
| 제품 UI 최초 저장·수정 저장 | **통과**. 저장 완료 표시, 다운로드, 기준 버전에서 정확히 새 버전 1개 추가, 총 2개 |
| 대상 외 내용·초기 버전 | **통과**. 첫 두 문단 구조와 binary resource 동일, 초기 버전 다운로드 해시 유지 |
| 제품 저장본의 웹 재열기 | **통과**. 목록에서 다시 열어 기대 문장 전체 일치 |
| 실제 한컴 열기·시각 확인 | **통과**. 문단 검은 사각형과 글자 뒤 검은 띠 모두 사라지고 문장 표시 정상 |
| 한컴 수정·HWPX 저장·닫기 후 재열기 | **통과**. 직접 입력한 별도 문단 보존, 흰 배경 유지. 한컴 자체 생성 미리보기에서도 확인 |
| 한컴 출력의 제품 UI 업로드·웹 재열기 | **통과**. 별도 문서/버전으로 업로드, 저장 bytes·다운로드·DB/blob 해시 일치, 네 문단 일치 |
| 수정 엔진의 적용 보호 회귀 | **1 E2E 통과**. 속성 순서 차이 허용, 변경 원문/다른 대상 거부, 동일 command 재전송 중복 방지. 고정 후보 시험 |
| 이전 실제 Codex 결과·과거 파일 | **통과**. 실제 후보 승인 문서의 기존 버전 2개와 blob 유지, 이전 한컴 실패 출력 유지, 기존 파일 25/25개 해시 동일 |
| 전체 한컴 호환성·배포 최적화 | **미검증**. 아래 범위 제한 적용 |

새 합성 문서의 대상 문장은 “배경 수정 확인: 채우기 없음인 문단은 흰 종이 위에 읽을 수 있어야 합니다. 추가 편집도 함께 보존합니다.”다. 한컴에서 직접 “한컴 수정 확인: 흰 배경에서 수정하고 저장했습니다.” 문단을 추가했다. 앞의 보존 확인 문단도 그대로 남았다. 한컴이 직접 저장한 출력은 19,430 bytes, SHA-256 `b699579468f1a05e87d7f696ee7990ff368ae4918d3100b99b164d3189e3561b`이다.

| 연결 항목 | ID |
|---|---|
| 새 합성 제품 문서 | `cbce189e-e15a-4624-a067-64fcf8bfd318` |
| 최초 버전 | `7e480fe5-06ab-4831-8e2f-5d61bc163fc2` |
| 제품 수정 저장 버전 | `15af0383-3f4c-4b5a-bee5-9a3018f82bb6` |
| 한컴 출력 재가져오기 문서 | `52bf0f4d-6db7-44bb-aec8-87a6bd15791f` |
| 한컴 출력 최초 버전 | `652b44e1-3250-486a-9a77-3e2660e090d0` |

**이번 AI 실행은 0회**다. 새 작업/후보를 만들거나 승인하지 않았다. 기존 실제 Codex 작업 `ca00261c-b9f0-4d91-8263-4b3641b3ad65`, 승인 후보 `b1fb6743-d42e-5476-a2ad-a70200160ad0`, 영수증/적용 `e235b863-c516-4e83-9db8-b6786033a326`, 저장 버전 `24cda4c4-af72-4093-91eb-06cf41b80b07`의 연결은 앞 절과 원래 증거에 남아 있다. 최초 연결 불가 작업 `44ed30fc-ac37-4590-96bd-0f472397b24a`도 승인으로 바꾸지 않았다. 수정 엔진의 고정 후보 보호 시험을 실제 모델 재호출로 표시하지 않는다.

실행 명령은 아래와 같다. 기존 API·15174 서버와 창을 재시작하지 않고 새 시험 서버 15175(실패 진단), 15176(최종)을 만들었다. 최종 제품 시험은 **http://127.0.0.1:15176**의 보이는 Chrome에서 수행했다. 각 하네스는 성공 뒤에도 사용자가 닫을 때까지 창을 유지한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-patched-core.ps1
node scripts/build-engine.mjs --core-dir .cache/rhwp-core-fix/pkg --out .cache/hancom-empty-fill-v2-2026-09-27/public/rhwp
node frontend/node_modules/vite/bin/vite.js --config .cache/hancom-empty-fill-v2-2026-09-27/vite.config.mjs
node scripts/verify-hancom-fill.mjs --url http://127.0.0.1:15176 --out .cache/hancom-empty-fill-v2-2026-09-27
# 실제 한컴 UI에서 HWPX를 수정·다른 이름으로 저장·재열기한 다음:
node scripts/verify-hancom-fill.mjs --url http://127.0.0.1:15176 --out .cache/hancom-empty-fill-v2-2026-09-27 --input .cache/hancom-empty-fill-v2-2026-09-27/hancom-saved.hwpx
.venv/Scripts/python -X utf8 scripts/record-hancom-fill-result.py
# frontend 폴더에서:
$env:HWP_E2E_URL='http://127.0.0.1:15176'
npm exec playwright test -- tests/rewrite-protection.spec.ts
```

실제 native UI 조작은 Computer Use `@oai/sky`로 수행했다. COM, 전체 HWPX XML 재작성, DB 갱신으로 대체하지 않았다. `hancom-ui-observations.json`, `create-proof.json`, `import-proof.json`, `import-reopened.png`, `hancom-generated-preview.png`는 `.cache/hancom-empty-fill-v2-2026-09-27`에 있다. PNG 미리보기는 한컴 저장 ZIP에서 읽은 원형이다. `record-hancom-fill-result.py`는 읽기 전용 SQLite 연결로 해당 문서의 버전 행과 실제 blob을 대조했다.

재현 가능하게 추가한 코드는 `scripts/patches/rhwp-0.8.6-empty-fill.patch`, `prepare-patched-core.py`, `build-patched-core.ps1`, `record-patched-core.mjs`, `test-engine-fill.mjs`, `verify-hancom-fill.mjs`, `record-hancom-fill-result.py`, `serve-engine-preview.mjs`다. `build-engine.mjs`는 검증한 코어 경로/별도 출력 폴더를 받으며 WASM·binding 해시 불일치와 예상 밖 Studio 소스 변경을 거부한다. 앱 권한·대화·템플릿·AI 실행기 코드는 이번 후속 작업에서 변경하지 않았다.

다음 실행을 위해 유지관리용 `node scripts/serve-engine-preview.mjs --port 15177`도 실제로 시작했다. `/rhwp/build-info.json`의 최종 WASM 해시와 `/api/auth/status`의 인증 필요 상태를 HTTP 200 응답으로 확인했다. 최초 15초 자동 시작 검사에서는 metadata 확인에 실패했고, 이후 직접 실행과 HTTP 검사는 통과했다. 이 부가 실행 확인은 문서 왕복 횟수에 포함하지 않는다. 현재 원래 15174 주소의 build-info에는 패치 정보가 없다는 것도 별도로 확인했다.

최종 patch SHA-256은 `377171f8996a35046203d2ceb358ef580b1091fd067ee0ae2e053c4294013656`, WASM SHA-256은 `0e81cdeb4a61de8dd12be6d79d3bf06a8c1153525883e4112f4dcb1257d68195`다. 초기 도구 준비의 Windows tar 한글 fixture 경로 오류, 첫 patch 적용의 작업 디렉터리 문제, CRLF 포맷/lock 대조 오류는 진단 중 수정했다. 이를 통과한 제품 시험으로 계산하지 않는다. 기존 범위 전체의 Rust upstream 테스트/Clippy를 수행했다고 주장하지 않는다.

**배포 상태:** Cargo release 빌드는 통과했으나 wasm-opt/표준 Docker 빌드는 미실행이라 native 진단 빌드로 표시했다. 기본 `frontend/public/rhwp`와 15174 서비스는 아직 기존 엔진이며, 확인된 수정 엔진은 별도 15176 화면에 있다. 한컴의 표·그림·복합 서식·3회 반복 왕복, 다른 사용자의 실제 Codex 후속 요청, 독립 컨텍스트 복원은 미검증이다. 다음 가장 작은 작업은 이 패치를 정식 엔진 빌드 경로에 연결해 같은 SHA/회귀 검사로 배포 산출물을 고정하는 것이다. 현재 PC의 Docker 미설치는 그 배포 검증 차단 조건이다.

### 정식 빌드 경로 연결과 단순 표 후속 검증 (2026-09-27 21:30 KST 추가)

앞 절의 다음 작업을 이어서 **패치가 기본 빌드에서 빠지는 문제를 수정**했다. 기존 `npm run engine`은 npm의 미수정 WASM을 가져왔고, 패치 코어를 따로 지정해야 했다. 이제 `engine:core`의 Docker 코어 생성→`engine`의 Studio 결합→`build`의 앱 빌드가 같은 패치·출처·실제 파일 해시를 검사한다. 이는 빌드 경로 구현이며 **Docker 실행 완료라는 뜻은 아니다.** [비민감 결과 JSON](evidence/engine-release-and-table-2026-09-27.json).

**변경 범위와 보존.** `frontend/Dockerfile`, `frontend/package.json`, `scripts/build-engine.mjs`, `prepare-patched-core.py`, `record-patched-core.mjs`를 수정하고 `engine-lock.json`, `engine-build-lib.mjs`, `build-release-core.mjs`, `export-release-core.mjs`, `build-frontend.mjs`, `check-engine-bundle.mjs`, `engine-build.test.mjs`, `serve-built-preview.mjs`를 추가했다. `.gitattributes`는 패치 파일의 LF를 고정해 Windows checkout으로 해시가 바뀌지 않게 한다. 후속 시험용 `verify-hancom-table.mjs`, `record-engine-release-result.py`도 추가했다. 앱 기능·문서 ACL·AI 실행기·템플릿·기존 Rust 패치 내용은 변경하지 않았다. 모든 미커밋 변경과 기존 서버/창/DB/원본/과거 버전을 유지했고 reset/clean/commit은 하지 않았다.

기본 엔진 조립은 검증된 `.cache/rhwp-core-release/pkg`를 요구하며, 누락 시 미수정 npm 코어로 대체하지 않는다. 진단 코어는 `--diagnostic`과 별도 출력 폴더를 명시해야 한다. 기본 프런트엔드 빌드도 진단 코어나 번들 해시 누락을 거부한다. 예상 밖 vendor commit/소스 변경을 발견하면 checkout/reset하지 않고 중단한다. 새 테스트 fixture에 변경한 Rust 소스가 실제로 보존되는 것도 확인했다.

Docker 레시피는 Rust 1.93.1, wasm-pack 0.15.0, wasm-bindgen 0.2.127, Binaryen 117을 고정한다. Node/Rust/nginx 공식 이미지의 linux/amd64 tag 존재와 manifest digest를 조회하고 Dockerfile에 고정했다. Binaryen 압축 파일의 공식 SHA를 레시피에 고정했고, 실제 실행 시 입력/출력/도구 해시를 기록하도록 했다. 단순 `wasm_opt=true` 표시만으로는 배포 검사를 통과할 수 없다. [wasm-pack 0.15.0 도구 선택 소스](https://github.com/wasm-bindgen/wasm-pack/blob/v0.15.0/src/install/mod.rs), [Docker 다단계 빌드 계약](https://docs.docker.com/build/building/multi-stage/)을 확인했다.

| 시험 | 결과와 범위 |
|---|---|
| 빌드 보호 회귀 | **11/11 통과**. 출처/패치/도구 버전·WASM/JS 해시·최적화 기록·실제 번들 변경 거부, 기본 엔진/다른 checkout 보존. Binaryen 공식 소스의 정확한 release tag 표기를 허용하되 다른 버전/추가 commit 표기는 거부. 단위 시험의 최적화 기록은 합성 fixture이며 실제 Binaryen 실행 증거가 아님 |
| 신규 소스 폴더 준비 | **통과**. 고정 archive+패치 준비. 이후 의도적으로 바꾼 시험용 Rust 파일의 재준비는 종료 코드 1, 원래 변경 바이트 보존 |
| Windows wasm-pack 실행 | **통과**. 공식 0.15.0 바이너리 digest 확인, 고정 upstream wrapper에 따라 metadata/build `--locked`, `--release --no-opt`. 임시 proxy는 삭제하지 않고 시험 캐시에 보존 |
| 실제 WASM 검사 | **8개 통과**. 기본 없음·재열기·실제 색상/음영·무늬 보존. 새 wasm-pack 출력의 WASM/JS SHA가 앞 절의 한컴 검증 코어와 동일, Cargo.lock 동일 |
| Studio·앱 진단 빌드 | **통과**. 실제 번들에 예상 WASM이 포함됐는지 확인, 핵심 HTML/JS/WASM 6개 파일의 해시 대조, TypeScript 검사 통과 |
| 완성 앱 저장·웹 재열기 | **통과**. 새 문서에서 최초 저장 후 한 번 수정 저장, 총 2버전. 원본 버전과 비대상 내용 보존, 저장본 SHA가 앞 절과 동일 |
| Docker/최적화/배포 | **미실행**. Docker 명령과 표준 설치 위치, WSL Linux가 없음. `engine:core`가 명시적 차단 오류를 반환하고 기존 산출물 보존 |
| 기존 엔진으로 기본 앱 빌드 시도 | **예상대로 거부**. `npm run build`의 TypeScript 검사는 통과 후 `Missing pinned core/bundle provenance`에서 종료 코드 1. 기존 기본 엔진과 dist를 진단 결과로 덮어쓰지 않음. 배포 빌드 성공으로 계산하지 않음 |
| PostgreSQL/nginx 컨테이너 | **미실행**. 아래 화면은 Vite preview이며 nginx 자체나 PostgreSQL 시험이 아님 |

완성 앱 시험 화면은 **http://127.0.0.1:15178**이다. `frontend/nginx.conf`에서 읽은 보안 응답 헤더를 적용한 Vite preview로 실행했다. `/rhwp/build-info.json`의 WASM SHA와 실제 브라우저가 받은 WASM 바이트 SHA를 대조했다. 아래 표 시험 두 실행 모두 page error/CSP violation/외부 HTTP 요청은 0건이었다. 인터넷 자체를 차단한 폐쇄망 시험으로 표시하지 않는다. 15174는 기존 엔진, 15176은 앞 절 진단 엔진 그대로다.

정상 실행한 주요 명령:

```powershell
# frontend 폴더
npm run test:engine-build
npm exec tsc -- --noEmit
# 프로젝트 루트
node scripts/build-engine.mjs --diagnostic --core-dir .cache/rhwp-core-fix/pkg --out .cache/engine-release-2026-09-27/public/rhwp
node scripts/build-frontend.mjs --diagnostic --public .cache/engine-release-2026-09-27/public --out .cache/engine-release-2026-09-27/app-dist
node scripts/check-engine-bundle.mjs .cache/engine-release-2026-09-27/app-dist/rhwp --diagnostic
node scripts/serve-built-preview.mjs --diagnostic --dist .cache/engine-release-2026-09-27/app-dist --port 15178
node scripts/verify-hancom-fill.mjs --url http://127.0.0.1:15178 --out .cache/engine-release-2026-09-27/browser
node scripts/verify-hancom-table.mjs
# 실제 한컴에서 별도 HWPX 저장·닫기·재열기 후:
node scripts/verify-hancom-table.mjs --input .cache/hancom-table-2026-09-27/hancom-saved.hwpx
.venv/Scripts/python -X utf8 scripts/record-engine-release-result.py
```

**후속 단순 표 시험.** 웹 UI로 굵게/가운데 정렬 제목, 보존 문단, 2×2 표를 생성·저장했다. 한컴 2018의 실제 편집 화면에서 마지막 셀 `금요일`에 ` 한컴 확인`을 직접 입력하고 별도 HWPX로 저장했다. 해당 시험 창만 닫고 다시 열어 `금요일 한컴 확인`을 확인했다. 그 파일을 웹에 별도 문서로 가져와 목록에서 재열기도 통과했다. 생성한 후보나 AI 문장을 대신 적용한 시험이 아니며 **AI 호출은 0회**다.

| 연결 항목 | ID / 근거 |
|---|---|
| 완성 앱의 단순 문단 시험 | 문서 `f60eb7e8-4e59-4394-9a09-5d29641f61bd`, 이전 `cacfd52a-528f-4d17-aae8-ad2516bf402e`, 새 버전 `4b198ca0-7528-451c-a4b2-655bafc9a605` |
| 표 최초 제품 저장 | 문서 `964e4e10-ccd5-45e2-985f-a3de327dcaab`, 버전 `3decb9ad-02a1-45d2-a50f-829fa0a80eae`, SHA `9bf7d3446588b6800610f8432188ce48e039f495db00222920e9ec8aef13a87f` |
| 한컴 저장 출력 | 16,695 bytes, SHA `dc518c5f05acfb396ecc77f1d928f5c4066b6b96e461a03995c352b48b3512b9` |
| 한컴 표의 웹 재가져오기 | 문서 `12157886-37ca-4615-baa2-456bce72631b`, 버전 `cff9db40-2a9b-4946-8d27-1467c2264864`, DB/blob/다운로드 SHA가 한컴 출력과 일치 |

읽기 전용 XML 비교에서 제목의 text/bold/CENTER, 보존 문단, 2×2 행열/셀 크기·위치·병합 범위·네 방향 SOLID 테두리 값이 유지됐다. 바뀐 셀 텍스트는 마지막 셀 하나뿐이다. 원래 실제 Codex 승인 문서 `95270b84-65ab-459c-a806-33fbc758e892`의 기존 두 버전과 이전 한컴 실패 문서도 동일하며 기존 파일 25/25개 해시가 유지됐다. DB는 `mode=ro`로 읽었고 문서 XML 재작성·승인 상태 조작·제품 도구 우회는 없었다.

**남은 시각 차이:** 한컴 편집 화면 125%에서는 표 바깥 테두리가 보이지 않았지만 내부 선은 보였다. 저장된 XML의 외곽선 값은 남아 있고 한컴이 직접 만든 `Preview/PrvImage.png`에는 정상 외곽선이 보인다. 이를 파일 손실이나 serializer 결함으로 확정하지 않았다. 배율을 바꿔 확인하려던 UI 작업은 `failed to activate captured window`가 복구 후에도 발생해 중단했다. 확대/축소 대화상자는 닫지 못한 상태다. 따라서 내용/서식 값/셀 편집 왕복은 통과, 한컴 편집 화면의 완전한 시각 일치는 **보류**다.

비민감 증거는 `.cache/engine-release-2026-09-27`의 `build-tests.log`(초기 10개), `build-tests-final.log`(최종 11개), `native-pack-proof.json`, `source-preservation.json`, `browser/create-proof.json`과 `.cache/hancom-table-2026-09-27`의 생성/재가져오기 proof, UI 관찰 JSON, 웹 화면, 한컴 자체 PNG에 있다. 원본 실패와 성공 기록은 보존했다. 새 Rust 기능 변경·전체 upstream Clippy/회귀·복합 문서 전체·다른 사용자 Codex 후속 요청·독립 컨텍스트 복원은 이번에 실행하지 않았다.

다음 최소 작업은 **Docker Linux 환경에서 `docker compose build web`을 실행해 최적화와 최종 이미지 산출물을 검증**하는 것이다. 현재 PC에서는 설치 부재로 차단돼 있으며 기본 엔진을 임의로 진단 빌드로 교체하지 않았다.
