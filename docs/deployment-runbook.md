# Ubuntu / Tailscale Funnel 실배포 실행 안내

**기준:** 외부 `/hwp-assistant/` → 호스트 Nginx의 접두 경로 제거 → `127.0.0.1:23000`.

이 변경은 **문서 기능의 컨테이너 배포 기반**이다. 외부 유료 LLM 제공자 구현과 Codex 전용 보고서 생성기의 교체는 포함하지 않는다. 기존 모델 미설정 오류가 표시될 수 있으며, 개인 Codex 로그인 파일을 서버에 복사하지 않는다. 실제 집 서버에서 편집·저장·한컴 호환성을 검수한 결과와 CI 실행 결과는 별도로 기록해야 한다.

2026-10-22 반응성·세션·정적 파일 변경과 재로그인, 설정 및 검증 범위는 [구현 기록](responsiveness-security-2026-10-22.md)을 먼저 확인한다.

## 1. 추가한 구성

| 파일 | 역할 |
|---|---|
| `compose.deploy.yaml` | 독립적인 운영 Compose. web/API/PostgreSQL 및 외부 영속 볼륨 |
| `deploy/nginx.hwp-assistant.conf` | 컨테이너 Nginx: API 프록시, 공개 쿠키 경로, 전달된 HTTPS 보존 |
| `deploy/host-nginx-location.conf.example` | 기존 호스트 server 블록에 적용할 HWP Assistant 전용 location 예시 |
| `scripts/deploy/init.py` | 임의 비밀번호·사용자 토큰 생성, 권한 600 설정, 최초 볼륨 생성 |
| `scripts/deploy/deploy.sh` | 현재 커밋 빌드 또는 동일 SHA 이미지 pull → 백업 → 기동 → 점검 |
| `scripts/deploy/backup.sh` | 앱 쓰기 중지 후 DB 덤프와 HWPX 파일을 일관된 세트로 백업 |
| `scripts/deploy/smoke.py` | 라우팅·정적 파일·WASM·인증 검사. 합성 문서 쓰기는 명시적 옵션 |
| `.github/workflows/hwp-deploy.yml` | PR/main 검사, 수동 이미지 빌드·실행 검증·GHCR 게시, 선택적 집 서버 배포 |

기존 `compose.yaml`은 루트 경로의 개발 구성으로 보존한다. **운영 파일을 개발 파일과 `-f`로 합치지 않는다.** 호스트 포트 23000과 컨테이너 포트 80은 다르다. API 8000, DB 5432는 호스트에 publish하지 않는다.

## 2. 전제 조건

Ubuntu x86_64, Git, Python 3, Docker Engine 및 Docker Compose v2(권장 2.24 이상), curl, tar, util-linux의 flock이 필요하다. 빌드 단계는 고정 Rust/WASM 엔진을 실제 컴파일한다. 런타임보다 많은 메모리·디스크·외부 다운로드가 필요하며 이 저장소에서 최소 사양을 실측한 것은 아니다.

```bash
uname -m
free -h
df -h /
docker version
docker compose version
sudo ss -ltnp 'sport = :23000'
```

23000을 다른 프로세스가 쓰고 있으면 해당 프로세스의 용도를 먼저 확인한다. 스크립트는 임의의 기존 프로세스를 종료하지 않는다. Docker 권한을 가진 계정은 사실상 호스트 관리자에 준하는 권한을 가진다는 점을 고려한다.

## 3. 최초 준비

저장소 접근 권한이 있는 계정으로 clone하거나 기존 checkout을 보존하며 갱신한다.

```bash
git clone https://github.com/m4r4m01m/hwp-assistant.git
cd hwp-assistant
git switch main
git pull --ff-only

sudo install -d -m 700 -o "$USER" -g "$(id -gn)" /opt/hwp-assistant
python3 scripts/deploy/init.py --owner yoseob \
  --env-file /opt/hwp-assistant/production.env
```

생성한 `production.env`는 서버에만 둔다. 로그인 토큰은 해당 파일의 `HWP_AUTH_TOKENS`에서 본인이 로컬 편집기로 확인한다. 파일·화면·토큰을 GitHub, 채팅, 빌드 로그에 붙이지 않는다. 사용자 추가 시 서로 다른 32자 이상의 난수 토큰을 발급하고 API를 재배포한다.

초기화는 파일이나 운영 볼륨이 이미 있으면 중단한다. **재배포 때 다시 초기화하지 않는다.** 기존 PostgreSQL 볼륨의 비밀번호는 `.env` 값을 바꾼다고 자동 변경되지 않는다. 초기화가 중간에 실패하면 만들어진 파일과 볼륨을 조사하고 보존한 뒤 복구한다.

생성하는 볼륨:

- `hwp-assistant-prod-metadata`: PostgreSQL 데이터
- `hwp-assistant-prod-documents`: HWPX 원본·저장 버전

둘은 Compose에 같이 연결되지만 서로 다른 볼륨이다. `external: true`이므로 일반 Compose 종료 명령이 삭제하지 않는다. `docker volume rm`, 전체 볼륨 정리, 디스크 장애는 여전히 데이터를 잃게 할 수 있다. 기존 개발용 SQLite 및 다른 Compose 볼륨을 자동 이전하거나 초기화하지 않는다.

## 4. 호스트 Nginx 확인

`deploy/host-nginx-location.conf.example`를 보고 `/etc/nginx/sites-available/side`의 **HWP Assistant location만** 적용한다. 기존 Funnel, loopback listener, 다른 서비스의 24000/28080 경로는 변경하지 않는다.

핵심은 `proxy_pass http://127.0.0.1:23000/;`의 마지막 `/`이다. 브라우저는 `/hwp-assistant/api/...`를 쓰지만 컨테이너는 `/api/...`를 받는다.

Ubuntu의 `proxy_params` 내용에 따라 `X-Forwarded-Proto`가 중복 전달될 수 있다. 제공한 예시는 include 대신 헤더를 각각 한 번씩 선언한다. 호스트와 컨테이너 모두 요청 본문을 50m까지 수용하되, 앱의 HWPX 30MB 제한은 그대로 유지한다. 이는 Base64 변경분 전송의 크기 증가를 고려한 초기 설정이며 큰 파일 저장 검수가 필요하다.

```bash
sudo nginx -t && sudo systemctl reload nginx
```

프로그램이 아직 시작되지 않았다면 외부 접속은 502일 수 있다. 23000이 loopback이더라도 **Funnel을 통해 공개된다.** 운영 entrypoint는 인증 토큰이 없는 local 모드로 시작하지 않는다.

## 5. 첫 배포: 서버에서 직접 빌드

처음에는 registry나 self-hosted runner 없이 실행할 수 있다.

```bash
# 저장소 루트, 깨끗한 main checkout에서
bash scripts/deploy/deploy.sh build
```

이 스크립트는 자격증명 파일과 실제 커밋을 확인하고, API/web을 빌드한 뒤 PostgreSQL을 준비한다. 기존 앱 쓰기를 중지하고 DB·문서 백업을 만든 뒤 새로운 앱을 올리고 내부 경로를 점검한다. 새 DB인 최초 실행에도 빈 DB/파일의 백업을 만들 수 있다.

상태 확인:

```bash
export HWP_IMAGE_TAG=$(git rev-parse HEAD)
docker compose --env-file /opt/hwp-assistant/production.env \
  -f compose.deploy.yaml ps

docker compose --env-file /opt/hwp-assistant/production.env \
  -f compose.deploy.yaml logs --tail=100 api web
```

`docker compose config` 전체 출력에는 비밀값이 포함될 수 있다. 검사는 `config --quiet`를 사용한다. 운영 Compose는 명시적 `--env-file`을 사용하며, 설정값을 컨테이너에 전달하는 항목은 Compose의 `environment`에 선언돼 있다.

## 6. 외부 검수

```bash
python3 scripts/deploy/smoke.py \
  --url https://pseudowasabi-svr.taile7b6c4.ts.net/hwp-assistant/
```

위 기본 점검은 데이터 쓰기를 하지 않는다. 인증·쿠키와 합성 HWPX 저장까지 시험하려면 **서버에서** 아래 명령을 명시적으로 실행한다. 자격증명을 서버 밖으로 옮길 필요가 없다.

```bash
python3 scripts/deploy/smoke.py \
  --url https://pseudowasabi-svr.taile7b6c4.ts.net/hwp-assistant/ \
  --env-file /opt/hwp-assistant/production.env --upload-sample
```

합성 문서 1개가 생기고 `.deploy/smoke-receipt.json`에 ID·버전·해시만 기록된다. 기존 문서는 삭제하지 않는다. 재시작 후 같은 명령에서 `--upload-sample` 대신 `--verify-receipt`를 사용하면 이전 바이트가 유지되는지 확인한다.

브라우저에서는 로그인, 실제 편집기, 본문·표 편집, 저장·재열기, 버전 비교·복원, 로그아웃, 다른 사용자 권한을 확인한다. 특히 브라우저 Network에서 루트 `/api/`, `/rhwp/`, `/assets/`로 요청이 새는지 확인한다. HTTP smoke나 WASM 해시 일치만으로 편집·한컴 왕복·실모델 성공을 표시하지 않는다.

## 7. GitHub Actions 파이프라인

### 7.1 가벼운 검사

PR과 main push 시 Python 회귀 시험, TypeScript 검사, 공개 경로 검사, 빌드 보호 단위 시험 및 배포 스크립트 검사를 실행한다. 이 작업은 GitHub-hosted runner에서 실행하며 집 서버에는 접근하지 않는다.

### 7.2 이미지 빌드·검증·게시

GitHub → Actions → **HWP deployment** → Run workflow에서 main을 선택한다. 처음에는 `deploy`를 체크하지 않는다. Rust/WASM 빌드가 무거우므로 모든 push마다 실행하지 않고 명시적으로 요청할 때만 실행한다.

순서:

1. Python/TypeScript/배포 검사 통과
2. `linux/amd64` API 및 `/hwp-assistant/` 전용 web 이미지 빌드
3. 일회용 PostgreSQL·API·web·자가서명 HTTPS 게이트웨이 실행
4. 로그인·쿠키·합성 HWPX 업로드·다운로드·재시작 후 동일 파일 검증
5. Chromium에서 실제 rhwp와 버전 비교 렌더러 로드
6. 위 검증이 통과한 경우에만 GHCR에 이미지 게시

태그는 `latest`가 아니라 **40자리 소스 커밋 SHA**를 사용한다. GHCR 태그 자체의 변경 불가 정책을 보장한다는 뜻은 아니며, 배포 스크립트는 이미지의 revision 라벨이 소스 SHA와 일치하는지도 확인한다.

```text
ghcr.io/m4r4m01m/hwp-assistant-api:<commit-sha>
ghcr.io/m4r4m01m/hwp-assistant-web:<commit-sha>
```

저장소에서 Actions 실행과 `GITHUB_TOKEN`의 `packages: write`가 허용되어 있어야 한다. 패키지는 저장소와 연결하며 공개 전환하지 않는다. 계정 플랜·사용량에 따른 Actions/패키지 비용은 사용자가 확인한다. 이 파이프라인 생성은 실제 클라우드 빌드 통과와 같지 않다.

수동 pull 배포는 **동일 SHA의 깨끗한 checkout/worktree**에서 한다. private GHCR이면 해당 패키지를 읽을 수 있는 최소 권한 토큰으로 서버에서 `docker login ghcr.io`가 필요하다. 토큰을 명령행 인자나 Git 파일에 넣지 않는다.

```bash
# 검증된 SHA가 현재 HEAD인 전용 checkout/worktree에서
bash scripts/deploy/deploy.sh pull
```

### 7.3 선택적 집 서버 배포

완전한 배포 실행을 원할 때만 다음을 설정한다.

- Ubuntu에 이 저장소 전용 GitHub Actions self-hosted runner 설치
- runner 라벨: `self-hosted`, `linux`, `x64`, `hwp-deploy`
- runner 계정이 Docker와 `/opt/hwp-assistant/production.env`에 접근 가능해야 함
- `hwp-production` Environment에 main 배포 제한 및 필요한 승인 규칙 설정
- 공개 저장소나 신뢰할 수 없는 기여자의 작업이 이 runner에서 실행되지 않도록 접근 통제

이후 main에서 Run workflow의 `deploy`를 체크하면 검증된 두 이미지를 게시한 뒤, 집 서버 runner가 pull·백업·교체·점검을 수행한다. **SSH 공개 포트, Tailscale 인증 키, Funnel 설정 변경은 필요 없다.** runner 설치·권한·승인 설정은 이 코드가 자동으로 수행하지 않는다.

개인 PC에서 임의 PR 코드를 실행하지 않는다. workflow는 집 서버 작업을 main의 명시적 수동 실행으로 한정하지만, runner/Docker 관리 권한 자체가 매우 강하므로 별도의 신뢰 경계가 필요하다.

## 8. 백업·실패·복원

```bash
bash scripts/deploy/backup.sh
```

백업은 `/opt/hwp-assistant/backups/` 아래에 DB custom dump, 문서 tar.gz, SHA256SUMS, 실행 영수증으로 보관된다. 쓰기 중지로 잠깐 서비스가 중단된다. standalone 백업은 성공·실패 후 이전에 실행되던 앱을 다시 시작한다. 배포 중 백업 실패는 기존 앱을 재시작하고 교체를 중단한다.

이 백업에는 API 키와 토큰을 의도적으로 넣지 않는다. `production.env`도 별도 보호된 위치에 백업하고, DB·문서 세트는 다른 장치/저장소에도 보관한다. 같은 디스크에 있는 볼륨과 백업만으로는 디스크 고장을 대비할 수 없다.

새 앱 기동 실패 시 DB를 지우거나 자동 downgrade하지 않는다. 이전 이미지 ID와 백업 영수증을 확인한다. 스키마 호환성이 확인된 경우에만 이전 커밋의 별도 worktree에서 이전 이미지로 재배포한다. 그렇지 않으면 **격리된 새 볼륨/DB에서** `pg_restore`와 문서 아카이브 복원을 검증한 후 전환한다. 원본 운영 볼륨을 삭제하거나 실행 중 PGDATA를 tar로 복제하지 않는다.

## 9. 이번 변경에 포함하지 않은 것

- 실제 Ubuntu 서버 명령 실행, 호스트 Nginx 자동 수정
- 실제 Funnel 경로에서의 사용자 승인·저장 및 한컴 호환성 검수 완료 주장
- 사내 Qwen 실모델 연결 또는 외부 유료 LLM 어댑터 구현
- Codex 전용 보고서 생성·보완·웹검색의 API 전환
- SQLite → PostgreSQL 기존 데이터 자동 이전
- 자동 DB 스키마 downgrade, 무중단 배포, 다중 API 워커

## 공식 참고

- Docker Compose startup order: https://docs.docker.com/compose/how-tos/startup-order/
- Compose external volumes: https://docs.docker.com/reference/compose-file/volumes/
- GitHub Container registry: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
- Nginx proxy cookie/header/URI rules: https://nginx.org/en/docs/http/ngx_http_proxy_module.html
- Vite public base: https://vite.dev/guide/build.html#public-base-path
