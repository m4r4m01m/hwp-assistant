# 사내 HWPX 문서함 + AI 검토·문서 대화

실제 rhwp 편집기에서 HWPX를 열고 수정한 후 서버에 **새 불변 버전**으로 저장한다. 원본 다운로드와 엔진을 거친 재저장을 구분한다. 사용자별 토큰 인증을 설정하면 문서 권한·공유/개인 대화·자체 컨텍스트가 적용된다. 미설정 시 `local` 단일 사용자 개발 모드다.

2026-09-27: 기존 앱에 변경분 저장, 문서 권한·대화·컨텍스트, Codex MCP 브리지, HWPX 양식 추천·새 문서 적용을 추가했다. [사용·설정 안내](docs/collaboration-and-mcp.md), [이번 실제 검증 결과](docs/verification-2026-09-27.md)를 참조한다. 기존 요구사항 계획은 완료 보고로 변경하지 않았다.

현재 로컬 검증: Windows, Node 24.15.0, Python 3.14.4, Chrome. React 19.2.0 / TypeScript 5.9.3 / FastAPI 0.121.0 / SQLAlchemy 2.0.44를 채택했다. 기존 로컬 애플리케이션은 없었고 계획 v0.4의 초기 제안을 따랐다. 운영 구성은 PostgreSQL 17.6 + 파일 볼륨이며, Docker가 없는 개발 PC에서도 실행하도록 SQLite 개발 대안을 제공한다. **SQLite 테스트는 PostgreSQL 실측 결과가 아니다.**

## 빠른 실행: Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build
```

[문서함](http://127.0.0.1:8080)으로 접속한다. 최초 빌드는 npm/PyPI/GitHub/컨테이너 레지스트리에 연결한다. 이미지 생성 뒤 실행은 모델을 자동 설치하거나 다운로드하지 않는다. API와 DB 포트는 외부에 노출하지 않고 웹 포트도 loopback에만 바인딩한다.

이 작업 환경에는 Docker/Podman/PostgreSQL 실행 파일이 없어서 Compose 빌드·실행은 **미검증**이다. 아래 네이티브 실행은 실제 검증했다. 사내 반입 전 Compose/PostgreSQL 시험을 수행해야 한다.

## Windows에서 API·웹 개발 실행

저장소 루트에서 준비한다. `.env`는 Compose용이며 네이티브 Python 프로세스가 자동으로 읽지 않는다. 아래 엔진 생성에는 Docker가 필요하다. Docker 없는 PC의 별도 진단 빌드·실행은 [수정 엔진 안내](docs/engine-empty-fill-fix.md)를 따른다. 이미 실행 중인 문서함은 이 준비 명령 때문에 종료하지 않는다.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend/requirements.lock.txt
cd frontend
npm ci
npm run engine:core
npm run engine
npm run build
cd ..
```

터미널 1, 저장소 루트:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

터미널 2:

```powershell
cd frontend
npm run dev
```

[개발 문서함](http://127.0.0.1:5173), [API 문서](http://127.0.0.1:8000/docs).
`engine:core`는 수정 코어를 Docker Linux 빌드로 생성한다. Docker 없는 PC는 기존 실행을 유지하고 [별도 진단 빌드 안내](docs/engine-empty-fill-fix.md)를 따른다. 수정되지 않은 npm 코어로 자동 대체하지 않는다. 현재 PC의 기본 엔진을 이 문서 수정만으로 교체한 것은 아니다.
`data/metadata.db`와 `data/files/`가 재시작 후 유지된다. 실문서·키·데이터 폴더는 Git에 포함하지 않는다. 종료는 각 터미널의 Ctrl+C.

## 사용자 동작

시험용 [합성 문단·표 HWPX](samples/paragraph-and-table.hwpx)를 바로 업로드할 수 있다. 실제 rhwp 브라우저 편집·재저장 시험에서 생성한 파일이며 회사 자료가 아니다.

1. HWPX 업로드 → 목록에서 문서명 클릭 → rhwp의 한글·문단·서식·표 도구로 편집.
2. 상단 **저장** → 서버 확정 후 **저장 완료** → **저장본 다운로드**.
3. 목록의 **새 버전 업로드**로 같은 문서에 한글 수정본 등록. 같은 파일명 신규 업로드는 다른 문서가 된다.
4. **버전 기록**에서 과거 파일 다운로드 또는 복원. 복원도 새 버전이다.
5. **새 문서**는 엔진의 정상 빈 문서 생성 경로를 사용한다. 첫 저장 시 HWPX로 직렬화한다.
6. **다른 이름으로 저장**은 별도 문서 ID를 생성한다. 현재 문서는 유지된다.
7. 변경이 있으면 20초 간격 자동 저장. 수동/자동 저장은 하나의 진행 상태를 공유한다. 저장 중 추가 편집은 계속 미저장으로 표시한다.
8. 충돌·세션 만료 시 **복구용 다운로드**로 현재 버퍼를 보관하고, 목록으로 돌아가 최신 문서를 연다. 자동 병합은 없다.

편집 세션은 120초 만료, 30초 간격 갱신이다. 닫힌 탭이 세션을 반납하지 못하면 만료를 기다린다. 서버 응답 유실 시 같은 바이트·기준 버전·요청 ID로 재시도한다. 이미 최신 버전이 바뀌면 오래된 영수증을 저장 성공으로 표시하지 않는다.

## AI 설정

AI를 설정하지 않아도 문서 업로드·편집·저장·다운로드가 동작한다. PoC는 보존했으며 공용 전역 messages는 사용하지 않는다. SDK 의존성 없이 같은 OpenAI 호환 `/chat/completions` 계약을 httpx로 호출한다. 공급자 경계는 `backend/app/llm.py`의 `LlmProvider`다.

| 환경변수 | 예시 / 의미 |
|---|---|
| `LLM_PROVIDER` | `openai-compatible`; 미설정이면 503 |
| `LLM_BASE_URL` | 네이티브: `http://127.0.0.1:1234/v1` |
| `LLM_API_KEY` | `lm-studio`는 예시. 실제 키는 `.env` 또는 실행 환경에만 보관 |
| `LLM_MODEL` | `google/gemma-4-e4b`는 PoC 예시. UI의 **AI 연결·모델 확인**으로 `/models` 실제 ID 확인 |
| `LLM_TIMEOUT_SECONDS` | `30` |
| `DATABASE_URL` | Compose는 PostgreSQL, 네이티브 기본값 `sqlite:///./data/metadata.db` |
| `STORAGE_DIR` | 네이티브 기본 `./data/files`, Compose `/data/files` |
| `SESSION_TTL_SECONDS` | 기본 `120`; UI 갱신 주기보다 충분히 길게 설정 |

네이티브 실행에서 연결하려면 API 실행 전 터미널에 설정한다.

```powershell
$env:LLM_PROVIDER='openai-compatible'
$env:LLM_BASE_URL='http://127.0.0.1:1234/v1'
$env:LLM_API_KEY='lm-studio'
$env:LLM_MODEL='google/gemma-4-e4b'
$env:LLM_TIMEOUT_SECONDS='30'
```

`localhost`는 호출하는 프로세스가 속한 환경이다. 네이티브 API는 개발 PC, 컨테이너 API는 해당 컨테이너를 가리킨다. Compose에서 PC의 모델에 연결하려면 `host.docker.internal`을 사용하고 모델 서버의 바인딩·방화벽을 확인한다. 다른 Codex 호스트/격리 런타임이면 그 환경의 localhost이며 PC의 모델을 의미하지 않는다. 이번 실행은 이 Windows PC의 네이티브 프로세스였고 `127.0.0.1:1234/v1/models`는 **연결 거부**였다. 회사 폐쇄망 접속은 시도하지 않았다.

운영 모델 명칭 **Qwen 3.8**의 정확한 ID·크기·API는 미확인이다. 임의로 다른 Qwen ID로 치환하지 않는다. Ollama 설치는 필요 없다. 외부 유료 API로 자동 대체하지 않고 OpenAI 유료 서버 도메인도 거부한다. 모델 서버 URL은 관리자가 제공하는 로컬·사내 주소만 설정한다.

검토는 커서가 놓인 **본문 문단 전체**를 대상으로 한다. 표 셀·필드·컨트롤·혼합 글자 서식 문단은 엔진의 안전 명령 대상이 아니다. 문서 첫 문단에 구역 설정 컨트롤이 있으면 다음 본문 문단을 사용한다. 원문·수정안·이유를 표시하며 승인 시 문서 ID/버전, 편집 리비전, 선택 위치, 원문·서식·인접 문맥 해시를 검증한다. 승인 적용은 엔진의 단일 실행 취소 트랜잭션이다.

## 자체 호스팅과 재현

`@rhwp/editor` **0.8.6**, npm 배포 `gitHead` **e8800c8def63449808a4092798442652ed460552**의 Studio·Rust 코어에 한컴 색상 없음 패치를 적용한다. `npm run engine:core`는 Docker에서 고정 Rust/wasm-pack/wasm-bindgen/Binaryen으로 코어를 생성하고 실제 WASM 회귀 검사를 수행한다. `npm run engine`은 코어 출처·해시·최적화 기록을 검사한 뒤 같은 커밋의 Studio와 묶는다. npm의 미수정 WASM으로 자동 대체하지 않는다. 고정값은 `scripts/engine-lock.json`, 소스는 `.cache/rhwp`, 코어는 `.cache/rhwp-core-release/pkg`, 결과는 `frontend/public/rhwp`다. 결과를 Vite 또는 nginx가 직접 제공한다. 공개 데모 iframe은 사용하지 않는다.

`npm run build`도 실제 번들 파일 해시를 전후 검사한다. 기본 경로는 진단 산출물을 거부하며, Windows 진단은 명시적 `--diagnostic`과 별도 출력 경로가 필요하다. Docker 레시피 연결과 Docker 실행 통과는 구분하며, 현재 실행 근거는 [검증 기록](docs/verification-2026-09-27.md)에 남긴다.

`scripts/assistant-bridge.ts.txt`는 SDK에 없는 원문/서식 증거·손실 보고·원자적인 저장 리비전 확인을 추가한다. 텍스트 변경은 공개 `applyTextCommand`를 사용한다. `chrome=embed`로 엔진의 별도 파일 열기·저장을 차단해 호스트가 문서 수명주기를 관리한다. 폰트는 업스트림 `assets/fonts`의 번들 자원을 자체 제공하고 `RHWP_DISABLE_EXTERNAL_WEBFONTS=1`로 CDN 폰트를 끈다. 회사 글꼴은 추가하지 않았다. 업스트림 LICENSE, THIRD_PARTY_LICENSES, fonts/FONTS.md와 포함된 폰트 고지를 배포 디렉터리에 보존한다. 전체 라이선스 감사가 완료된 것은 아니며 반입 전 폰트별 고지를 재확인한다.

의존성은 `frontend/package-lock.json`, `backend/requirements.lock.txt`, 업스트림 고정 커밋의 `rhwp-studio/package-lock.json`에 고정한다. 프런트엔드 Docker의 Node/Rust/nginx 베이스는 공식 레지스트리에서 확인한 digest도 고정했다. 백엔드·DB 이미지는 기존 버전 태그를 유지하며 전체 공급망 감사는 미완료다. 인터넷 없이 새로 빌드하려면 의존성 캐시 또는 사전 빌드 이미지를 별도로 반입해야 한다.

## 저장 구조와 마이그레이션

`documents`: 표시 이름, UUID, 최신 버전, 임대 토큰/만료.
`versions`: UUID, 문서 ID, 기준 버전, UUID 파일 키, 크기, SHA-256, 시각, 원인, 멱등 키, 요청 지문, 세션 해시.

PostgreSQL에서는 문서 행 `FOR UPDATE` 잠금으로 세션 확인과 최신 버전 갱신을 직렬화한다. 첫 문서 생성의 중복 요청은 UUID 기반 advisory lock으로 보호한다. SQLite 개발 모드는 `BEGIN IMMEDIATE`로 쓰기를 직렬화한다. 표시 파일명은 저장 경로에 쓰지 않는다.

빈 DB 초기 스키마: `backend/migrations/001_initial.sql`. 추가형 협업 스키마는 `002_collaboration.sql`이다. Compose는 `python -m app.migrate`로 기존 테이블·파일을 보존하며 확장한다. 기존 공용/local 문서에 인증을 도입할 때 `HWP_LEGACY_OWNER`를 명시해야 한다. 자세한 내용은 협업 설정 안내를 참조한다.

파일은 배타적인 새 키로 작성하고 fsync 후 DB를 commit한다. 실패 파일·프로세스 중단으로 남은 참조 없는 파일은 정상 버전으로 조회되지 않으며, 자동 삭제하지 않는다. DB와 볼륨을 함께 백업해야 한다. 영구 삭제·가비지 컬렉션은 후속 작업이다.

ZIP 크기 30MB, 해제 합계 120MB, 항목 수·중복 경로·압축률·필수 XML·mimetype·CRC·DTD/entity 등을 검사한다. 파일 경로로 ZIP을 풀지 않는다. 엔진 손실 보고가 있으면 게시 차단과 복구 후보 다운로드, 서버 리소스 누락 감지 시 후보 파일 격리를 수행한다. 이것은 완전한 의미/서식 비교나 한글 호환성 보증이 아니다.

## 테스트

브라우저 시험은 실행 중인 개발 문서함에 합성 시험 문서를 추가한다. 기존 문서를 삭제하지 않는다.

```powershell
# 저장소 루트
.\.venv\Scripts\python -m pytest -q
# API:8000, Vite:5173을 먼저 실행. Chrome 설치가 필요하다.
cd frontend
npm run build
npm run test:e2e
npm audit
```

실행 결과와 범위는 [이번 검증 기록](docs/verification-2026-09-27.md), [최초 구현 기록](docs/verification.md), 수동 한글 시험은 [호환성 검증표](docs/compatibility.md), 후속 범위는 [남은 작업](docs/remaining-work.md)을 참조한다. 새 일정·공수는 산정하지 않았다. 기존 계획과 로드맵은 요구사항·참고 자료로 보존했다.
