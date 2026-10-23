# 게시된 이미지 배포만 하기

## 현재 설치 경로 유지

Runner가 `~/apps/hwp-assistant/actions-runner`에 있어도 이동/재등록할 필요는 없다.
기본 `_work` 설정에서는 작업 checkout이 그 아래 별도 디렉터리에 생긴다.
이번 workflow는 각 실행/시도마다 새로운 `release-<run_id>-<attempt>`에 checkout한다.
기존 소스 루트나 Runner 설치 폴더에서는 checkout 정리/초기화를 실행하지 않는다.
Runner 디렉터리에는 인증 정보가 있으므로 Git에 추가하지 않는다. `.gitignore`의
`actions-runner/`와 `.dockerignore`의 제외 규칙을 유지한다. 상위 저장소에서
`git clean -xfd`, 무분별한 삭제/초기화를 실행하지 않는다.

`run.sh`도 실행 중이면 작업을 받을 수 있다. 터미널을 닫거나 재부팅해도 유지하려면,
진행 중인 작업이 없는 것을 확인하고 기존 `run.sh`를 Ctrl+C로 종료한 다음:

```bash
cd "$HOME/apps/hwp-assistant/actions-runner"
sudo ./svc.sh install "$(id -un)"
sudo ./svc.sh start
sudo ./svc.sh status
```

이미 서비스 설치가 되어 있으면 install을 반복하지 않는다. 같은 Runner를
run.sh와 서비스로 동시에 실행하지 않는다. Docker 그룹을 새로 부여했다면
재로그인 후 Runner도 재시작해야 변경된 그룹 권한이 적용된다.

## 서버에서 먼저 확인할 사항

Runner를 실행한 동일한 비root 사용자로 별도 터미널에서 실행한다.

```bash
cd "$HOME/apps/hwp-assistant"
python3 -B scripts/deploy/check-host.py \
  --env-file /opt/hwp-assistant/production.env
```

`BLOCKED`가 있으면 배포하지 않는다. `MANUAL` 중 특히 기존 DB/문서 저장 위치,
23000 포트 소유자, 실행 프로젝트를 확인한다. 기존 서비스가 있는데 예정된
프로젝트/볼륨이 없으면 자동 초기화하지 말고 데이터 인수부터 검토한다.
설정 파일은 현재 Runner 사용자가 소유하고 권한 600이어야 한다.
`/opt/hwp-assistant/state`, `backups`도 같은 사용자가 쓸 수 있어야 한다.
기존 DB 비밀번호와 로그인 자격증명은 재생성하지 않는다.

GitHub Settings → Actions → Runners에서 Idle/Online 상태 및
`self-hosted`, `linux`, `x64`, `hwp-deploy` 라벨을 확인한다.
Settings → Environments의 `hwp-production`을 main으로 제한하고, 지원되는
플랜에서는 승인 규칙을 설정한다. Docker 접근은 관리자급 권한이므로 신뢰할 수
없는 PR 작업을 운영 Runner에서 실행하지 않는다.

## 재빌드 없는 배포

이 파일과 `.github/workflows/hwp-deploy-only.yml`이 main에 병합된 후 사용한다.
성공한 원본 빌드의 Actions URL에서 **run ID**를 입력한다. 예를 들어 기존
성공 실행은 `39952556251`이며 화면의 `#24`와는 다른 번호다.
이 실행의 이미지 태그는 `324b1bfe94f0c4b3062775080cdfb577b954eb1a`다.

```bash
gh workflow run hwp-deploy-only.yml \
  --repo m4r4m01m/hwp-assistant \
  --ref main \
  -f build_run_id=39952556251 \
  -f confirm_deploy=true
```

GitHub 웹: Actions → **HWP deploy published images** → Run workflow → main →
build_run_id 입력 → confirm_deploy 선택. 서버의 기존 소스를 git pull할 필요는 없다.
Runner는 별도 작업 공간에 원본 이미지와 같은 커밋을 checkout한다.

이 명령은 실제 운영 서비스를 변경한다. 백업 중 API/웹 쓰기를 일시 중단한다.
GitHub-hosted 작업이 원본 저장소·워크플로·main·성공 상태·검증/게시 단계·커밋
계보를 확인한 뒤에만 self-hosted 작업을 예약한다. 홈서버는 사전 진단 후
임시 GITHUB_TOKEN 인증으로 GHCR 이미지를 pull하고 기존 deploy.sh의 백업,
이미지 revision 검사, DB/앱 교체, 상태 점검 절차를 그대로 실행한다.
Docker build, Rust 컴파일, npm install, 전체 회귀 테스트는 이 배포 경로에서 하지 않는다.

두 패키지에 이 저장소의 Actions 읽기 권한이 있어야 한다. GHCR denied 오류가
발생하면 각 패키지 Settings → Manage Actions access에서 해당 저장소의 Read
접근을 확인한다. Runner 등록 토큰이나 앱 로그인 토큰으로 Docker 로그인하지 않는다.

## 확인

```bash
docker ps --filter 'label=com.docker.compose.project=hwp-assistant-prod' \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
cat /opt/hwp-assistant/state/last-success.json
```

state 기록은 로컬 smoke 성공을 의미한다. 별도의 공개 HTTPS smoke가 성공했는지
Actions에서 확인하고, 브라우저에서 재로그인·열기·편집·저장·재열기를 검수한다.
운영 설정·문서 볼륨·DB 볼륨은 작업 폴더 밖에 유지한다. 수동 복구에 사용할
출시별 checkout은 자동 삭제하지 않으며 디스크 정리는 운영자가 별도 검토한다.

## 기존 workflow와 구분

- `hwp-deploy.yml`, deploy=false: 빌드·검증·게시, 운영 배포 없음.
- `hwp-deploy.yml`, deploy=true: 빌드·검증·게시 후 운영 배포.
- `hwp-deploy-only.yml`: 이미 성공한 실행을 선택해 배포만 수행.

배포 workflow를 추가한 커밋과 배포 이미지의 커밋이 달라도 된다. 앱은 원본
게시 커밋의 이미지로 실행된다. 최신 앱 변경을 반영하려면 새 빌드가 필요하다.

## 검증 범위와 제한

`python3 -B scripts/deploy/test_published_run.py`는 모의 API 메타데이터 및 workflow
구성 경계의 오프라인 테스트다. 홈서버, 실제 GHCR 인증, 실제 컨테이너 교체를
시험한 결과가 아니다. 원본 전체 실행이 성공한 경우만 허용하므로, 원본의 게시
이후 배포가 실패한 실행은 현재 정책상 거절한다.

SHA 태그와 revision 라벨을 대조하지만 과거 게시 바이트에 대한 서명 검증이나
원본 digest 고정 배포는 아니다. 태그 쓰기 권한을 제한해야 한다. 향후 게시 시
두 이미지 digest를 영수증에 기록해 검증·고정하는 강화가 가능하다.
이전 이미지를 선택하는 것은 DB 마이그레이션을 역행시키지 않는다. 롤백은
스키마 호환성과 백업 복구를 별도로 검토해야 한다.
