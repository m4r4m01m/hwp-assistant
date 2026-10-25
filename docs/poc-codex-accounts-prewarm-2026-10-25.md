# 홈서버 Codex·계정·편집 자원 미리 받기

> 현재 상태 보충(2026-10-25): 아래는 검토 당시의 설계/검증 범위 기록이다. 이후 PR #31 병합, #32 이력 연결, #33 통합 및 수동 운영 배포 Actions 성공을 확인했다. 최신 source·검증 링크·남은 서버 인증/실모델 확인은 [PROJECT_CONTEXT](PROJECT_CONTEXT.md)를 따른다. 아래의 “미병합/배포하지 않음”을 현재 상태로 해석하지 않는다.

## 범위와 상태

2026-10-25 변경. main 기준 `fc0afef31ff33d3a3817b8576f981ee144f80f52`에서 별도 브랜치로 구현한다. 이 문서는 **배포 완료나 실제 모델 호출 성공을 뜻하지 않는다**. PR은 main에 병합하지 않고 검토용으로 둔다. 원본 HWPX·profile.json·기존 문서/버전·등록 표준·동결 초안의 계약을 변경하지 않는다.

Codex CLI 0.153.0을 공식 release SHA-256으로 고정하여 API 이미지 빌드 때 설치한다. 잠긴 MCP 의존성도 API 이미지에 포함한다. 보고서와 문서 대화는 기존 app-server, 문단 수정 후보는 작업별 MCP 경로를 그대로 쓴다. MCP의 PYTHONPATH는 실제 backend 위치에서 계산하므로 네이티브 체크아웃과 `/app/app` 컨테이너 모두 지원한다. 실행마다 새 임시 작업 폴더·제한된 도구·제한 시간·승인 경계를 유지한다. API 키나 다른 유료 모델로 자동 전환하지 않는다.

## 구독 사용과 다중 사용자: 구분할 사항

공식 안내상 ChatGPT 로그인으로 Codex를 쓰면 해당 구독의 사용 한도가 적용된다. API 요금과 별개이고, $100 요금제를 정액 API 크레딧처럼 볼 수 없다. 실제 남은 한도는 이 앱이 조회하지 않는다. 길이·모델·검색 횟수에 따라 소모가 달라지므로 “공모전이니 무조건 충분”하다고 보장하지 않는다.

개인 계정은 본인 사용이 원칙이다. **소수 이용·비상업 PoC라는 이유만으로 다른 사람의 요청을 한 개인 구독에 모아 처리할 권한이 자동으로 생기지 않는다.** 기본값은 소유자 앱 ID 하나이며 신규 회원·guest에 AI 권한을 자동 부여하지 않는다. `HWP_CODEX_ALLOWED_USERS`는 명시적 운영 ACL이지 OpenAI의 이용 허가를 대신하지 않는다. 다중 사용자 실행을 열기 전 해당 서비스 이용 조건/조직 승인을 확인해야 한다. 심사에서는 소유자가 직접 AI를 시연하고 심사자는 문서 편집·저장 및 결과 검토를 체험하는 구성을 우선한다. 사용자별 인증/조직용 API 방식은 이 PR에서 구현하지 않는다.

확인 자료(2026-10-25):
- [Codex 인증·headless device login](https://developers.openai.com/codex/auth/)
- [ChatGPT 구독으로 Codex 사용](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
- [OpenAI 계정 공유 정책](https://help.openai.com/en/articles/10471989-openai-account-sharing-policy)
- [고정 CLI release](https://github.com/openai/codex/releases/tag/rust-v0.153.0)

회사 자료·개인정보를 외부 모델에 보내도 되는지 별도 확인한다. 개인 구독의 데이터 제어 설정도 확인하고, PoC에서는 합성 자료만 사용한다. 브라우저에서 “AI가 읽을 내용 확인”으로 전송 범위를 볼 수 있다.

## 배포 변경: 프로세스와 저장소

상시 컨테이너는 **3개 그대로**다: `db`, `api`, `web`. 외부 웹 포트는 `127.0.0.1:23000`, 공개 하위 경로는 `/hwp-assistant/`를 유지한다. API/DB/Codex RPC 포트를 외부에 열지 않는다. Codex는 요청 동안 API 컨테이너의 자식 프로세스이며 문단 수정에서는 MCP 자식 프로세스도 잠시 생긴다. GPU나 로컬 LLM 가중치를 설치하지 않는다. 홈서버 실제 메모리·지연은 아직 측정하지 않았다.

기존 외부 볼륨 2개에 `hwp-assistant-prod-codex-state`를 추가한다. `/home/app/.codex`에 연결하며 UID 10001, 디렉터리 0700, 기본 설정 0600이다. Docker 이미지에는 **로그인 정보가 없다**. `auth.json`은 device login 후 해당 private volume에 생긴다. 재배포해도 유지하며 자동 DB/files 백업에는 포함하지 않는다. 삭제 시 재로그인이 필요하다. 사용 중인 개발 PC의 전체 `.codex` 폴더나 MCP 설정을 복사하지 않는다. `docker inspect` 전체 결과·auth.json·private env를 채팅/PR/Actions artifact에 올리지 않는다.

새 설치는 `init.py`가 3개 볼륨을 만든다. 기존 설치는 `deploy.sh`가 **새 Codex 볼륨만 없을 때 생성**한다. 기존 env/DB 비밀번호를 새로 만들지 않는다. 기존 DB/files의 일관 백업 후 앱을 교체하는 절차와 main-only 배포 gate는 유지한다.

## 기존 서버에서 최초 설정

PR 병합·검증 후 운영 checkout에서 실행한다. PR만 올린 상태에서는 운영 배포하지 않는다. 아래에서 `yoseob`는 소유자 앱 아이디의 예시다. 기존 guest 문서 소유자는 바꾸지 않는다.

1. 기존 private env를 별도로 보호하여 백업하고 `chmod 600`을 유지한다. **기존 서버에 init.py를 다시 실행하지 않는다.** 다음 값을 private env에 추가/수정한다.

```dotenv
HWP_ACCOUNTS_ENABLED=1
HWP_REGISTRATION_ENABLED=1
HWP_REGISTRATION_MAX_USERS=100
HWP_CODEX_USER=yoseob
HWP_CODEX_ALLOWED_USERS=
HWP_REWRITE_PROVIDER=codex
HWP_DOCUMENT_ASSISTANT_PROVIDER=codex
HWP_CODEX_MODEL=
HWP_REWRITE_MAX_ACTIVE=1
HWP_CODEX_DAILY_REQUEST_LIMIT=60
```

`HWP_AUTH_TOKENS`, `POSTGRES_PASSWORD`, `HWP_LEGACY_OWNER`, `HWP_ALLOWED_HOSTS`는 기존 값을 보존한다. 계정 기능은 명시적으로 활성화하기 전까지 기존 토큰 화면을 유지한다. `HWP_GUEST_PASSWORD`는 비워 두고 아래 운영자 명령으로 guest를 만드는 방식을 권장한다. 환경변수로 최초 seed를 제공할 수도 있으나 이미 존재하는 guest의 비밀번호를 재시작 때 덮어쓰지 않는다. seed 후에는 환경변수에서 제거한다.

2. 기존 main 전용 배포 workflow 또는 `scripts/deploy/deploy.sh pull`을 사용한다. compose 직접 실행 시에도 먼저 기존 백업 절차를 따른다. 이후 아래처럼 같은 compose 설정을 사용한다.

```bash
export HWP_ENV_FILE=/opt/hwp-assistant/production.env
export HWP_IMAGE_TAG="$(git rev-parse HEAD)"
dc() { docker compose --env-file "$HWP_ENV_FILE" -f compose.deploy.yaml "$@"; }
dc ps
# 관리자 전용: 암호는 명령줄 인자나 로그에 남지 않고 터미널에서 두 번 입력.
dc exec api python -m app.account_admin create --user guest
# guest 비밀번호는 요청한 체험용 값으로 입력. 이미 있으면 create는 덮어쓰지 않음.
# 변경이 필요한 경우: dc exec api python -m app.account_admin password --user guest
dc exec api python -m app.account_admin create --user yoseob --name '관리자 표시이름' --employee 'TEST-OWNER'
dc exec api codex login --device-auth
dc exec api codex login status
```

device login의 링크·일회용 코드는 해당 서버 터미널과 본인의 브라우저에서만 사용한다. ChatGPT 보안 설정에서 device code login 허용이 필요할 수 있다. 현재 대화의 로그인은 서버 CLI로 자동 전달되지 않는다. 계정·사번은 실제 인사 인증이 아니라 PoC 구분 정보다. 위 예시 사번은 가상 값이다.

3. 실제 HTTPS 화면에서 소유자 계정으로 로그인하고 보고서·문서 대화·문단 후보를 **합성 자료로 각각 1회** 확인한다. 후보 승인 전 원문 불변, 저장 후 새 버전 및 재열기, 서버 재시작 후 세션/CLI 로그인 유지를 확인한다. `configured=true`는 로컬 실행기/로그인 확인이지 모델 응답·남은 한도의 보장이 아니다.

CLI 업데이트는 pin/hash 갱신과 offline/실모델 재검증 후 수행한다. 서버 기동 때 latest를 설치하거나 기존 로그인 파일을 CI에 넣지 않는다.

## 계정·DB 관리

첫 화면은 guest, 내 계정, 새 계정 카드다. “이 브라우저에 아이디 기억”을 선택한 개인 계정은 다음 로그인에서 최대 5개 카드로 나타난다. 선택해도 비밀번호를 다시 요구한다. **전체 회원 목록·이름·사번을 로그인 전 공개하지 않는다.** 공용 기기에서는 기억 기능을 끄거나 기억한 아이디 지우기를 사용한다.

후속 클라우드 점검에서는 기억한 ID와 별도로 **운영자가 지정한 샘플 계정 선택**을 추가했다. 기본 `HWP_SAMPLE_ACCOUNTS=[]`는 카드를 공개하지 않는다. 계정 기능을 켠 환경에서 운영자 CLI로 합성 비밀번호 계정을 먼저 만들고, 공개해도 되는 ID·라벨만 다음 형식으로 명시한다. 아래는 설정 예시이며 운영 서버에 적용한 값이 아니다.

```dotenv
HWP_SAMPLE_ACCOUNTS=[{"user_id":"sample-a","label":"합성 작성자"},{"user_id":"sample-b","label":"합성 검토자"}]
```

최대 8개, ID는 소문자 영문·숫자·`._@-` 3~40자, 라벨은 제어문자 없는 1~40자다. `local`·`guest`, 중복·추가 필드·잘못된 JSON, 계정 기능을 끈 상태의 비어 있지 않은 목록은 시작 시 거부한다. 기본값은 기존 토큰 로그인을 유지한다. 목록에 있어도 실제 활성 member 비밀번호 계정이 없으면 표시하지 않으며 공개 회원가입으로 해당 ID를 선점할 수 없다. 계정·비밀번호 생성/초기화나 AI 허용을 수행하지 않는다. 이름·사번·암호·토큰은 목록에 넣지 않는다.

카드를 누르면 비밀번호 입력 화면만 열린다. 다른 샘플로 전환하면 이전 입력 비밀번호를 지운다. 서로 다른 ID는 기존 문서 ACL로 격리되지만 같은 샘플 ID를 사용하는 체험자들은 그 ID의 문서함을 공유한다. AI 사용은 `HWP_CODEX_USER`/`HWP_CODEX_ALLOWED_USERS`에서 별도로 결정한다. 실제 적용에는 새 코드 이미지와 운영자 설정·기존 백업 절차가 필요하며, 이 변경만으로 운영 계정 기능이 켜지지 않는다.

**확정된 사용자 요구는 체험 계정 2~3개를 선택하고 각 계정의 비밀번호로 로그인하는 방식이다.** 위 예시는 2개이며 세 번째가 필요하면 `sample-c`와 공개 합성 라벨을 같은 목록에 명시한다. 기억한 ID만으로 대체하지 않는다. 운영 적용 승인 후 아래 절차를 사용한다. 이 문서를 작성하면서 실제 운영 계정·암호·ACL·env는 변경하지 않았다.

1. 기존 백업·배포 절차로 새 코드를 적용하고 원하는 샘플 ID를 목록에 예약한다. `HWP_ACCOUNTS_ENABLED=1`을 명시하되 공개 가입은 필요한 경우에만 별도로 결정한다. DB 비밀번호·토큰·기존 AI ACL은 그대로 보존한다. env 변경은 현재 승인된 이미지로 컨테이너 재생성이 필요하며 restart만으로 반영되지 않는다.
2. 공개할 ID와 가상 사번이 기존 회원·토큰·과거 문서 주체와 충돌하지 않는지 **읽기만** 확인한다. 아래는 2개 예시이며 세 번째를 쓸 때는 사전 확인 목록에도 추가한다. 충돌하면 새 ID/가상 사번을 선택한다. 기존 주체에 샘플을 연결하거나 암호 재설정으로 해결하지 않는다.

```sh
docker exec -i hwp-assistant-prod-api-1 python - <<'PY'
import os
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.accounts import UserAccount, historical_identity_in_use
from app.auth import credentials
from app.db import make_engine

engine = None
try:
    engine = make_engine(os.environ['DATABASE_URL'])
    requested = {'sample-a': 'TEST-SAMPLE-A', 'sample-b': 'TEST-SAMPLE-B'}
    token_ids = {user.lower() for user in credentials()}
    with Session(engine) as db:
        conflict = any(user in token_ids or db.get(UserAccount, user) is not None
            or historical_identity_in_use(db, user)
            or db.scalar(select(UserAccount.user_id).where(UserAccount.employee_number == employee).limit(1)) is not None
            for user, employee in requested.items())
    if conflict:
        raise SystemExit('샘플 ID 또는 가상 사번이 사용 중입니다. 새 값을 선택하세요. 변경하지 않았습니다.')
    print('샘플 ID와 가상 사번 사용 가능. 계정은 아직 생성하지 않았습니다.')
except Exception:
    raise SystemExit('샘플 사전 확인 실패. 운영자가 설정을 확인하세요. 변경하지 않았습니다.') from None
finally:
    if engine is not None:
        engine.dispose()
PY
```

3. 각 계정을 대화형 터미널에서 별도로 생성한다. 계정별 비밀번호를 본인이 두 번 입력하며 프런트엔드·명령 인자·env 예시·PR·로그에 기록하지 않는다. `create`는 기존 계정을 덮어쓰지 않는다. 하나가 이미 존재해 실패하면 원인을 확인하고 다른 ID를 사용하며 `password`로 기존 계정을 재설정하지 않는다.

```sh
docker exec -it hwp-assistant-prod-api-1 python -m app.account_admin create \
  --user sample-a --name '체험 작성자' --employee TEST-SAMPLE-A
docker exec -it hwp-assistant-prod-api-1 python -m app.account_admin create \
  --user sample-b --name '체험 검토자' --employee TEST-SAMPLE-B
# 3개로 결정하고 목록/사전 확인에 추가한 경우에만 같은 방식으로 sample-c 생성.
```

4. 공개 상태 응답의 카드가 2~3개인지 확인하고 각각의 비밀번호로 로그인한다. 합성 문서 하나를 첫 계정에서 저장한 뒤 다른 계정에서 보이지 않는지 확인한다. 같은 계정 재로그인 시에는 다시 보여야 한다. AI 실행을 원하면 **별도 운영 판단으로** 해당 ID만 기존 허용목록에 추가하고 현재 이용 ID를 누락하지 않는다. 카드 표시나 회원 생성 자체가 AI 허용을 뜻하지 않는다.

회원가입 입력: 아이디 3~40자, 비밀번호 8~128자, 이름, 사번. 사번은 대문자로 정규화하고 중복을 막는다. 재직 여부 확인이나 SSO가 아니다. 비밀번호는 랜덤 salt가 있는 scrypt(N=32768,r=8,p=3)로만 저장한다. 본인확인 이메일·비밀번호 분실 복구·관리자 웹 화면은 범위 밖이며 운영자 CLI로 재설정한다. 암호 해시도 민감 정보이므로 조회/백업 권한을 제한한다.

```sql
SELECT user_id, name, employee_number, kind, is_active,
       to_timestamp(created_at) AS created_at,
       to_timestamp(last_login_at) AS last_login_at
FROM user_accounts ORDER BY created_at;

SELECT user_id, count(*) AS accepted_requests
FROM codex_usage
WHERE created_at >= extract(epoch FROM date_trunc('day', now() AT TIME ZONE 'UTC'))
GROUP BY user_id;
```

```bash
dc exec api python -m app.account_admin password --user sampleuser
dc exec api python -m app.account_admin disable --user sampleuser
dc exec api python -m app.account_admin enable --user sampleuser
```

비밀번호 재설정은 해당 비밀번호 세션을 무효화한다. 자동화 토큰은 별개이므로 필요한 경우 private env에서도 해당 토큰을 교체/제거한다. 비활성화는 비밀번호·기존 세션·Bearer 토큰 경로에서 모두 차단한다. 로그인 요청은 사용자/IP별 속도와 동시 해싱 2건으로 제한한다. 생산용 API는 기존대로 1 worker다. 여러 worker/서버로 늘릴 때 로그인 제한기도 공유 저장소로 옮겨야 한다.

guest는 **공용 문서함**이므로 다른 guest 이용자가 같은 문서를 볼 수 있다. 기존 guest 소유 문서는 그대로 연결된다. 새 회원은 자기 문서와 명시적으로 공유받은 문서만 본다. 사전에 AI 권한을 지정한 아이디는 공개 회원가입으로 선점할 수 없고 운영자가 만든다. 가입 결과 응답이 유실되면 같은 아이디로 로그인하며, 기존 암호를 덮어쓰거나 자동 중복 POST하지 않는다. 로그아웃/계정 전환 시 해당 탭의 이전 보고서 복구 입력을 지운다.

## AI 사용량·예외 처리

홈서버 production 기본 동시 생성은 **1건**이다. 보고서·문단 수정·문서 대화는 기존 공통 실행 슬롯을 사용한다. 요청이 많으면 429와 재시도 안내를 반환하며 숨은 무한 대기열을 만들지 않는다. 앱 자체 일일 cap 기본 60건은 PostgreSQL에 영속화하며 **UTC 자정(한국 09:00)** 갱신 기준이다. 이 숫자는 운영자가 정한 앱 상한이지 구독이 보장하는 생성량이 아니다. 접수 뒤 실패/결과 미확인 요청도 소모하며 같은 작업 ID 결과 재확인은 새 호출로 세지 않는다. 재시작으로 한도를 초기화하지 않는다.

로그아웃·사용량 소진·네트워크 실패·출력 계약 오류는 문서 편집/저장과 분리한다. 로그인 상태 확인은 최대 5초, 15초 캐시이며 모델을 호출하지 않는다. 실제 응답 성공은 각 작업 완료 때 별도로 확인한다. 사내/로컬 HTTP 모델의 기존 선택 기능은 별도 연결을 설정한 경우에만 사용하며 Codex가 그것을 자동 대체하지 않는다.

## 로그인 시 WASM 미리 받기·사전 컴파일

초기 #31 구현은 아래 자원의 사전 다운로드만 수행했다. 후속 신뢰성 점검 변경은 검증한 WASM 바이트의 사전 컴파일을 추가하며 다운로드·컴파일·실제 문서 준비를 구분한다. 이 절은 코드 동작을 설명하며 모든 실제 브라우저에서 성능 개선을 검증했다는 뜻은 아니다.

로그인 UI를 먼저 그리고 약 100ms 뒤 `rhwp/build-info.json`의 현재 빌드 영수증을 읽는다. 같은 origin·같은 `/hwp-assistant/rhwp/assets/...` 해시 URL의 WASM/JS/CSS만 낮은 우선순위, 동시 2개로 내려받고 SRI 해시를 검사한다. 편집기가 사용하는 정확한 URL이므로 HTTP 캐시를 재사용한다. 다운로드를 마친 뒤 검증한 WASM 바이트에 `WebAssembly.compile`을 호출한다. WASM 인스턴스 생성·JavaScript 실행·숨은 편집기·사용자 문서 로드·모델 호출은 하지 않는다. 새 Service Worker나 사용자 문서 캐시도 추가하지 않는다. 로그인·회원가입은 이 준비의 완료를 기다리지 않는다.

브라우저가 제공하는 데이터 절약, 2G, offline, 비활성 상태에서는 자동 미리 받기를 보류한다. 온라인/활성화 시 제한적으로 다시 시도하고 사용자가 수동으로 준비할 수도 있다. 다운로드와 컴파일의 대기를 합쳐 45초로 제한한다. 한 파일 32MiB/합계 64MiB/최대 24개로 제한하며 컴파일용 WASM 바이트만 보관한다. 실패·잘못된 해시·배포 도중 manifest/asset 불일치·시간 초과에서는 일반 편집기 로딩으로 돌아간다. 브라우저의 네이티브 컴파일 자체는 취소할 수 없으므로 시간 초과 후 늦은 완료 결과를 무시하고 로그인 화면을 완료 상태로 바꾸지 않는다.

화면의 **다운로드 완료**와 **WASM 컴파일 완료**는 각각 해당 단계의 완료만 뜻한다. 엔진 초기화·글꼴 준비·문서 배치는 실제 문서를 열 때 수행한다. 사전 컴파일한 결과는 브라우저 코드 캐시의 재사용을 기대하는 최적화이며 iframe의 엔진 초기화가 해당 결과를 반드시 재사용한다고 보장하지 않는다.

Chrome/Edge/Firefox/Safari의 캐시 정책·메모리·비공개 모드·네트워크 API 지원이 다르다. Safari 등은 데이터 절약 API를 제공하지 않을 수 있다. 캐시 삭제/퇴출, 개발자 도구의 Disable cache, 즉시 로그인·즉시 문서 열기, 오래된 탭, 저사양 휴대폰에서는 대기 시간이 남을 수 있다. SRI 처리와 컴파일 준비에도 임시 메모리가 필요할 수 있다. **10초→0초를 모든 브라우저에서 보장하지 않는다.** 자동 시험은 실제 캐시 사용/무결성/문서 저장과 UI 모의 시험을 구분한다.

## 검증과 운영 승인

일반 HWP deployment의 Python/TypeScript/UI/TLS 검사는 유지한다. 새 `PoC accounts and Codex runtime` PR workflow는 GitHub-hosted 일회용 PostgreSQL/API/web만 실행한다. API binary·exec flags·app-server initialize/account-read는 `--network none`에서 검사하고 실제 모델을 부르지 않는다. 웹은 기존 native-core cache를 사용하며 원본 WASM 빌드 입력은 바꾸지 않는다. 실제 HTTPS 계정 생성/guest 저장/문서 격리/캐시 재사용과 API 재시작을 확인한다. 인증 파일·DB/env·브라우저 trace는 artifact에 올리지 않는다.

PR code를 self-hosted 운영 runner에 실행하지 않고 이미지 게시·main 병합·운영 배포를 하지 않는다. 홈서버 device 인증과 실제 모델 3개 경로, Safari/Firefox 실기기, 한컴 재개방 검수는 별도로 수행해야 한다. 시험 결과는 PR의 실제 Actions 및 최신 검증 기록을 우선한다.
