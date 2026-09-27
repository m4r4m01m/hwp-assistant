# 실행 검증 기록 · 2026-09-26

이 파일은 최초 구현의 과거 실행 기록이다. 이후 변경은 [2026-09-27 검증 기록](verification-2026-09-27.md)에 별도로 기록했다.

대상: 이 작업 폴더의 첫 실행 구현. Windows 네이티브 Python 3.14.4, Node 24.15.0, Chrome, SQLite 파일 DB. rhwp 0.8.6 / 커밋 `e8800c8def63449808a4092798442652ed460552` 자체 빌드. 테스트 입력은 합성 문서다.

| 명령 | 결과 | 검증 범위 |
|---|---|---|
| `.venv\Scripts\python -m pytest -q` | 18 passed | 원본 해시, 편집/재업로드/복원 불변성, 중복 이름 분리, 동시 저장, 멱등 재시도, 이전 영수증 충돌, 세션 만료/갱신 거부, 파일·DB 확정 실패, ZIP 거부·리소스 손실 차단, AI 미설정 격리, 모델 JSON/timeout/연결 오류 |
| 위 테스트 중 `test_real_server_process_restart` | 통과 | uvicorn 실제 프로세스 종료·다시 실행 후 목록/버전 바이트 유지 |
| `cd frontend; npm run engine` | 성공 | 고정 커밋 Studio + 같은 npm WASM 빌드, 폰트 자체 제공, CDN 폰트 off |
| `cd frontend; npm run build` | 성공 | 앱 TypeScript 검사 + Vite production build |
| `cd frontend; npm run test:e2e` | 4 passed (17.1s) | 실제 엔진 시험; AI mock/네트워크 주입은 아래 구분 |
| 고정 Studio `npm exec tsc -- --noEmit` | 성공 | 자체 브리지를 포함한 업스트림 TypeScript 검사 |
| `cd frontend; npm audit` | 0 vulnerabilities | 앱 npm 트리; 라이선스/보안 감사 완료 의미 아님 |
| 업스트림 Studio `npm audit --omit=dev` | 0 vulnerabilities | 배포 runtime 의존성 |
| 업스트림 Studio `npm audit` | 5건 | 빌드 도구 의존성: low 1, moderate 1, high 3; 남은 작업에 기록 |
| `127.0.0.1:1234/v1/models` 실제 HTTP 요청 | 연결 거부 | LLM 성공/모델 설치/품질 검증 아님 |

## 실제 rhwp 브라우저 시험

`frontend/tests/engine.spec.ts`는 로컬 API와 자체 호스팅 iframe을 사용한다. 첫·둘째·넷째 시험의 엔진·저장 API는 mock이 아니다.

1. 엔진 새 문서 → 키보드 한글 입력 → 서버 저장 → 실제 HWPX 다운로드 → 그 파일 신규 업로드 → 원본 SHA 비교 → 다시 키보드 수정 → 새 버전 저장 → 과거 바이트 동일 → 재열기 내용 확인. AI 미설정 오류 중 저장 가능. 브라우저 시나리오의 외부 HTTP 요청 0건 확인.
2. 문자 굵게·문단 가운데 정렬 → 엔진 표 도구에서 2×2 표 생성 → 셀 텍스트 입력/수정 → 저장 XML에 표·내용·서식 확인 → 업로드/재열기 → 다시 엔진 직렬화 후 셀 내용 확인. 한글 시각적 호환 검증 아님.
3. **실제 엔진 + 명시적 mock AI 응답**: 승인 전 무변경, 승인 후 실제 exact command로 변경, 실행 취소/다시 실행, 후속 편집 뒤 오래된 제안 적용 거부. 실제 모델 품질/서버 연결 성공을 뜻하지 않음.
4. **실제 엔진·API + 네트워크 장애 주입**: 저장 응답을 지연하는 동안 추가 입력해 미저장 상태 유지; 서버 commit 후 응답 유실을 주입하고 재시도 시 버전 수 증가 없음 확인.

백엔드 저장 정책 시험의 작은 ZIP fixture는 형식/저장 검사를 위한 합성 데이터로, 한글 호환 샘플이 아니다. 실제 엔진 파일 시험은 브라우저 시험으로 분리했다. Python 테스트에 Starlette/anyio deprecation warning 1건이 있지만 실패는 없다.

실행 화면: [문단 편집](evidence/real-editor.png), [표 편집](evidence/table-editor.png). 재사용 가능한 실제 엔진 합성 파일은 `samples/paragraph-and-table.hwpx`에 보존했다. SHA-256: `98cf53fc1b906fe322ad575051d8c3abcbcce86d07cc61c7f5cb6acec689b813`.

## 실행하지 못한 항목

- PostgreSQL 실행·경쟁 테스트와 Docker Compose: 해당 실행 도구가 설치돼 있지 않음.
- 실제 LLM 문장 검토/모델 품질: 로컬 예시 서버 연결 거부. 사내망에 접근하지 않음.
- 한컴오피스 한글의 시각/편집/역방향/반복 왕복: 한글 실행 환경 미확인.
- 인터넷 차단 환경의 전체 실행: 격리망 시험을 수행하지 않음. 일반 개발 환경에서 외부 요청이 없었던 관찰과 구분함.
- 한국어 OS IME 조합키 전체·복잡한 중첩 표·모든 개체: 일반 한글 문자열 브라우저 입력과 단순 표 범위만 자동 검증.

## 구현 중 발견해 수정한 문제

- Vite의 `/rhwp/` 디렉터리 요청이 앱 SPA로 fallback: 자체 호스팅 `index.html`을 명시.
- 개발 API proxy가 Host를 변경해 동일 출처 검사를 실패: `changeOrigin:false` 명시.
- Windows Git symlink가 폰트 경로 텍스트 파일로 체크아웃: 빌드 시 정식 assets/fonts를 디렉터리로 materialize.
- 실패 테스트 fixture ZIP의 현재 시각 때문에 바이트 비교가 간헐 실패: fixture ZIP 시각을 고정. 엔진/저장 원본을 수정해 테스트를 통과시키지 않음.
- 원본 첫 문단의 구역 컨트롤은 exact AI target 제외: 시험용 일반 본문 문단을 사용하고 제약을 UI/README에 표시.
