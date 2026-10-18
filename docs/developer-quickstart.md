# Windows 개발자 빠른 시작

저장소를 clone한 다음 루트의 **`Start HWP Assistant.cmd`를 더블클릭**한다. 처음에는 잠금 파일에 맞는 Python/MCP·npm 의존성과 검증된 개발용 rhwp 엔진을 준비하고 API·웹을 실행한다. 다음 실행은 같은 개발자 문서함을 다시 연다. 모델은 시작할 때 호출하지 않는다.

## 처음 한 번 필요한 것

- [Git for Windows](https://git-scm.com/download/win), [Node.js](https://nodejs.org/en/download) 22.12 이상, [Python](https://www.python.org/downloads/windows/) 3.11 이상을 설치하고 PATH를 적용한다. 검증한 버전은 Node 24.15.0 / Python 3.14.4다. 설치 후 터미널을 다시 연다. 실행기는 시스템 도구·관리자 권한·Docker를 조용히 설치하지 않는다.
- 이 비공개 저장소를 clone할 수 있는 본인 GitHub 계정이 필요하다. 엔진 release도 같은 저장소 권한으로 받는다. Git Credential Manager로 clone했거나 `gh auth login`한 개발자는 기존 자격을 메모리에서만 사용한다. 토큰을 설정 파일이나 명령 인자에 쓰지 않는다.
- AI 보고서·문단 수정을 쓰려면 **본인의 Codex CLI와 ChatGPT 로그인**이 필요하다. Codex 데스크톱 앱 UI는 필수가 아니다. [공식 Codex CLI 설치 안내](https://developers.openai.com/codex/cli/)를 따른 다음 `codex login`한다. 이 앱은 설치된 `codex`의 `app-server` 및 문단 수정용 `exec`를 실행한다. 전역 PATH에서 찾지 못하는 설치는 실행 환경의 `HWP_CODEX_EXECUTABLE`로 지정할 수 있다. 다른 사람의 인증 파일을 복사하지 않는다. API 키·유료 모델로 자동 전환하지 않는다.

AI 설정 전에도 파일 열기·수동 편집·서버 저장을 사용할 수 있다. 최초 설치에는 PyPI/npm/GitHub 연결이 필요하다. 폐쇄망 신규 설치에는 별도 의존성 반입이 필요하며 원클릭 오프라인 설치가 검증된 것은 아니다.

```powershell
git clone https://github.com/m4r4m01m/hwp-assistant.git
cd hwp-assistant
# 또는 탐색기에서 Start HWP Assistant.cmd 더블클릭
& ".\Start HWP Assistant.cmd"
```

## 실행 중인 화면과 데이터

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

기본 실행은 [개발 엔진 lock](../scripts/developer-engine.lock.json)의 저장소·release tag·asset 이름을 GitHub API로 조회해 **고정 SHA-256**을 검사한다. 이어 ZIP의 82개 정적 파일 해시, 고정 upstream 커밋·두 패치·WASM·현재 브리지·라이선스 고지를 확인한다. 원본 문서·DB·로그인·이 PC의 경로를 엔진 kit에 넣지 않는다.

이 kit는 현재 검증한 **Windows native diagnostic 엔진**이다. 배포용 최적화 코어로 취급하지 않으며 진단 빌드 제한을 유지한다. 앱 UI는 clone한 현재 소스로 매번 빌드한다. 엔진을 미수정 npm WASM으로 대체하지 않는다. ZIP·해시만으로 서명된 공급망 인증이 제공되는 것은 아니다.

엔진 변경 시 기존 cache가 맞지 않으면 중단한다. `.cache/developer/public`을 별도 위치로 옮겨 보관한 뒤 새 kit를 받거나 아래 source build를 사용한다. 문서 데이터 폴더를 삭제하지 않는다.

GitHub release 다운로드가 불가능하면 개발 담당자에게 **동일 버전 kit와 SHA-256**을 받아 루트에 `developer-engine.zip`, `developer-engine.zip.sha256`으로 놓는다. 또는 경로와 확인한 해시를 명시한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 `
  -EngineArchive C:\Downloads\hwp-assistant-developer-engine-0.8.6-20261018.zip `
  -EngineArchiveSha256 bcfd8793bbc5fe3b3c766784348de4e07ccb794719479cffca476fe4715d36fa
```

기존 검증 엔진을 직접 쓰는 경로도 있다. 엔진을 복사한 실행 폴더를 만들고, 실제 제공 바이트를 다시 확인한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 `
  -EnginePublic .cache/my-verified-engine/public -NoInstall -NoBrowser
```

소스에서 다시 빌드하려면 Docker Linux의 `-BuildEngine Docker` 또는 [고정 native 도구 준비](engine-empty-fill-fix.md) 후 `-BuildEngine Native`를 사용한다. 이 명시적 경로는 release 다운로드를 생략한다. native 빌드에는 Visual Studio C++ Build Tools와 프로젝트 안의 고정 Rust/wasm-bindgen이 추가로 필요하다. Docker 배포 실행은 이 Windows 검증으로 대체하지 않는다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 -BuildEngine Docker
# 고정 native 도구를 이미 준비한 경우
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 -BuildEngine Native
```

개발 담당자가 새 kit를 내보낼 때는 검증 엔진 경로와 새 ZIP 이름을 명시한다. 기존 ZIP은 덮어쓰지 않는다. 생성 후 release에 ZIP와 `.sha256`을 게시하고 lock을 함께 갱신한다.

```powershell
node scripts/developer-engine-kit.mjs pack --public .cache/my-verified-engine/public --out .cache/my-release/developer-engine.zip
```

기존 [수동 설치 안내](../README.md#windows에서-api웹-개발-실행), [엔진 빌드 상세](engine-empty-fill-fix.md), [Codex 연결 계약](collaboration-and-mcp.md)도 유지한다. 실제 설치·기동·회귀 시험과 미실행 범위는 [검증 기록](developer-setup-verification-2026-10-18.md)에 구분한다.
