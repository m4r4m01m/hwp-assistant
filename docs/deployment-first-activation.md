# 홈서버 배포 파이프라인 첫 연결

이 문서는 **이미 접속 가능한 홈서버에 GitHub Actions 배포를 연결**하는 절차다. 워크플로 파일이 저장소에 있는 상태, GitHub에서 이미지 검증을 완료한 상태, 홈서버가 실제로 교체된 상태를 구분한다. 기존 문서·DB·로그인 토큰은 그대로 인수하며 새 설치용 초기화를 반복하지 않는다.

## 1. 준비된 코드와 필요한 연결

| 구간 | 준비된 동작 | 첫 연결에 필요한 것 |
|---|---|---|
| PR / main push | Python·TypeScript·UI 및 배포 도구 검사 | GitHub Actions 실행 권한 |
| main 수동 실행, `deploy=false` | GitHub-hosted Ubuntu에서 Docker/WASM 빌드 → PostgreSQL·HTTPS·브라우저 검사 → GHCR 게시 | Actions와 패키지 게시 권한. 홈서버 runner는 필요 없음 |
| main 수동 실행, `deploy=true` | 위 검증 후 홈서버에서 이미지 pull → 기존 DB/문서 백업 → 교체 → 점검 | 온라인 self-hosted runner, Docker 접근, 기존 운영 설정과 볼륨 |

`deploy=true`는 runner를 설치하는 옵션이 아니다. 일치하는 runner가 없으면 홈서버 작업은 대기한다. main push만으로 배포되지는 않는다. workflow의 상세 계약은 [배포 runbook](deployment-runbook.md)과 [.github/workflows/hwp-deploy.yml](../.github/workflows/hwp-deploy.yml)에 있다.

2026-10-22 확인 시 main에는 워크플로가 있었지만 조회 가능한 `workflow_dispatch` 실행 기록은 0개였다. PR #24의 검사 성공을 실제 Docker 이미지 게시나 홈서버 배포 성공으로 계산하지 않았다. 현재 실행 상태는 [Actions](https://github.com/m4r4m01m/hwp-assistant/actions/workflows/hwp-deploy.yml)에서 확인한다.

## 2. 기존 서버를 먼저 읽기 전용으로 확인

runner로 사용할 **비root 계정**의 서버 터미널에서, 이 진단기가 포함된 저장소 루트로 이동해 실행한다.

```bash
python3 -B scripts/deploy/check-host.py --env-file /opt/hwp-assistant/production.env
```

운영 설정 파일이 다른 위치에 있으면 실제 경로를 지정한다. 프로그램은 Python 표준 라이브러리만 사용하며, Docker 조회에는 제한 시간을 둔다. 설정 파일은 유효성 검사에만 사용하고 내용·토큰·DB 비밀번호·명령의 원문 출력은 표시하지 않는다. 파일 작성, 권한 변경, 컨테이너 시작·정지, 볼륨 생성·삭제, runner 등록은 하지 않는다.

진단의 `--env-file`은 배포 설정을 자동 변경하지 않는다. 현재 `deploy-home`은 `/opt/hwp-assistant/production.env`를 사용한다. 기존 파일이 다른 경로에 있다면 workflow의 `HWP_ENV_FILE`과 이후 배포·검수 명령을 **같은 기존 파일**로 맞추는 변경을 먼저 검토한다. 아래 기본 경로의 smoke 예시도 실제 경로에 맞춘다.

결과의 의미:

- `PASS`: 해당 로컬 조건을 확인했다.
- `BLOCKED`: 설정·권한·도구·기존 데이터 경로를 확인하거나 정리해야 한다. `deploy=true`를 실행하기 전에 해결한다.
- `MANUAL`: 이 프로그램으로 확인하지 않은 조건이다. GitHub runner 연결·환경 규칙, 포트와 외부 프록시, 기존 데이터의 정확한 인수는 별도로 확인한다.

이 진단이 통과해도 배포 승인이나 실제 가동 검증이 완료되는 것은 아니다. 파일 쓰기 가능성은 접근 권한으로만 검사하며 실제 쓰기를 시도하지 않는다.

Docker 조회는 현재 CLI의 context 및 환경 설정을 따른다. 원격 Docker context를 사용하는 경우 조회 대상이 실제 배포할 홈서버인지 별도로 확인한다. 디스크 여유·DB 버전·프록시 대상과 기존 문서 인수까지 자동 판정하지 않는다.

### 기존 설치를 새 설치로 오인하지 않기

현재 배포 코드는 Compose 프로젝트 `hwp-assistant-prod`, DB 볼륨 `hwp-assistant-prod-metadata`, 문서 볼륨 `hwp-assistant-prod-documents`를 사용한다. 실제 서비스가 다른 프로젝트·다른 볼륨·SQLite를 사용하면 자동으로 이전되지 않는다.

설정이나 대상 볼륨이 없다는 결과는 **현재 서버의 기존 데이터가 없다는 뜻이 아니다.** 이때 `init.py`를 실행하거나 새 비밀번호를 만들지 말고, 현재 서비스의 DB 종류·마운트와 23000 포트를 사용하는 프로세스를 먼저 확인한다. 전체 `docker inspect`, `docker compose config`, `production.env` 내용은 공유하지 않는다.

기존 환경이 일치하면 env와 볼륨을 유지한다. 계정을 새로 만들 경우에도 로그인 토큰·DB 비밀번호를 재생성하지 않는다.

## 3. GitHub에서 빌드와 게시부터 확인

개선 PR을 검토·병합한 **main** 기준으로 실행한다. 이 단계는 홈서버의 파일이나 실행 중인 컨테이너에 접근하지 않는다.

1. [HWP deployment](https://github.com/m4r4m01m/hwp-assistant/actions/workflows/hwp-deploy.yml)를 연다.
2. `Run workflow`에서 브랜치 `main`을 선택한다.
3. 첫 실행은 `deploy`를 **체크하지 않는다**.
4. `build-verify-publish`의 Docker 빌드, 실제 PostgreSQL/HTTPS 검사, 브라우저 검사와 GHCR 게시 결과를 확인한다.
5. 성공한 실행의 전체 40자리 소스 SHA를 기록한다.

CLI를 이미 설정한 환경에서는 같은 실행을 다음처럼 요청할 수 있다. GitHub CLI 설치·로그인은 이 문서가 자동으로 수행하지 않는다.

```bash
gh workflow run hwp-deploy.yml --repo m4r4m01m/hwp-assistant --ref main -f deploy=false
```

수동 실행에는 저장소 쓰기 권한이 필요하다. 워크플로는 기본 브랜치에 존재해야 한다. GHCR 패키지 접근·Actions 정책·사용량 문제가 있으면 해당 설정을 확인하고, 검증 실패를 건너뛰어 게시하지 않는다.

## 4. 홈서버 runner 계정과 서비스 연결

### 운영 파일의 소유자 확인

runner는 이 저장소 배포에 사용할 비root 계정으로 실행한다. 현재 스크립트는 운영 설정에 mode `600`, 상태·백업 디렉터리에 mode `700`을 요구한다. 따라서 새 runner 계정에 **그룹 읽기 권한만 추가하는 방법은 맞지 않는다.** 기존 운영 계정을 사용할지, 전용 계정으로 소유권을 인수할지 먼저 정한다.

전용 계정으로 옮길 때는 기존 설정 내용과 백업을 보존하고, 운영 중인 예약 백업·관리 명령의 실행 계정도 함께 확인한다. 확인 없이 `/opt` 전체를 재귀 chown하거나 파일을 world-readable로 바꾸지 않는다. runner 계정의 Docker 접근은 호스트 관리자에 준하는 권한이므로 이 계정에 개인 Codex 인증이나 불필요한 개인 작업 폴더를 연결하지 않는다.

### GitHub 등록

1. [저장소 Settings → Actions → Runners](https://github.com/m4r4m01m/hwp-assistant/settings/actions/runners)에서 `New self-hosted runner`를 선택한다.
2. 서버가 현재 배포 대상과 같은 Linux x64인지 확인한다. ARM 서버에 x64 지시를 그대로 적용하지 않는다.
3. GitHub가 제공하는 현재 runner 다운로드와 checksum 검증 명령을 서버의 별도 runner 디렉터리에서 실행한다. 소스 checkout이나 `/opt/hwp-assistant`의 운영 데이터 디렉터리 안에 설치하지 않는다.
4. GitHub의 Configure 안내로 이 저장소에 등록한다. 기본 라벨 `self-hosted`, `linux`, `x64`를 유지하고 **추가 라벨 `hwp-deploy`**를 지정한다.
5. 등록 토큰은 GitHub가 발급하는 단기 토큰이며 앱 로그인 토큰과 다르다. 서버에서만 사용하고 채팅·저장소·작업 로그에 넣지 않는다.

등록에는 해당 서버 접근과 GitHub 저장소 소유자/관리자 권한이 필요하다. runner가 GitHub로 연결하는 구조이므로 이 경로를 위해 새로운 외부 SSH 수신 포트를 열 필요는 없다. GitHub 및 GHCR·이미지 다운로드에 필요한 외부 통신은 가능해야 한다.

### Linux 서비스로 실행

등록을 끝낸 뒤 **선택한 비root runner 계정으로 로그인한 터미널**에서 runner 설치 디렉터리로 이동한다. 이미 `run.sh`가 실행 중이면 먼저 그 프로세스를 정상 종료한다.

```bash
sudo ./svc.sh install "$(id -un)"
sudo ./svc.sh start
sudo ./svc.sh status
```

GitHub의 runner 화면에서 Online/Idle 상태와 `hwp-deploy` 라벨을 확인한다. 라벨은 작업 선택 조건이며 별도의 보안 승인 기능은 아니다. 등록만 하고 runner 프로세스가 꺼져 있으면 배포 작업을 받을 수 없다.

## 5. 실제 GitHub 환경 보호 기능 확인

[Settings → Environments](https://github.com/m4r4m01m/hwp-assistant/settings/environments)에서 `hwp-production`의 사용 가능 여부를 확인한다. 지원되는 경우 배포 브랜치를 명시적인 `main`으로 제한하고 필요한 승인 규칙을 설정한다. `Protected branches only`는 보호 규칙이 하나도 없는 저장소에서 기대한 제한이 되지 않을 수 있으므로 실제 규칙을 확인한다.

워크플로의 `environment: hwp-production` 한 줄이 승인자를 자동으로 만들지는 않는다. 공식 문서상 GitHub Free/Pro/Team의 required reviewers는 public 저장소에서만 제공되므로, 이 private 저장소에서 별도 승인 기능을 당연히 쓸 수 있다고 가정하지 않는다.

환경은 지원되지만 별도 reviewer 기능이 없으면 제한된 운영자의 main 수동 실행을 배포 승인으로 사용한다. 환경 자체를 사용할 수 없거나 원하는 통제가 불가능하면 GitHub에서는 `deploy=false`로 검증·게시까지만 수행하고, [runbook의 수동 pull](deployment-runbook.md)로 운영 교체를 실행할 수 있다. 기존 정책을 우회하거나 저장소를 공개로 전환하지 않는다.

## 6. 첫 실제 배포와 완료 판정

기존 데이터 인수, 로컬 진단, runner 연결 및 GitHub 설정이 확인된 뒤 `main`의 수동 실행에서 `deploy=true`를 선택한다. 이 실행은 main의 당시 SHA를 다시 검증·게시한 후 같은 SHA를 배포한다. 이전 실행 이후 main이 이동했다면 이전에 검증한 SHA와 같다고 간주하지 않는다.

홈서버 job은 기존 DB로 일관된 백업을 만든 뒤 DB와 앱 이미지를 교체한다. 운영 쓰기가 일시 중단되고, 세션 변경본을 처음 배포하면 기존 브라우저는 다시 로그인해야 한다. 앱 기동 실패 시 DB를 삭제하거나 자동 downgrade하지 않는다.

공개 URL의 기본 smoke는 자격증명을 보내지 않아 로그인·저장까지 증명하지 않는다. 최초 배포 검수에서 서버 로컬의 env를 사용해 합성 문서 저장을 명시적으로 확인할 수 있다.

```bash
python3 scripts/deploy/smoke.py \
  --url https://pseudowasabi-svr.taile7b6c4.ts.net/hwp-assistant/ \
  --env-file /opt/hwp-assistant/production.env --upload-sample
```

이 명령은 합성 문서 하나를 만들고 `.deploy/smoke-receipt.json`에 식별자·버전·해시만 기록한다. 별도로 검토한 재시작 후 `--upload-sample` 대신 `--verify-receipt`로 동일 바이트가 유지되는지 확인한다. 브라우저 편집·저장·재열기·이전 버전과 실제 ingress의 전달 헤더도 확인한다.

현재 공개 health/smoke는 기대한 전체 release SHA와의 대조까지 수행하지 않는다. 첫 검수에서는 홈서버 이미지 revision 확인과 프록시 대상 확인을 함께 수행한다. 자동 release SHA 대조, 정기 외부 백업 및 복원 리허설은 후속 보강 항목이다.

## 공식 참고

- [GitHub: self-hosted runner 추가](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
- [GitHub: runner 서비스 등록](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application)
- [GitHub: runner 통신과 작업 배정](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
- [GitHub: 수동 workflow 실행](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [GitHub: 배포 환경·보호 규칙과 플랜 제약](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
