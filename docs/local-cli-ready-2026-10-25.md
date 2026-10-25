# 로컬 실행·Codex CLI 작업 인수인계 — 2026-10-25

브랜치: **`codex/local-cli-ready-2026-10-25`**. 앱 기준 커밋은 `a013b8633e2d488d0c1a4799f9698ec310bcb92d`이며, 지금 검수하던 로컬 QA 기능과 main 동기화 내용을 포함한다. 후속 통합으로 QA 커밋 `41a64bd`의 편집 디자인·AI 추천·표준 편집 개선도 포함한다. 새 bridge에 맞춘 엔진 kit로 실행한다.

## 새 PC에서 실행

Windows에서 Git, Node 22.12 이상, Python 3.11 이상과 이 비공개 저장소 접근 권한을 준비한다. 검증 환경은 Node 24.15.0 / Python 3.14.4다.

```powershell
git clone --branch codex/local-cli-ready-2026-10-25 https://github.com/m4r4m01m/hwp-assistant.git
cd hwp-assistant
& ".\Start HWP Assistant.cmd"
```

처음에는 인터넷으로 잠금 의존성과 [호환 엔진 ZIP](https://github.com/m4r4m01m/hwp-assistant/releases/tag/developer-engine-2026-10-25-editor-designs)을 받는다. 기본 웹/API 포트는 15190/18190이며 사용 중이면 다음 빈 포트를 찾는다. 창을 유지하고 종료할 때 `Ctrl+C`를 사용한다. 다음 실행은 같은 `.cache/developer/data` 문서함을 연다. 기존 PC의 문서·DB·로그인 정보는 Git이나 kit에 포함하지 않았으므로 새 clone은 빈 문서함으로 시작한다. [자세한 실행 안내](developer-quickstart.md).

## Codex CLI와 연결

앱은 이미 백엔드에서 설치된 Codex CLI의 `app-server`와 `exec`를 호출한다. Codex 데스크톱 창을 통해 요청을 전달하지 않는다. 본인 CLI를 설치·로그인한 뒤 앱의 **AI 연결 → 연결 상태 확인 → 이 PC의 Codex 사용**으로 연결 안내를 완료한다. [공식 CLI 설치 안내](https://developers.openai.com/codex/cli/)와 `codex login`을 사용한다. 실행 파일이 PATH에 없으면 `HWP_CODEX_EXECUTABLE`에 해당 실행 파일의 절대 경로를 지정한다. 로그인 정보는 복사하거나 저장소에 올리지 않는다.

이 저장소 자체를 Codex CLI에서 개발하려면 저장소 폴더에서 `codex`를 시작하고 [AGENTS.md](../AGENTS.md)와 [PROJECT_CONTEXT](PROJECT_CONTEXT.md)를 읽게 한다. Git의 코드·문서가 인수인계 기준이며 데스크톱 대화 이력이 자동 이전되는 것은 아니다.

현재 PC의 CLI는 데스크톱 배포본에 포함된 `codex-cli 0.163.0-alpha.10.2` 실행 파일이다. CLI 프로세스의 실행·로그인 확인과 별도로, 다른 배포본/버전의 프로토콜 호환성은 해당 환경에서 확인한다. 처음 시작할 때 모델을 호출하지 않으며 API 키나 유료 제공자로 자동 전환하지 않는다.

## 보존한 작업과 다음 시작점

- **선택 영역 디자인 적용·대화 중 디자인 추천**과 표준 편집 후속 검증이 완료되어 이 실행 브랜치에 병합했다. 세부 검증과 한계는 PROJECT_CONTEXT의 최신 기록을 따른다.
- 기존 문서함·원본·과거 버전·표준·기준서·frozen 초안과 기존 실행 중인 로컬 앱을 변경하지 않는다. 사용자 데이터 이전이 필요하면 Git push와 별도로 DB와 파일을 함께 백업·복원한다.
- 새 설계에서도 원문·선택·버전 fence, 사용자 승인 후 적용, 새 버전 저장, 소유권/AI ACL을 유지한다. 기존 문서를 덮어쓰거나 인증 파일을 포함시키지 않는다.
- kit는 **Windows native diagnostic** 빌드다. Docker/최적화 운영 이미지·원격 배포는 이 분기의 실행 검증과 별개다. 운영 워크플로는 변경하지 않았다.

## 검증

실행 증거와 이번에 확인한 범위는 [검증 기록](evidence/local-cli-ready-2026-10-25.json)에 남긴다. 이전 기준 커밋의 백엔드·UI·실제 rhwp 검증은 [로컬 main 동기화 기록](evidence/local-main-sync-2026-10-25.json)을 따른다. 실제 모델 호출과 Hancom 재검수는 이번 패키징 작업의 검증으로 표시하지 않는다.
