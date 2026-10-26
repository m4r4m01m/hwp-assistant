# 비밀번호 로그인·가입 승인·계정 감사 기록

작성일: 2026-10-26 (한국 시간). 개발 기준은 main [`82d3126b0133895abcab462d38121f6768fc9a81`](https://github.com/m4r4m01m/hwp-assistant/commit/82d3126b0133895abcab462d38121f6768fc9a81)이다. 이 문서는 `codex/account-approval-audit`의 동작과 시험 절차를 설명한다. PR·정확한 검증 결과는 [PROJECT_CONTEXT](PROJECT_CONTEXT.md)의 이번 작업 기록을 따른다. 문서의 절차를 적은 것과 실제 운영 서버에 적용한 것은 별개다.

## 계정과 권한

- `guest`는 운영자가 정한 비밀번호로 접속하는 공용 체험 계정이다. 같은 guest를 사용하는 사람은 문서 주체와 감사 기록의 계정 ID를 공유한다. 감사 기록만으로 그중 어느 사람이 조작했는지는 식별할 수 없다. 사람별 책임추적이 필요하면 승인된 개인 계정을 사용한다.
- `admin`은 운영자가 명시적으로 만든 관리 계정이다. ID 문자열만으로 관리자 권한이 생기지 않는다. 기존에 같은 이름의 일반 계정이 있어도 자동 승격하지 않는다.
- 공개 회원가입은 기본적으로 승인 대기 상태로 접수한다. 접수 완료는 로그인 완료가 아니며 세션을 발급하지 않는다. admin이 승인한 뒤 개인 비밀번호로 로그인한다. 거절·비활성 계정은 로그인할 수 없다.
- 관리자 권한은 가입 승인·계정 관리·감사 기록 조회를 위한 것이다. 이번 체험 환경은 `HWP_LIBRARY_ACCESS_MODE=shared`를 명시적으로 선택하여 guest·admin·승인 회원 모두 같은 문서 목록과 읽기·편집·관리 권한을 사용한다. 따라서 과거에 만들어 둔 문서도 활성 승인 계정에 공개되므로 운영자가 전환 전에 범위를 확인한다. 기존 문서 ACL·원본·버전의 주체를 다시 쓰지는 않는다. 기본값 `private`에서는 기존 계정별 문서와 명시적 공유를 유지하며 관리자 역할만으로 우회하지 않는다.
- 기존 계정은 지우지 않고 필요한 계정을 비활성화한다. 문서·수정자·감사 기록과 ID 예약을 보존한다. guest/admin 외 계정을 서버 시작 때 자동 정리하지 않는다. 마지막 활성 관리자를 비활성화하여 관리 경로를 잃는 요청은 거부한다.

체험 환경에는 다음 값을 운영자가 명시적으로 설정한다. 기존 설치에 새 기능이 자동으로 열리지 않도록 Compose 기본값은 계정/가입 꺼짐·문서 `private`·기존 토큰 허용·AI 허용목록을 유지한다.

```dotenv
HWP_ACCOUNTS_ENABLED=1
HWP_REGISTRATION_ENABLED=1
HWP_REGISTRATION_REQUIRE_APPROVAL=1
HWP_LIBRARY_ACCESS_MODE=shared
HWP_LEGACY_TOKEN_ACCESS_ENABLED=0
HWP_CODEX_ACCOUNT_ACCESS=approved_accounts
HWP_SAMPLE_ACCOUNTS=[]
```

새 가입의 관리자 승인은 `HWP_REGISTRATION_REQUIRE_APPROVAL=1`이 기본이다. 기존 승인 계정을 일괄 승인 대기로 바꾸지 않는다. private env를 수정했다면 현재 이미지와 Compose로 API를 재생성해야 하며 `docker restart`만으로 변경이 적용되지는 않는다. 원래 토큰 설정을 지우거나 최초 설치 초기화 도구를 기존 운영 서버에서 다시 실행하지 않는다.

## 최초 관리자와 guest 준비

대상 서버의 API 컨테이너에서 실행한다. 아래 `TEST-ADMIN`은 실제 사번이 아닌 예시다. 비밀번호는 명령줄·문서·채팅에 쓰지 않고 터미널 프롬프트에 입력한다.

```sh
python -m app.account_admin create --user admin --role admin --name '관리자' --employee TEST-ADMIN
python -m app.account_admin create --user guest
```

기존 계정이 있으면 `create`는 실패하며 덮어쓰지 않는다. guest 비밀번호 변경은 `python -m app.account_admin password --user guest`로 한다. 이미 존재하는 적법한 일반 계정에 관리 권한을 부여할 때만 운영자가 `python -m app.account_admin promote-admin --user <계정ID>`를 별도로 실행한다. 비밀번호 변경과 비활성화는 기존 비밀번호 세션을 무효화한다. 계정의 문서 소유권은 바뀌지 않는다.

새 승인 대기 계정은 admin 화면에서 확인·승인한다. 사용하지 않을 예전 비밀번호 계정은 문서 주체와 영향 범위를 확인하고 각각 비활성화한다. 관리 화면에 없는 기존 `HWP_AUTH_TOKENS` 주체는 별개다. `HWP_LEGACY_TOKEN_ACCESS_ENABLED=0`은 기존 토큰 로그인·정적 Bearer 인증과 과거 토큰 로그인에서 발급한 세션을 차단한다. 비밀번호 로그인 세션과 작업 전용 제한 토큰의 경계는 유지한다. `HWP_AUTH_TOKENS`의 비어 있지 않은 설정은 현재 운영 기동 검사·기존 ID 예약에 필요하므로 그대로 보존한다. 이 flag가 기본값 `1`인 환경에서는 비밀번호 계정 목록 정리만으로 기존 토큰 접근을 폐기한 것이 아니다. 토큰 원문은 화면이나 로그에 옮기지 않는다. `local` 등 기존 토큰 주체와 신규 비밀번호 계정을 같은 사람이라고 추정하여 병합하지 않는다.

## AI 이용 정책과 Mattermost

심사 중에는 admin 설정에서 승인된 활성 계정과 guest의 Codex 이용을 허용할 수 있다. 이후 허용목록 방식으로 되돌릴 수 있다. DB에 저장한 관리자 설정은 `HWP_CODEX_ACCOUNT_ACCESS` 초기값보다 우선하므로 이미 설정을 저장한 서버에서는 관리자 화면에서 현재 값을 확인하고 변경한다. 승인 대기·거절·비활성 계정에는 이 정책으로 권한을 주지 않는다. 진행 중 Codex 보고서·문단 후보·문서 대화도 등록 계정의 현재 이용 권한을 재검사한다. 계정 비활성화나 허용목록 전환으로 권한이 회수되면 완료 초안/답변으로 게시하지 않는다. 이미 완료된 동결 draft·문서 버전을 수정하거나 별도 HTTP 모델의 정책을 Codex 정책으로 바꾸지는 않는다. 이는 앱의 이용 권한이며 서버 Codex 로그인·모델 준비·공유 일일 한도와 별개다. 개인별 Codex 인증정보를 받거나 유료 AI로 자동 전환하지 않는다.

Mattermost incoming webhook 주소는 private 서버의 `HWP_MATTERMOST_WEBHOOK_URL`에만 둔다. 기본은 HTTPS이며 내부 시험에서 HTTP가 필요한 경우에만 `HWP_MATTERMOST_ALLOW_HTTP=1`을 명시한다. 리다이렉트를 따라가지 않고 전송은 4초 제한 시간과 최대 5회 전송 시도를 사용한다. 주소에는 비밀값이 있으므로 PR·브라우저 응답·감사 기록·HTTPX 로그에 출력하지 않는다. 가입 접수는 먼저 DB에 확정하고 알림 결과와 구분한다. Mattermost에 계정 비밀번호·토큰·사번·문서 내용은 전송하지 않는다. 알림이 오지 않아도 admin 화면의 승인 대기 목록에서 접수 여부를 확인한다. 관리자 설정 응답의 알림 구성 여부·대기/실패 수에도 webhook 주소나 응답 본문은 포함하지 않는다. 운영 webhook으로 실제 메시지를 보내는 검수는 운영자가 주소를 설정한 뒤 별도로 수행한다. 전송 후 결과 기록 전에 프로세스가 중단되면 중복 알림은 가능하며 승인 목록이 정본이다. [Mattermost 공식 안내](https://docs.mattermost.com/developers/integrate/webhooks/incoming)의 JSON text 전송 방식을 사용한다.

## 관리 API 경계

| 경로 | 동작 |
|---|---|
| `POST /api/auth/register` | 기본 202 승인 대기 접수, 세션 없음 |
| `POST /api/auth/password-login` | 올바른 대기 계정은 403 승인 대기 안내, 승인·활성 계정만 세션 발급 |
| `GET /api/admin/accounts` | 관리자만 계정 상태 필터·페이지 목록 조회 |
| `POST /api/admin/accounts/{user_id}/approve` 또는 `/reject` | 미처리 가입 요청 승인/거절 |
| `POST /api/admin/accounts/{user_id}/disable` 또는 `/enable` | 승인된 계정의 이용 중지/재개 |
| `POST /api/admin/accounts/{user_id}/password` | 관리자 비밀번호 지정, 기존 세션 무효화 |
| `GET` / `PATCH /api/admin/settings` | AI 이용 정책 조회/변경 |
| `GET /api/admin/audit` | 관리자만 계정·작업·커서로 감사 기록 조회 |

관리 화면은 목록에 받은 `revision`을 변경 요청의 `expected_revision`으로 전송한다. 검토 후 대상 계정이 달라지면 409로 거부하고 새로 조회한다. 역할·승인·활성 상태는 서버에서 다시 검사하며 화면 버튼을 숨기는 것에 의존하지 않는다. 비밀번호 해시·세션 바인딩·사번은 관리 목록으로 내보내지 않는다. 계정 삭제 API와 웹을 통한 관리자 승격 API는 두지 않는다.

## 감사 기록의 범위

인증과 계정 관리, 파일 생성/추가·이름 변경·새 버전 저장·복원·관련 관리 작업을 계정 ID와 대상 ID, 시각으로 추적한다. 문서 본문이나 수정 문장을 감사 기록에 복사하지 않는다. 본문 차이는 기존 버전 비교·수정자 기록으로 확인한다. 성공한 문서 저장은 해당 DB 확정과 연결하며, 재시도로 같은 저장이 중복 기록되지 않도록 기존 멱등 경계를 사용한다.

관리자 화면에서 계정·작업별로 조회한다. 앱에는 감사 기록을 수정하거나 지우는 관리 API를 두지 않는다. 다만 DB 운영자가 원장을 바꾸는 것까지 암호학적으로 방지하는 외부 감사 저장소는 아니므로 별도 보관 정책이 필요하다. 배포 이전 활동을 새 기록처럼 만들어 채우지 않는다. 문서 영구 삭제 기능은 새로 추가하지 않으며 현재 제공하는 표준/디자인 숨김 삭제와 서버에서 관찰한 파일 작업을 기록한다. 브라우저 안에서만 실행한 로컬 내보내기 완료나 guest를 사용한 실제 개인의 신원까지 증명하지는 않는다. DB와 문서 파일의 일관된 백업에 감사 기록도 포함한다.

## 병합 전 서버 시험과 현재 GitHub Actions

이번 변경은 `.github/workflows/hwp-deploy.yml`의 main 제한을 바꾸지 않는다. 현재 **HWP deployment**에서 브랜치를 선택하거나 CLI의 `--ref`로 지정할 수는 있지만, `build-verify-publish`와 `deploy-home`은 `github.ref == 'refs/heads/main'` 조건을 갖고 있다. 작업 브랜치에서는 검사가 실행되고 무거운 이미지 빌드/게시·홈서버 배포는 건너뛴다. `deploy=true`도 이 조건을 우회하지 않는다. **HWP deploy published images**도 main과 게시 run·main 계보를 검증하므로 작업 브랜치 시험용 우회 경로가 아니다. [실제 workflow](../.github/workflows/hwp-deploy.yml), [게시 이미지 배포](deploy-published-images.md), [GitHub 수동 실행 안내](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)를 함께 확인한다.

현재 Actions 이름 그대로 브랜치 이미지를 게시·배포하려면 후속 작업에서 다음을 모두 설계해야 한다. 이번 PR에서는 변경하지 않는다.

1. 검토할 정확한 source SHA를 입력받고 해당 SHA의 필수 검사가 통과했는지 확인한다. 브랜치명이 움직여도 검사·빌드·시험 source가 바뀌지 않도록 모두 같은 SHA를 쓴다.
2. 신뢰한 main의 수동 workflow가 별도 GitHub-hosted runner에서 해당 SHA를 빌드하도록 한다. 이미지 태그와 revision label도 그 SHA로 통일하며 운영 이미지 태그를 덮어쓰지 않는다.
3. 별도 `hwp-staging` 환경·수동 승인·시험 전용 runner/호스트를 사용한다. 운영 runner에 PR 코드를 실행시키거나 운영 env·Codex 인증·볼륨을 전달하지 않는다.
4. staging의 프로젝트명·외부 볼륨 3개·포트·도메인·비밀번호·비밀정보·배포 잠금/영수증을 분리한다. 기존 `scripts/deploy/common.sh`와 `deploy.sh`는 운영 프로젝트/볼륨을 고정하므로 그대로 재사용하지 않는다.
5. 합성 자료와 별도 계정으로 검수한 뒤 PR의 같은 SHA를 승인한다. 운영 전환은 기존 main-only 경로와 DB/파일 백업 계약을 유지한다.

main 조건 두 줄을 지우는 것만으로 운영 전의 안전한 검수 환경이 생기지는 않는다. 지금 workflow를 유지하면서 검수하려면 아래처럼 **수동으로 격리한 시험 환경**을 준비할 수 있다.

## 수동 격리 시험 환경 예시

아래는 운영 배포가 아닌 x86_64 Linux Docker 호스트의 별도 시험 환경 예시다. Docker daemon·빌드 도구·여유 포트/공간과 검토한 exact SHA의 깨끗한 checkout이 필요하다. 이 문서 작성 환경의 Docker Compose 5.1.1에서 합성 비밀값으로 `config --format json` 결과를 메모리에서 검사했다. 프로젝트명·외부 시험 볼륨 3개·gateway의 loopback 24444 단일 공개·web/API/DB의 공개 포트 없음·계정 flag 보간을 확인했고 비밀값은 출력하지 않았다. Docker daemon이 없어 이미지 빌드나 서버 기동은 수행하지 않았다. 이 구성 검증을 실제 기동·HTTPS·모델 검증으로 확대하지 않는다.

운영 `/opt/hwp-assistant/production.env`, 운영 checkout과 볼륨을 복사하거나 수정하지 않는다. 새 checkout에서 `compose.deploy.yaml`와 **시험용** `compose.ci-gateway.yaml`를 아래 override와 함께 사용한다. 개발용 `compose.yaml`는 결합하지 않는다. `-p`만 변경하면 고정된 운영 외부 볼륨 이름은 그대로이므로 충분하지 않다.

시험 상태 디렉터리를 예를 들어 `/opt/hwp-assistant-account-preview`에 소유자 전용으로 만들고 private `preview.env`에 독립 DB 비밀번호와 자동화 토큰을 생성한다. 운영 초기화 스크립트는 실행하지 않는다. 기존 helper의 문자열 생성 함수만 사용하는 예시이며 비밀은 출력하지 않는다.

```sh
umask 077
mkdir -m 700 /opt/hwp-assistant-account-preview
python3 - <<'PY'
import os
import sys
from pathlib import Path
sys.path.insert(0, 'scripts/deploy')
from init import settings
path = Path('/opt/hwp-assistant-account-preview/preview.env')
content = settings('preview-automation')
replacements = {
    'HWP_ALLOWED_HOSTS': 'localhost,127.0.0.1,[::1]',
    'HWP_ACCOUNTS_ENABLED': '1',
    'HWP_REGISTRATION_ENABLED': '1',
    'HWP_CODEX_USER': 'admin',
    'HWP_LLM_ALLOWED_USERS': 'admin',
    'HWP_REGISTRATION_REQUIRE_APPROVAL': '1',
    'HWP_LIBRARY_ACCESS_MODE': 'shared',
    'HWP_LEGACY_TOKEN_ACCESS_ENABLED': '0',
    'HWP_CODEX_ACCOUNT_ACCESS': 'approved_accounts',
    'HWP_SAMPLE_ACCOUNTS': '[]',
}
present = {line.split('=', 1)[0] for line in content.splitlines() if '=' in line}
content = '\n'.join(
    key + '=' + replacements[key] if '=' in line and (key := line.split('=', 1)[0]) in replacements else line
    for line in content.splitlines()
) + '\n'
content += ''.join(key + '=' + value + '\n' for key, value in replacements.items() if key not in present)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(content)
PY
```

private `preview.override.yaml`도 같은 디렉터리에 저장한다. 아래 `!override`는 기존 운영 포트를 목록에서 제거하여 교체하기 위한 것이며 [Docker Compose 2.24.4 이상](https://docs.docker.com/reference/compose-file/merge/#replace-value)이 필요하다. 오래된 Compose에서는 포트가 추가로 병합될 수 있으므로 진행하지 않는다.

```yaml
services:
  web:
    ports: !override []
  gateway:
    ports: !override ["127.0.0.1:24444:443"]
volumes:
  metadata:
    name: hwp-assistant-account-preview-metadata
  documents:
    name: hwp-assistant-account-preview-documents
  codex-state:
    name: hwp-assistant-account-preview-codex-state
```

다음은 검수할 exact SHA의 checkout에서 실행하는 준비 명령이다. 시험 볼륨이 이미 있으면 먼저 그 환경의 소유자와 사용 여부를 확인하고, DB 비밀번호를 다시 생성하지 않는다. 운영 볼륨이나 다른 작업의 볼륨을 삭제하지 않는다.

```sh
export HWP_IMAGE_TAG="$(git rev-parse HEAD)"
export HWP_CI_CERT_DIR=/opt/hwp-assistant-account-preview/certs
mkdir -m 700 "$HWP_CI_CERT_DIR"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
  -keyout "$HWP_CI_CERT_DIR/key.pem" -out "$HWP_CI_CERT_DIR/cert.pem" \
  -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' 2>/dev/null
for volume in hwp-assistant-account-preview-metadata hwp-assistant-account-preview-documents hwp-assistant-account-preview-codex-state; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    echo 'Preview volume already exists; verify its owner and original private env first.' >&2
    exit 1
  fi
done
for volume in hwp-assistant-account-preview-metadata hwp-assistant-account-preview-documents hwp-assistant-account-preview-codex-state; do
  docker volume create --label app=hwp-assistant-account-preview "$volume" >/dev/null
done
preview() {
  docker compose -p hwp-assistant-account-preview \
    --env-file /opt/hwp-assistant-account-preview/preview.env \
    -f compose.deploy.yaml -f compose.ci-gateway.yaml \
    -f /opt/hwp-assistant-account-preview/preview.override.yaml "$@"
}
preview config --quiet
preview build api web
preview up -d --no-build --wait --wait-timeout 180
preview exec api python -m app.account_admin create --user admin --role admin --name '검수 관리자' --employee TEST-ADMIN
preview exec api python -m app.account_admin create --user guest
```

Compose 보간에 영향을 주는 상위 셸의 `POSTGRES_PASSWORD`, `HWP_AUTH_TOKENS`, `HWP_IMAGE_PREFIX`, 계정/AI 변수 등이 있으면 명령을 별도 깨끗한 셸에서 실행하여 preview.env를 덮어쓰지 않도록 한다. `config --quiet`는 구문과 보간 검증이며 비밀값을 출력하는 `config` 전체 출력을 채팅/로그에 남기지 않는다. 이미지 revision label과 checkout SHA, 기동 후 시험 전용 볼륨 이름·포트만 필요한 항목으로 확인한다.

원격 호스트라면 별도 터미널에서 `ssh -N -L 24444:127.0.0.1:24444 USER@SERVER`로 터널을 만들고 **https://localhost:24444/hwp-assistant/**를 연다. 직접 생성한 시험 인증서의 지문과 호스트를 확인하고 시험용 브라우저 프로필에서 신뢰한다. 운영과 다른 호스트명을 사용한다. 쿠키는 포트별로 분리되지 않으므로 운영과 같은 호스트명에 포트만 바꿔 공개하지 않는다. 기존 localhost 개발 서비스가 있으면 별도 브라우저 프로필을 사용한다. 시험 gateway나 API/DB를 인터넷에 바로 노출하지 않는다.

guest/admin 비밀번호 로그인 → 새 개인 계정 가입 대기 → 승인 전 로그인 거부 → admin 승인 → 로그인 → 파일 생성/저장/복원·모든 승인 계정의 공용 문서 접근·계정별 감사 기록 → 비활성/비밀번호 변경 후 세션 무효화를 확인한다. 승인 대기와 일반 계정이 admin API를 직접 호출해도 거부되는지도 확인한다. Mattermost는 시험용 webhook만 설정한다.

실제 AI 검수에는 **새 시험 Codex 볼륨에서** `preview exec api codex login --device-auth`로 별도 인증하고, admin 설정의 AI 정책·공유 한도를 확인한 뒤 합성 문서로만 요청한다. 운영 인증 파일을 복사하지 않는다. 모델 호출은 코드/세션 시험과 별도 증거로 기록한다. 기존 운영으로 되돌릴 때 새 가입을 받는 구버전은 승인 대기 계약을 모를 수 있으므로 먼저 가입 접수를 중단하고 스키마/계정 상태 호환성을 확인한다. 자동 스키마 downgrade나 ID/이력 재작성으로 해결하지 않는다. 시험을 중단할 때는 `preview down`으로 이 프로젝트만 정지하고 원 DB/문서·볼륨은 보존한다. 운영 전환·스키마 되돌리기·볼륨 정리를 이 명령으로 대신하지 않는다.
