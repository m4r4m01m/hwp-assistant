# 매터모스트 문서 검색·초안 봇

## 범위와 구현 상태

2026-10-28, 기준 main [`4b20b5f293ee81f158fe25d14a48955d47948373`](https://github.com/m4r4m01m/hwp-assistant/commit/4b20b5f293ee81f158fe25d14a48955d47948373)에서 분리한 `codex/mattermost-document-bot-2026-10-28`의 구현 안내다. 제품 source는 `f5941cef11447bbced1ab3221a002054298bbbbf`이며 로컬 시험 결과와 남은 실패는 [PROJECT_CONTEXT](PROJECT_CONTEXT.md)의 같은 날짜 봇 항목에 기록한다. 해당 PR의 CI는 별도 근거다. 실제 Mattermost 서버·내부 임베딩 모델·Codex 생성·운영 배포의 성공을 이 문서로 주장하지 않는다.

2026-10-30 보완은 main `50babefdf739a680eb16c984693e5e7b602d6088`에서 서버용 입력창 콜백 주소를 분리한다. 아래 `HWP_MATTERMOST_CALLBACK_URL`과 Docker 내부 통신 안내는 이 보완을 포함한 새 API 이미지가 게시·배포된 뒤 사용할 수 있다. 기존 이미지는 이 환경변수를 읽지 않으며 입력창 제출 주소를 공개 웹 주소로 구성한다. 이 절은 소스·설정 안내이며 실제 운영 반영 기록은 아니다.

봇은 기존 **API 프로세스 안의 명령 수신 경로와 백그라운드 처리기**로 동작한다. 별도 봇 컨테이너나 WebSocket 연결을 추가하지 않는다. `db`, `api`, `web` 구성과 API 1 worker 원칙, `/hwp-assistant/`, 기존 외부 볼륨을 유지한다. 웹은 계정 연결 확인, 저장 문서 열람, 초안 검토를 맡는다.

입력은 **사용자 지정 Slash Command `/hwp`와 대화상자 제출**, 출력은 **봇 계정의 REST API를 통한 개인 메시지**다. Outgoing Webhook을 등록할 필요가 없다. 기존 가입 승인 알림의 Incoming Webhook은 별도 기능이며 설정을 공유하지 않는다. Slash Command의 등록 형식은 [Mattermost 공식 안내](https://docs.mattermost.com/developers/integrate/slash-commands/custom)를 따른다.

## 사용자 흐름

| 명령 | 동작 |
| --- | --- |
| `/hwp 연결` | 5분 동안 유효한 연결 링크를 요청자에게만 표시한다. 웹에서 승인된 개인 계정으로 로그인하고 **이 계정으로 연결**을 눌러 확정한다. |
| `/hwp 검색 장애 대응 절차` | 제목과 저장된 본문에서 관련 문서를 찾고 최대 5개 결과의 제목·일부 내용·열람 링크를 개인 메시지로 보낸다. 검색어는 2~200자의 한 줄이다. |
| `/hwp 초안` | 작성 내용(5~3,000자), 별첨 1·2·3 양식, 공개자료 조회 여부를 선택하는 입력창을 연다. |
| `/hwp 초안 다음 달 점검 계획을 작성해 주세요` | 기본 양식인 별첨 1로 요청한다. 공개자료 조회는 기본적으로 끈다. 작성 내용은 5~6,000자다. |
| `/hwp 내요청` | 현재 연결에서 접수한 최근 요청 최대 8개와 초안 작업 링크를 요청자에게만 표시한다. |
| `/hwp 취소 요청번호` | 본인 대기 요청 또는 실행 중인 초안을 취소한다. 이미 생성한 초안과 저장 문서는 보존한다. |
| `/hwp 연결해제` | 계정 연결을 해제한다. 웹의 매터모스트 연결 화면에서도 해제할 수 있다. |
| `/hwp 도움말` | 사용법을 표시한다. |

입력창은 [Mattermost의 textarea 안내](https://docs.mattermost.com/developers/integrate/plugins/interactive-dialogs#textarea-elements)에 맞춰 3,000자로 제한한다. 더 긴 내용은 `/hwp 초안 내용`으로 최대 6,000자까지 요청한다.

사용자 ID나 이메일 문자열만으로 HWP 계정을 추정하지 않는다. 승인·활성 상태인 개인 `member` 계정만 연결할 수 있고 `guest`와 로컬 작업 계정은 제외한다. 하나의 Mattermost 사용자와 하나의 HWP 계정을 연결하며, 다른 계정으로 바꾸려면 기존 연결을 해제하고 새 링크를 요청한다. 다른 사람이 전달한 연결 링크를 승인하지 않는다.

연결 토큰은 URL fragment로 전달하고 웹이 로그인 화면을 띄우기 전에 주소에서 제거한다. DB에는 토큰 해시와 만료·대상 식별자만 기록한다. 연결 링크를 열기만 해서는 연결되지 않는다. 연결 해제 후에는 이전 토큰이나 입력창으로 새 연결의 권한을 사용할 수 없다.

검색 결과는 연결된 HWP 계정의 현재 문서 권한을 따른다. 서비스가 공용 문서함으로 설정돼 있으면 기존 공용 문서함 정책도 그대로 적용된다. 결과 본문은 채널에 게시하지 않고 봇과 요청자의 개인 메시지로 전달한다. 문서명·발췌문은 멘션과 임의 링크로 해석되지 않도록 처리한다. 이미 전달한 개인 메시지는 연결 해제로 회수되지 않는다.

검색 링크는 `?document=<문서 UUID>&version=<저장 버전 UUID>`로 **읽기 전용 저장본**을 연다. 링크 자체가 접근 권한을 부여하지 않으며 웹 로그인 후에도 서버 권한을 검사한다. 사용자가 편집을 선택할 때 최신 저장본과 권한을 다시 확인하고 기존 편집권 획득 절차를 사용한다. 링크를 여는 것만으로 편집권을 얻거나 열린 편집 버퍼를 덮어쓰지 않는다.

초안은 기존 보고서 작업으로 생성하고 `?report=<작업 UUID>` 링크에서 본인만 확인한다. 검색 결과를 자동으로 모아 초안의 근거 자료로 주입하는 기능은 포함하지 않는다. 사용자가 입력한 내용과 선택한 공개자료 조회 설정으로 생성하며 기존 Codex 이용 권한·일일 한도·동시 실행 제한을 적용한다. 봇 연결만으로 AI 권한을 추가하지 않는다.

새 초안은 현재 문서 표준의 revision 3과 별첨 구성을 고정한다. 생성 후 **내용 검토 → 서식 미리보기 → 명시적인 초안 확정·서버 저장** 흐름과 보완본의 별도 작업 생성은 유지한다. 봇은 문서를 자동 저장하거나 기존 문서·과거 버전·수동 편집본을 수정하지 않는다. 자세한 계약은 [문서 작성 표준](document-writing-standard.md), [보고서 내용 검토](report-review-workflow-2026-10-20.md)를 따른다.

## 서버와 Mattermost 설정

계정 기능 `HWP_ACCOUNTS_ENABLED=1`과 승인된 개인 계정이 전제다. 기존 계정·Codex 정책을 임의로 넓히지 않는다. 아래 값은 서버의 private 환경 설정에서 관리하고 실제 토큰이나 전체 환경 설정을 채팅·PR·로그에 출력하지 않는다.

| 환경변수 | 기본값 | 용도 |
| --- | --- | --- |
| `HWP_MATTERMOST_BOT_ENABLED` | `0` | `1`일 때 봇을 활성화한다. 계정 기능과 함께 켜면 API가 봇 처리기와 검색 자료 갱신을 시작한다. |
| `HWP_MATTERMOST_URL` | 빈 값 | API가 접속할 Mattermost 기준 주소. 예: `https://chat.example.invalid/mattermost/`. 내부 통신에서는 공개 SiteURL과 다른 주소를 지정할 수 있으며 `/company/mattermost/` 같은 실제 하위 경로는 유지한다. `/hooks/...` 또는 `/api/v4/` 주소를 넣지 않는다. |
| `HWP_MATTERMOST_PUBLIC_APP_URL` | 빈 값 | 웹에서 열 수 있는 HWP 서비스 기준 주소. 예: `https://docs.example.invalid/hwp-assistant/`. |
| `HWP_MATTERMOST_CALLBACK_URL` | 빈 값 | 선택 사항. Mattermost 서버가 입력창 제출을 보낼 전체 URL. 비어 있으면 `HWP_MATTERMOST_PUBLIC_APP_URL`에 `api/integrations/mattermost/dialog`를 붙인다. 직접 API로 연결하는 예: `http://api:8000/api/integrations/mattermost/dialog`. 경로는 `/api/integrations/mattermost/dialog`로 끝나야 하며 사용자 링크에는 사용하지 않는다. |
| `HWP_MATTERMOST_BOT_TOKEN` | 빈 값 | 실제 **봇 계정**의 액세스 토큰. 일반 사용자 토큰으로 개인 메시지를 보내지 않는다. |
| `HWP_MATTERMOST_COMMAND_TOKEN` | 빈 값 | 등록한 `/hwp` Slash Command의 검증 토큰. 봇 토큰과 별개다. |
| `HWP_MATTERMOST_TEAM_ID` | 빈 값 | 요청을 허용할 Mattermost 팀 ID 하나. 표시 이름이나 URL의 팀 이름이 아니다. |
| `HWP_MATTERMOST_BOT_ALLOW_HTTP` | `0` | 명시적으로 신뢰하는 내부 연결에 HTTP가 필요한 경우에만 `1`. 기본적으로 Mattermost·공개 웹·별도 콜백 URL 모두 HTTPS를 요구한다. 공개 사용자 링크에는 HTTPS 주소를 유지한다. |
| `HWP_SEARCH_ENABLED` | `0` | 봇을 켜지 않고도 검색 자료의 백그라운드 갱신을 시작할 때 `1`. 봇 활성화 시에는 별도로 켤 필요가 없다. |
| `HWP_SEARCH_EMBEDDING_URL` | 빈 값 | 선택 사항. 내부 임베딩 서버의 전체 `/embeddings` endpoint. 예: `https://embedding.internal/v1/embeddings`. |
| `HWP_SEARCH_EMBEDDING_MODEL` | 빈 값 | 위 내부 서버가 제공하는 임베딩 모델 이름. URL과 모델이 모두 있어야 의미 검색을 준비한다. |
| `HWP_SEARCH_EMBEDDING_ALLOW_HTTP` | `0` | 내부 임베딩 서버에 HTTP를 명시적으로 허용할 때만 `1`. 봇 HTTP 설정과 별개다. |
| `HWP_SEARCH_MIN_SIMILARITY` | `0.35` | 의미 검색의 최소 유사도. `0`~`1`의 유한 수이며 잘못된 값은 기본값을 사용한다. |

현재 [운영 Compose](../compose.deploy.yaml)는 위 설정을 API에 전달하며 `HWP_SEARCH_MIN_SIMILARITY`의 기본값은 `0.35`다. `HWP_MATTERMOST_WEBHOOK_URL`·`HWP_MATTERMOST_ALLOW_HTTP`는 [가입 승인 알림](account-approval-audit-2026-10-26.md)의 설정이므로 위 봇 설정으로 대체하지 않는다.

봇·명령 토큰과 팀 ID는 Mattermost의 소문자 영숫자 26자리 형식을 검사한다. URL의 사용자 정보·쿼리·fragment·모호한 경로는 거부하고 리다이렉트를 따라가지 않는다. 설정이 빠졌거나 잘못되면 봇 요청이 실패하며, 기본 비활성 상태에서는 일반 서비스의 기동에 봇 설정이 필요하지 않다.

등록 순서는 다음과 같다.

1. Mattermost에서 이 서비스용 봇 계정을 만들고 토큰을 private 설정에 보관한다. 봇 사용과 개인 메시지가 해당 서버 정책에서 허용돼 있어야 한다. [공식 봇 계정 안내](https://docs.mattermost.com/developers/integrate/reference/bot-accounts)를 참고한다.
2. 허용할 팀에 사용자 지정 Slash Command를 등록한다. Trigger Word는 `hwp`, 요청 방법은 **POST**, Request URL은 아래 command URL이다. 등록 후 발급된 명령 토큰을 `HWP_MATTERMOST_COMMAND_TOKEN`에 넣는다.
3. HWP 공개 웹 주소와 API가 접속할 Mattermost 주소·팀 ID·봇 토큰을 설정한다. 사용자 브라우저가 HWP 공개 웹에, Mattermost 서버가 명령·입력창 제출 주소에, API가 Mattermost REST API에 각각 접근할 수 있어야 한다.
4. 공개 웹 프록시를 거치는 명령·입력창 콜백은 다음처럼 `/hwp-assistant/`를 포함한다. 입력창 URL은 서버 설정으로 고정하여 HWP가 전송하므로 별도 Outgoing Webhook으로 등록하지 않는다. 같은 Docker 호스트에서 직접 API를 호출하는 구성은 아래 내부 통신 절을 따른다.

| 용도 | 공개 URL 예시 |
| --- | --- |
| Slash Command 요청 | `https://docs.example.invalid/hwp-assistant/api/integrations/mattermost/command` |
| 입력창 제출 | `https://docs.example.invalid/hwp-assistant/api/integrations/mattermost/dialog` |

웹 프록시는 기존처럼 `/hwp-assistant/`를 처리하고 API 내부에는 `/api/integrations/mattermost/...`로 전달한다. 정확히 이 두 POST 경로만 자체 연동 인증을 사용한다. 명령은 등록 토큰과 팀·명령을 검증하고, 입력창은 짧은 유효기간의 상태 토큰과 사용자·채널·팀·연결 세대를 검증한다. 다른 API와 웹 연결 확정은 기존 앱 인증을 유지한다. 명령 본문의 `response_url`은 호출하지 않는다.

이 안내는 운영 변경을 실행하라는 지시가 아니다. 운영 반영은 검토된 소스와 해당 커밋의 검증 후 [배포 절차](deployment-runbook.md)를 따른다. `compose.deploy.yaml`은 단독 사용하고 기존 private env·볼륨·실행 이미지를 보존한다. 환경변수 적용에는 컨테이너 재생성이 필요하며 단순 재시작으로 바뀌지 않는다. 기존 서버에서 최초 설치 초기화를 다시 실행하지 않는다.

## 같은 Docker 호스트의 내부 통신

2026-10-30 사용자 제공 네트워크 조회에서 Mattermost와 HWP API는 이미 `hwp-mattermost-hook`에 함께 연결돼 있었다. 이 망에서 Mattermost 별칭은 `mattermost`·`mattermost-webhook`, HWP API 별칭은 `api`였다. 이어 사용자가 HWP 컨테이너의 Python으로 `http://mattermost:8065/mattermost/api/v4/system/ping`을 조회해 HTTP 200·`status: OK`를 확인했다. 이는 HWP → Mattermost 내부 상태 조회의 근거이며 실제 명령·입력창·개인 메시지 성공과 구분한다. 다음 안내는 이 관측과 같은 배치의 예다. 다른 서버에서는 컨테이너의 실제 네트워크·별칭을 먼저 확인한다. 같은 사용자 정의 bridge의 컨테이너는 이름·별칭과 컨테이너 포트로 통신하므로 새 공개 포트를 열거나 고정 IP를 지정할 필요가 없다. [Docker 네트워킹](https://docs.docker.com/compose/how-tos/networking/)을 참고한다.

| 방향 | 주소 |
| --- | --- |
| Mattermost → HWP Slash Command | `http://api:8000/api/integrations/mattermost/command` |
| Mattermost → HWP 입력창 제출 | `http://api:8000/api/integrations/mattermost/dialog` |
| HWP → Mattermost REST 기준 주소 | `http://mattermost:8065/mattermost/` |
| 사용자 브라우저 → HWP | 기존 공개 HTTPS 주소와 `/hwp-assistant/` 유지 |

직접 API 주소에는 웹 프록시용 `/hwp-assistant/` 접두사를 붙이지 않는다. Mattermost가 실제로 `/mattermost` 하위 경로를 사용하는 이 예에서는 내부 REST 주소에도 그 경로를 유지한다. `MM_SERVICESETTINGS_SITEURL`은 사용자가 접속하는 공개 HTTPS 주소를 유지한다.

사용자가 확인한 서버 파일 위치와 변경 항목은 다음과 같다. 실제 환경 파일이나 토큰은 저장소에 복사하지 않는다.

| 서버 파일·화면 | 변경 항목 |
| --- | --- |
| `/opt/hwp-assistant/production.env` | 아래 HWP 환경변수. 기존 `HWP_ALLOWED_HOSTS` 목록에는 `api`를 쉼표로 구분해 추가한다. |
| `/opt/hwp-assistant/compose.deploy.yaml` | 새 이미지와 짝을 이루는 이 보완의 Compose를 사용한다. API에 새 콜백 환경변수를 전달하며 기존 `mattermost_hook` 네트워크를 유지한다. 개발 Compose와 병합하지 않는다. |
| `/home/pseudowasabi/apps/mattermost/docker-compose.yml` | `mattermost` 서비스의 기존 `MM_SERVICESETTINGS_ALLOWEDUNTRUSTEDINTERNALCONNECTIONS`에 `api`를 공백으로 구분해 추가한다. 기존 Mattermost DB 네트워크와 `hwp-mattermost-hook` 연결을 유지한다. |
| Mattermost → Integrations → Slash Commands → `hwp` | Request Method는 `POST`, Request URL은 위 내부 command 주소로 바꾼다. 기존 명령 검증 토큰은 유지한다. |

HWP private 환경 파일에서 사용할 값은 다음과 같다. 공개 웹 주소는 해당 서버의 기존 주소를 유지한다.

```dotenv
HWP_MATTERMOST_URL=http://mattermost:8065/mattermost/
HWP_MATTERMOST_PUBLIC_APP_URL=https://docs.example.invalid/hwp-assistant/
HWP_MATTERMOST_CALLBACK_URL=http://api:8000/api/integrations/mattermost/dialog
HWP_MATTERMOST_BOT_ALLOW_HTTP=1
```

공개 웹 주소를 내부 주소로 바꾸면 사용자에게 전달하는 계정 연결·문서·초안 링크도 내부 주소가 된다. 서버용 콜백만 `HWP_MATTERMOST_CALLBACK_URL`로 분리한다. 이 설정으로 입력창 제출 주소가 달라져도 상태 토큰·만료·사용자·팀·채널·연결 세대 검증은 동일하게 적용된다. 내부 HTTP는 신뢰하는 같은 호스트의 bridge에 한정하고 기존 봇·명령 토큰과 Host 검증을 유지한다.

현재 구현은 `HWP_MATTERMOST_URL`의 정규화된 주소 해시로 Mattermost 인스턴스를 구분한다. 이 값을 공개 주소에서 내부 주소로 바꾸면 같은 Mattermost 서버라도 새 인스턴스로 처리하므로 사용자는 `/hwp 연결`을 다시 진행해야 한다. 기존 HWP 계정·문서는 유지되며 이전 연결과 요청을 새 주소의 연결로 자동 재지정하지 않는다.

적용은 다음 순서로 검증한다.

1. 실제 네트워크·별칭과 `http://mattermost:8065/mattermost/api/v4/system/ping`의 API 컨테이너 응답을 확인한다. Mattermost 이미지에 `curl`·`printenv`·셸이 있다고 가정하지 않는다.
2. 보완을 포함한 새 API 이미지와 짝이 맞는 운영 Compose를 [게시 이미지 배포 절차](deploy-published-images.md)에 따라 반영한다. 기존 private 환경, 프로젝트·Compose 옵션, 외부 볼륨과 이전 이미지를 보존하는 절차를 따른다. Mattermost 환경변수만 변경하는 경우에는 현재 이미지 digest를 고정하고 해당 서비스만 재생성한다.
3. `/hwp 도움말`, `/hwp 연결`, 검색 결과 개인 메시지, `/hwp 초안` 입력창 열기·제출을 각각 확인한다. 입력창 없이 `/hwp 초안 내용`만 성공한 결과를 입력창 콜백 성공으로 계산하지 않는다.
4. 웹에서 연결·문서·초안 링크가 공개 HTTPS 주소인지, 요청자의 권한으로 열리는지 확인한다. 생성·서식·저장은 기존 검수 단계대로 확인한다.

사용자 제공 장애 로그에서 초기 `address forbidden`은 Mattermost의 내부 주소 제한에 의한 차단이었고, 허용 목록 변경 뒤에는 Tailscale IP의 `:443` 연결 시간 초과로 바뀌었다. 후자는 컨테이너에서 해당 HTTPS 주소까지의 경로가 연결되지 않았다는 근거이며 Tailscale ACL이 원인이라고 단정하지 않는다. Docker→호스트 경로·방화벽·리스너 등은 별도 진단 대상이다.

Mattermost **Test Live URL**은 서버가 `<SiteURL>/api/v4/system/ping`으로 자기 공개 주소를 요청하는 검사다. 봇의 내부 통신 전환이 이 자기 접속 경로까지 바꾸지는 않으므로 버튼은 계속 실패할 수 있다. 공개 SiteURL은 유지하고 이 경로를 별도로 진단한다. [Mattermost 11.11 구현](https://github.com/mattermost/mattermost/blob/v11.11.0/server/channels/app/admin.go#L159-L172). Calls의 UDP 시간 초과와 SMTP 연결 거부도 `/hwp` 명령의 TCP 연결 실패와 구분한다.

## 검색 방식과 제한

검색 자료는 현재 저장된 HWPX의 제목·문단·표 내용을 읽어 DB에 만드는 파생 자료다. 원본·과거 버전은 변경하지 않는다. 문서 저장 트랜잭션 안에서 추출이나 모델 호출을 하지 않고 API의 백그라운드 작업이 갱신한다. 저장하지 않은 편집 버퍼, 생성 중인 초안, AI 대화, 작성 메모는 검색 대상에 넣지 않는다.

임베딩이 없거나 연결할 수 없으면 **제목·본문 단어 검색**을 사용하고 결과에 그 상태를 안내한다. 내부 임베딩 서버가 구성되고 해당 문서의 벡터가 준비돼 있으면 단어 순위와 의미 유사도 순위를 합친다. 따라서 표현이 완전히 일치하지 않는 관련 문서를 찾을 수 있지만, 의미 검색 품질과 범위는 실제 내부 모델로 별도 확인해야 한다. 단어 검색만 동작하는 상태를 의미 검색 성공으로 표현하지 않는다.

내부 임베딩 요청은 `model`, `input`, `encoding_format: "float"`를 사용하는 `/embeddings` 형식이며 응답의 `data[].index`와 `embedding`을 검증한다. 현재 bearer API key 옵션은 없다. 주소를 한 번 해석하여 loopback 또는 허용된 사설 IP임을 확인한 뒤 해당 IP로 연결하고, HTTPS 인증서 검사·원래 호스트/SNI를 유지한다. 공인 IP·메타데이터용 link-local 주소·리다이렉트·환경 프록시를 사용하지 않는다. 구성한 내부 서버에는 저장 문서의 제목·본문 조각과 검색어가 전송될 수 있다. 유료 또는 외부 임베딩 제공자로 자동 전환하지 않는다.

현재 처리 한도는 다음과 같다.

- HWPX 파일 10 MiB, 검증한 XML 노드 100,000개, 문서당 최대 64개 조각과 조각당 700자. 긴 문서에는 일부 내용만 색인될 수 있다. 이미지의 OCR은 하지 않는다.
- 기본 갱신은 한 번에 문서 2개, 반복 사이 약 5초 간격이다. 처음 켠 직후나 문서가 바뀐 직후에는 본문 자료가 준비되지 않을 수 있다.
- 단어 후보와 의미 후보를 각각 최대 200개까지 처리한다. 봇 응답은 최대 5개이며 전체 문서함을 빠짐없이 검색한 총수나 전역 최상위 결과를 보장하지 않는다. 범위에 도달하면 검색어 구체화를 안내한다.
- 문서 ID·현재 버전·해시·제목을 맞춰 오래된 본문을 제외한다. 검색 후와 결과 전달 단계에서 접근 권한·휴지통·현재 버전을 다시 확인한다.
- 임베딩은 한 번에 최대 64개 입력, 입력당 1,200자, 총 64,000자, 최대 2,048차원으로 제한한다. 오류·응답 초과·내부 연결 실패 시 문서 원본은 보존하고 단어 검색으로 돌아간다.

파생 본문과 벡터도 회사 문서에서 나온 private 데이터다. 일반 DB 접근·백업 보호 정책을 적용한다. 내부 서버의 실모델 품질이나 회사 격리망 연결은 이 구현의 모의 시험과 구분하여 확인한다.

## 요청 보존·취소·재시작

연결, 만료되는 연결 요청, 봇 요청 영수증, 검색 자료는 추가 테이블에 보존한다. 명령은 작업 식별자와 입력 해시를 고정한 뒤 접수하고 실제 검색·생성을 처리한다. 같은 식별자의 같은 입력은 기존 요청을 반환하고 다른 입력은 거부한다. 대기·처리·생성 중인 요청은 사용자당 최대 5개, 명령은 사용자당 분당 20개와 서버 전체 분당 120개로 제한한다. 속도 제한과 처리기는 API 1 worker를 전제로 한다.

재시작 후 접수한 요청은 DB 상태를 확인해 이어서 처리한다. 이미 만들어진 보고서 작업은 같은 UUID로 확인하고 모델 생성을 자동으로 다시 시작하지 않는다. 중단되어 남은 실행 상태는 제한 시간 뒤 실패로 정리한다. 연결 해제·계정 비활성화·새 연결 세대가 확인되면 이전 요청의 처리나 개인 결과 전달을 중단한다. 이미 완성한 초안은 웹의 본인 작업에 남는다.

정상 종료 때는 새 봇 요청 처리를 시작하지 않고 진행 중인 전달·검색 자료 갱신이 끝난 뒤 DB 연결을 정리한다. 강제 종료와 전송 중 연결 유실은 아래의 전달 여부 불명 처리 대상이다.

개인 메시지를 Mattermost가 받았지만 응답 또는 로컬 영수증을 기록하기 전에 연결·프로세스가 끊길 수 있다. 이 경우 **전달 여부 불명/알림 실패**로 남기고 같은 메시지를 자동으로 재전송하지 않는다. REST 메시지 작성의 exactly-once를 보장하지 않는다. `/hwp 내요청`과 웹의 본인 작업에서 실제 초안 상태를 먼저 확인한다. 결과 알림 실패를 이유로 모델 생성도 자동으로 반복하지 않는다.

## 구현 근거와 남은 검증

- 수신·계정 연결: [mattermost.py](../backend/app/mattermost.py), [mattermost_store.py](../backend/app/mattermost_store.py)
- REST 전송·요청 처리: [mattermost_client.py](../backend/app/mattermost_client.py), [mattermost_worker.py](../backend/app/mattermost_worker.py)
- 문서 검색·내부 임베딩: [document_search.py](../backend/app/document_search.py), [document_search_embeddings.py](../backend/app/document_search_embeddings.py)
- 기존 보고서 접수 재사용: [reports.py](../backend/app/reports.py)
- 링크·연결 확인·저장본 열람: [external-entry.ts](../frontend/src/external-entry.ts), [external-entry-view.tsx](../frontend/src/external-entry-view.tsx)

합성 계정·문서, 모의 HTTP/모델, 브라우저 UI와 exact-head CI의 검증 결과는 [PROJECT_CONTEXT](PROJECT_CONTEXT.md)에 기록한다. 실제 Mattermost의 `/hwp` 등록·입력창·개인 메시지, 실제 내부 임베딩 모델의 표현 차이 검색, 실제 Codex 생성, 봇을 활성화한 PostgreSQL·재시작·운영 HTTPS 경로, 실제 엔진의 읽기 전용 링크·서식 검토·명시적 저장 검수는 별도 증거가 필요하다. 기존 계정·공동편집의 PostgreSQL 통합 CI 성공은 이 봇 실연동이나 운영 배포 완료를 뜻하지 않는다.
