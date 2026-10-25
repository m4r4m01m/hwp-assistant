# Windows 개발자 빠른 시작

`codex/local-cli-ready-2026-10-25` 브랜치를 clone한 다음 루트의 **`Start HWP Assistant.cmd`를 더블클릭**한다. 처음에는 잠금 파일에 맞는 Python/MCP·npm 의존성과 검증된 개발용 rhwp 엔진을 준비하고 API·웹을 실행한다. 다음 실행은 같은 개발자 문서함을 다시 연다. 모델은 시작할 때 호출하지 않는다.

## 이 브랜치의 준비 상태 (2026-10-25)

실행 기준은 로컬 QA에서 검증한 `a013b8633e2d488d0c1a4799f9698ec310bcb92d`다. 현재 bridge와 일치하는 [개발용 엔진 kit](https://github.com/m4r4m01m/hwp-assistant/releases/tag/developer-engine-2026-10-25-local-cli)를 게시하고 lock의 자산 ID·SHA-256을 갱신했다. 기존 2026-10-18 kit의 bridge 불일치 때문에 최초 설치가 중단되던 문제를 해결하는 분기다. 2026-10-22 당시 미게시 상태는 과거 기록이다.

kit는 **Windows native diagnostic 빌드**이며 최적화 운영 엔진을 대체하지 않는다. Git·Node·Python과 저장소 접근 권한이 있으면 기본 경로에서 Rust·Docker 소스 빌드를 요구하지 않는다. 작성 중이던 선택 영역 디자인 적용 기능은 이 브랜치에 섞지 않았다. [CLI 인수인계와 검증 범위](local-cli-ready-2026-10-25.md)를 함께 읽는다.

## 처음 한 번 필요한 것

- [Git for Windows](https://git-scm.com/download/win), [Node.js](https://nodejs.org/en/download) 22.12 이상, [Python](https://www.python.org/downloads/windows/) 3.11 이상을 설치하고 PATH를 적용한다. 검증한 버전은 Node 24.15.0 / Python 3.14.4다. 설치 후 터미널을 다시 연다. 실행기는 시스템 도구·관리자 권한·Docker를 조용히 설치하지 않는다.
- 이 비공개 저장소를 clone할 수 있는 본인 GitHub 계정이 필요하다. 엔진 release도 같은 저장소 권한으로 받는다. Git Credential Manager로 clone했거나 `gh auth login`한 개발자는 기존 자격을 메모리에서만 사용한다. 토큰을 설정 파일이나 명령 인자에 쓰지 않는다.
- AI 보고서·문단 수정을 쓰려면 **본인의 Codex CLI와 ChatGPT 로그인**이 필요하다. Codex 데스크톱 앱 UI는 필수가 아니다. [공식 Codex CLI 설치 안내](https://developers.openai.com/codex/cli/)를 따른 다음 `codex login`한다. 이 앱은 설치된 `codex`의 `app-server` 및 문단 수정용 `exec`를 실행한다. 전역 PATH에서 찾지 못하는 설치는 실행 환경의 `HWP_CODEX_EXECUTABLE`로 지정할 수 있다. 다른 사람의 인증 파일을 복사하지 않는다. API 키·유료 모델로 자동 전환하지 않는다.

AI 설정 전에도 파일 열기·수동 편집·서버 저장을 사용할 수 있다. 최초 설치에는 PyPI/npm/GitHub 연결이 필요하다. 폐쇄망 신규 설치에는 별도 의존성 반입이 필요하며 원클릭 오프라인 설치가 검증된 것은 아니다.

```powershell
git clone --branch codex/local-cli-ready-2026-10-25 https://github.com/m4r4m01m/hwp-assistant.git
cd hwp-assistant
# 또는 탐색기에서 Start HWP Assistant.cmd 더블클릭
& ".\Start HWP Assistant.cmd"
```

## 실행 중인 화면과 데이터

로컬 실행기로 처음 열면 **AI 연결 설정**이 나온다. **연결 상태 확인**을 누르면 설치된 Codex의 `app-server`에서 ChatGPT 로그인 여부만 읽고 종료한다. 모델에 요청하거나 로그인 정보를 복사하지 않는다. 확인 후 **이 PC의 Codex 사용**을 누르면 안내를 완료한다. 아직 설치·로그인하지 않았다면 안내 링크와 `codex login` 명령을 사용한 뒤 다시 확인한다. **나중에 설정**을 선택해도 문서 작업을 시작할 수 있으며, 사이드바의 **AI 연결**에서 언제든 다시 열 수 있다.

선택은 현재 문서함의 DB에 저장된다. 연결 안내를 완료하거나 미루는 선택이며 기존 제공자 설정, 진행 중인 AI 작업, 편집 내용을 바꾸지 않는다. 로그인 확인은 실제 모델 사용 권한이나 보고서 생성 성공까지 검증하지 않는다. 화면에서 보고서·문단 수정·문서 AI와 일반 대화·검토의 설정 상태를 각각 확인할 수 있다.

`start-local.ps1`과 `start-user-review.ps1`은 loopback 실행에만 `HWP_LOCAL_AI_SETUP=1`을 전달한다. 기본값은 꺼짐이며, 인증을 사용하는 서버는 이 값과 관계없이 서버 관리 화면을 표시한다. Docker·공유 서버의 화면에서 접속자 PC의 Codex를 설치하거나 연결하지 않는다. 서버 AI는 서버 운영자가 구성한다.

웹 기본 포트는 `15190`, API는 `18190`이다. 사용 중이면 각각 다음 빈 포트를 찾으며 URL을 출력하고 브라우저를 연다. loopback에만 바인딩한다. 원래 `data/`와 기존 실행 프로세스는 건드리지 않는다.

새 개발자 데이터는 `.cache/developer/data/metadata.db`와 `files/`에 보존된다. 로그·실제 제공 파일 해시·실행 기록은 `.cache/developer/runs/<고유 실행 ID>/`에 남는다. 문서를 보관하려면 `.cache/developer/data` 전체를 백업한다. 창을 유지하고 `Ctrl+C`로 종료하면 이번 실행기가 시작한 API·웹만 정리한다. 탐색기에서 창을 강제로 닫으면 프로세스 정리가 완료되지 않을 수 있으므로 `Ctrl+C`를 사용한다.

브라우저·포트·데이터 위치를 직접 지정할 수도 있다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 -CheckOnly
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 -NoBrowser -Port 15192 -ApiPort 18192
# 문서는 그대로 두고 의존성과 엔진만 준비
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 -NoStart
```

`-CheckOnly`는 기본 도구·로그인 상태만 검사한다. 설치·서버 실행·실제 AI 생성이 성공했다는 뜻은 아니다. `-NoInstall`은 이미 준비된 환경의 진단용이며 정상 최초 실행에서는 사용하지 않는다. PowerShell 실행 정책은 해당 프로세스에만 적용되며 사용자·시스템 정책을 바꾸지 않는다.

의존성 설치가 필요한데 같은 checkout의 Python/Node 서버가 실행 중이면 설치 전에 중단한다. Windows에서 실행 중인 패키지 파일을 교체하지 않도록 하기 위한 검사다. 새 clone에서 실행하거나 그 checkout의 서버를 종료한 뒤 다시 실행한다. 실행기가 기존 서버를 대신 종료하지 않는다. 다른 checkout의 서버는 유지한다.

## 엔진을 어떻게 준비하는가

기본 실행은 [개발 엔진 lock](../scripts/developer-engine.lock.json)의 bridge 해시가 현재 checkout과 일치하는지 먼저 확인한다. 일치하면 저장소·release tag·asset 이름을 GitHub API로 조회해 **고정 SHA-256**을 검사한다. 이어 ZIP의 정적 파일 해시, 고정 upstream 커밋·두 패치·WASM·현재 브리지·라이선스 고지를 확인한다. 2026-10-25 kit는 82개 파일이며, 원본 문서·DB·로그인·이 PC의 경로를 엔진 kit에 넣지 않는다.

잠금 파일의 2026-10-25 kit는 로컬 QA에서 사용한 **Windows native diagnostic 엔진**이다. 배포용 최적화 코어로 취급하지 않으며 진단 빌드 제한을 유지한다. 앱 UI는 clone한 현재 소스로 매번 빌드한다. 엔진을 미수정 npm WASM으로 대체하지 않는다. ZIP·해시만으로 서명된 공급망 인증이 제공되는 것은 아니다.

엔진 변경 시 기존 cache가 맞지 않으면 중단한다. `.cache/developer/public`을 별도 위치로 옮겨 보관한 뒤 새 kit를 받거나 아래 source build를 사용한다. 문서 데이터 폴더를 삭제하지 않는다.

GitHub release 다운로드가 불가능하면 개발 담당자에게 **현재 bridge와 일치하는 kit와 SHA-256**을 받아 루트에 `developer-engine.zip`, `developer-engine.zip.sha256`으로 놓는다. 또는 경로와 확인한 해시를 명시한다. 2026-10-18 kit를 새 bridge에 재사용할 수는 없다.

위 release에서 파일을 직접 전달받은 경우에는 다음처럼 명시할 수 있다. SHA-256은 [lock](../scripts/developer-engine.lock.json)과 일치해야 한다. 기존 2026-10-18 kit는 이 bridge에 사용할 수 없다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 `
  -EnginePublic .cache/developer/engine-20261025/public `
  -EngineArchive C:\Downloads\hwp-assistant-developer-engine-0.8.6-20261025-local-cli.zip `
  -EngineArchiveSha256 0fd6a562eb0387c68d7b00eb2d94d410233bc6f8340b59463904a5a3f4302168
```

기존 검증 엔진을 직접 쓰는 경로도 있다. 엔진을 복사한 실행 폴더를 만들고, 실제 제공 바이트를 다시 확인한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 `
  -EnginePublic .cache/my-verified-engine/public -NoInstall -NoBrowser
```

소스에서 다시 빌드하려면 Docker Linux의 `-BuildEngine Docker` 또는 [고정 native 도구 준비](engine-empty-fill-fix.md) 후 `-BuildEngine Native`를 사용한다. 이 명시적 경로는 release 다운로드와 루트 ZIP 자동 선택을 생략한다. native 빌드에는 Visual Studio C++ Build Tools와 프로젝트 안의 고정 Rust/wasm-bindgen이 추가로 필요하다. Docker 배포 실행은 native 검증으로 대체하지 않는다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 -EnginePublic .cache/developer/source-docker/public -BuildEngine Docker
# 고정 native 도구를 이미 준비한 경우
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 -EnginePublic .cache/developer/source-native/public -BuildEngine Native
```

개발 담당자가 새 kit를 내보낼 때는 검증 엔진 경로와 새 ZIP 이름을 명시한다. 기존 ZIP은 덮어쓰지 않는다. 생성한 ZIP을 별도 폴더로 다시 unpack/check하고, 현재 앱의 실제 엔진 회귀를 통과한 뒤 release에 ZIP와 `.sha256`을 게시한다. 게시된 자산의 해시·identity를 확인한 다음 lock의 tag/asset/SHA-256/WASM/bridge 정보를 함께 갱신한다. 로컬 ZIP 생성만으로 게시 완료나 기본 원클릭 복구를 기록하지 않는다.

```powershell
node scripts/developer-engine-kit.mjs pack --public .cache/my-verified-engine/public --out .cache/my-release/developer-engine.zip
```

기존 [수동 설치 안내](../README.md#windows에서-api웹-개발-실행), [엔진 빌드 상세](engine-empty-fill-fix.md), [Codex 연결 계약](collaboration-and-mcp.md)도 유지한다. 실제 설치·기동·회귀 시험과 미실행 범위는 [현재 CLI 인수인계](local-cli-ready-2026-10-25.md)와 [과거 설치 검증](developer-setup-verification-2026-10-18.md)에 구분한다.
