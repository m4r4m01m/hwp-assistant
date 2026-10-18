# Windows 원클릭 개발 환경 검증 · 2026-10-18

`Start HWP Assistant.cmd` → `scripts/dev.ps1` → `scripts/start-local.ps1` 경로를 추가했다. 고정 의존성·개발 엔진을 준비하고 새 데이터 폴더에 API·웹을 실행한다. 실행 시 AI 생성은 하지 않는다. [사용 안내](developer-quickstart.md) · [기계 판독 기록](evidence/developer-setup-2026-10-18.json)

## 실제 수행

- Windows PowerShell 5.1에서 Git·Node 24.15.0·Python 3.14.4·Codex 로그인 상태 검사 통과. 인증 출력·토큰은 저장하지 않았다.
- **기존 `.venv`와 `node_modules`가 없는 작업 사본**에서 새 가상환경 생성, 잠금 Python/MCP 설치와 `npm ci`, 오프라인 kit 가져오기, TypeScript·Vite 빌드, loopback API/웹 기동, HTTP로 제공한 앱·엔진 10개 파일 해시 확인, 종료 후 listener 0개를 확인했다. 사용자 수준 pip/npm 다운로드 캐시는 재사용했다. 원격 clone 시험으로 표시하지 않는다.
- 실제 `.cmd` 진입점에서 15890 포트를 자체 테스트 listener로 선점하자 15891을 선택했다. 기존 listener는 살아 있었고 이번 API·웹만 종료됐다. 같은 개발 데이터 폴더를 재사용했다.
- 같은 checkout의 자체 Node 서비스가 실행 중이고 설치 영수증이 없는 상황에서는 pip/npm 설치 전에 거절했다. 기존 프로세스와 의존성 영수증은 보존됐다.
- 알 수 없는 옵션을 전달하면 설치·시작 전에 실패하고 `.cmd`에서 exit code 1을 유지한다. 중첩 PowerShell 7 → Windows PowerShell 5.1 실행의 모듈 경로 문제도 `.cmd`의 프로세스 환경에만 표준 모듈 경로를 지정해 해결했다.
- kit 안전 검사 4개 통과: SHA 불일치, ZIP 경로 탈출, 기존 출력 보존, cache 밖 출력 거부. private release redirect 검사 4개 통과: 자산 호스트에 API 자격 전달 차단, 같은 API 호스트 유지, 다른 호스트·HTTP·비표준 포트 차단. 가짜 테스트 토큰만 사용했으며 이 검사에는 네트워크 접근이 없다.

실수로 잘못된 옵션을 무시하던 초안 실행기로 원래 checkout의 설치를 시도한 일이 있었다. pip 패키지는 기존 버전 그대로였고 npm은 사용 중인 native 모듈 잠금으로 실패했다. 서버·문서는 종료하거나 수정하지 않았다. 이 발견 뒤 옵션 검사와 설치 전 사용 중인 checkout 검사를 추가했고 분리 사본에서 차단 동작을 확인했다. 원래 checkout의 의존성 복구와 최소 `source-map-js` 보안 갱신은 통합 담당자가 별도 수행한다. 위 성공 기록은 분리된 작업 사본의 실행 결과다.

## 개발 엔진 kit

- 파일: `hwp-assistant-developer-engine-0.8.6-20261018.zip`, 37,244,030 bytes.
- SHA-256: `bcfd8793bbc5fe3b3c766784348de4e07ccb794719479cffca476fe4715d36fa`.
- rhwp 정적 파일 82개, 해제 크기 52,381,891 bytes. 라이선스·폰트 고지 포함. 문서·DB·인증·앱 소스·실행 환경은 제외했다.
- 고정 rhwp `e8800c8def63449808a4092798442652ed460552`, empty-fill 및 body-selection 패치·현재 브리지·WASM 해시를 검사한다. **diagnostic=true를 그대로 유지한다.** Docker 최적화·운영 배포 검증으로 승격하지 않는다.

## 구분할 범위

이 기록 시점의 원격 release 게시와 실제 비공개 release 다운로드·fresh clone 재검증은 통합 담당자의 후속 검증 단계다. 로컬 kit의 실제 설치·기동과 구분한다. Docker/native 엔진을 새 도구 환경에서 처음부터 다시 컴파일하지 않았다. 개발 도구가 아예 없는 새 PC의 시스템 설치, 폐쇄망 전체 설치, 실제 모델 생성·사람 승인·한컴 검수도 이 setup 시험으로 대체하지 않는다.

Codex 의존성은 현재 backend의 실행 파일 탐색·`app-server`·`exec` 경로와 [공식 Codex CLI 문서](https://developers.openai.com/codex/cli/), [App Server 문서](https://developers.openai.com/codex/app-server/)를 대조했다. 데스크톱 앱의 화면 자체를 요구하는 구조는 아니다. 각 개발자의 본인 CLI 로그인과 계정 사용 권한은 필요하다.

## 후속 통합 확인: 실제 원격 clone과 release 다운로드

위 단계 이후 구현을 `2feee80da98b2d045f01683c878a88e32b8fafeb`로 커밋·푸시하고 [비공개 개발 엔진 release](https://github.com/m4r4m01m/hwp-assistant/releases/tag/developer-engine-2026-10-18)를 게시했다. 그 커밋을 GitHub에서 **새로 clone**한 폴더에서 다음 명령을 실행해 exit 0 / `smoke-passed`를 확인했다.

```powershell
& ".\Start HWP Assistant.cmd" -NoBrowser -SmokeSeconds 3
```

- 프로젝트 `.venv`·`node_modules`·엔진 ZIP·문서·인증 파일을 복사하지 않았다. Git·Node·Python 및 본인 GitHub 인증은 설치돼 있었고 사용자 수준 다운로드 캐시는 재사용했다.
- 잠금 Python/MCP·npm 의존성을 새로 설치했다. `source-map-js`는 1.2.2이며 `npm audit` 결과 취약점 0건이었다. 원래 checkout에서 잠긴 native 모듈을 삭제·종료하지 않고 별도 설치본과 해시를 비교해 의존성을 복구했으며, TypeScript·빌드와 관련 시험도 통과했다.
- 비공개 release에서 37,244,030-byte ZIP를 실제 다운로드했다. 위 고정 SHA-256, 내부 82개 파일과 라이선스·패치·브리지·WASM 검증이 통과했다. Windows clone 후 기준 HWPX·profile·브리지·두 패치 해시도 원본과 같았다.
- TypeScript·Vite 빌드, API 18190 / 웹 15190 실행, HTTP 10개 파일의 실제 바이트 해시 검증을 통과했다. 시험 종료 후 이번 listener는 0개였고 기존 8개 서비스의 PID·시작 시각은 모두 같았다. 새 clone의 Git 작업 트리는 깨끗했다.
- 실제 모델 호출은 0회다. `diagnostic=true`, 새 PC의 시스템 도구 설치·폐쇄망·Docker 운영 배포·Hancom 검수를 대신하지 않는 범위는 그대로다.

[후속 기계 판독 증거](evidence/developer-fresh-clone-2026-10-18.json)에 release ID·검증한 커밋·해시·실행 영수증을 보관했다. 앞의 로컬 kit 시험과 원격 검증 전 상태 기록은 이력으로 유지한다.
