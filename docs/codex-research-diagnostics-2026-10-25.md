# Codex 공개자료 검색 최소 진단 — 2026-10-25 KST

대상은 고정 CLI **0.153.0**의 홈서버 개인망 PoC다. 이 문서는 안전한 다음 진단 절차이며 운영 검색 복구 완료 보고가 아니다. 제품 코드 `8a8c9afae4bd809362b8f2eb87507a10e44e1ad3`은 검색 실행 command/도구 설정을 바꾸지 않았다.

사용자 제공 실운영 근거: main `295b6e1`에서 research=false 합성 보고서 1건이 `gpt-6-astra`로 19.347초 만에 ready·completed=true가 됐고, 내용 검토 후 실제 fixed rhwp 2쪽 미리보기까지 성공했다. approval=null·서버 저장 미실행이다. 이는 기본 모델·JSON·서식 경로의 성공이며 research=true의 성공이 아니다. 클라우드가 직접 실행한 근거로 계산하지 않는다.

## 1. 기존 설치와 로그인만 확인

```sh
docker exec hwp-assistant-prod-api-1 codex --version
docker exec hwp-assistant-prod-api-1 codex login status
```

버전과 `Logged in using ChatGPT` 여부만 확인한다. 이미 확인된 정상 로그인을 이유 없이 초기화하지 않는다. `auth.json`, private env, 전체 docker inspect/확장 compose, CLI debug 원문, 브라우저 쿠키·토큰·일회용 인증코드는 출력/공유하지 않는다. `chat/review llm_not_configured`나 `/ai/models` 503을 이 검색 경로의 실패 원인으로 해석하지 않는다.

## 2. 모델 호출 없이 설정과 제공자 메타데이터를 확인

아래 명령은 기존 보고서의 `stream_command(research=True)`와 동일한 CLI override로 **새 임시 app-server**를 연다. initialize/account-read/config-read/provider-capability-read만 호출하며 thread/turn을 만들거나 검색·모델 추론을 요청하지 않는다. 계정 유형·설정·공식 boolean 필드만 출력한다. 자격정보와 model/provider 오류 원문은 출력하지 않는다. 실제 운영에서 실행한 명령이 아니라 다음 점검용 절차다.

```sh
docker exec -i hwp-assistant-prod-api-1 python - <<'PY'
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from app.codex_runner import capture_settings
from app.report_rpc import ReportRpc
from app.report_stream import stream_command

try:
    settings = capture_settings()
    if not settings.executable:
        raise RuntimeError('unavailable')
    with TemporaryDirectory(prefix='hwp-search-check-') as directory:
        instructions = Path(directory) / 'instructions.txt'
        instructions.write_text('Configuration diagnostics only. No inference requested.', encoding='utf-8')
        with ReportRpc(stream_command(settings, instructions, True),
                       environment=dict(settings.environment), cwd=directory, timeout=15) as rpc:
            serial = 0
            def call(method, params):
                global serial
                serial += 1
                request_id = serial
                rpc.send({'id': request_id, 'method': method, 'params': params})
                for _ in range(256):
                    event = rpc.receive()
                    if 'method' in event and 'id' in event:
                        raise RuntimeError('unexpected_request')
                    if event.get('id') == request_id:
                        if 'error' in event:
                            raise RuntimeError('protocol')
                        return event['result']
                raise RuntimeError('event_limit')
            call('initialize', {'clientInfo': {'name': 'hwp_search_check', 'version': '1'}})
            rpc.send({'method': 'initialized'})
            account = call('account/read', {'refreshToken': False}).get('account') or {}
            config = call('config/read', {'includeLayers': False})['config']
            capabilities = call('modelProvider/capabilities/read', {})
            print(json.dumps({
                'check': 'completed_without_inference',
                'chatgpt_account': account.get('type') == 'chatgpt',
                'web_search_live': config.get('web_search') == 'live',
                'provider_advertises_web_search': capabilities.get('webSearch') if type(capabilities.get('webSearch')) is bool else None,
                'default_provider_route': not config.get('openai_base_url')
                    and config.get('chatgpt_base_url') in (None, 'https://chatgpt.com/backend-api/', 'https://chatgpt.com/backend-api')
                    and not (config.get('model_providers') or {}).get('openai'),
            }))
except Exception:
    print(json.dumps({'check': 'failed', 'code': 'configuration_or_protocol_check_failed'}))
    raise SystemExit(2) from None
PY
```

결과 해석:

| 결과 | 의미와 다음 판단 |
|---|---|
| `chatgpt_account=false` | 해당 컨테이너의 ChatGPT 로그인을 운영자가 확인한다. 인증파일은 공유하지 않는다. |
| `web_search_live=false` | 실행 override·CLI pin·설정 계약을 확인한다. 소스의 live 문자열만으로 실제 설정을 확인한 것은 아니다. |
| `provider_advertises_web_search=false` | 제공자 메타데이터가 검색 지원을 알리지 않는다. 모델·경로를 자동 변경하지 않고 설정을 확인한다. |
| 모두 예상과 일치 | 구성·제공자 능력 메타데이터까지만 확인됐다. 대상 모델·turn의 실제 도구 노출이나 검색 성공은 증명하지 않는다. |
| `check=failed` | 설정/프로토콜 점검도 미완료다. 원문 오류를 복사하지 않고 CLI pin·서버 환경을 확인한다. |

`modelProvider/capabilities/read`는 제공자 메타데이터이며 모델별 도구 목록이 아니다. MCP의 `tools/list`와도 별개다.

이 문서의 Python을 클라우드의 빈 임시 CODEX_HOME·실제 고정 0.153.0에서 그대로 실행해 `chatgpt_account=false`, `web_search_live=true`, `provider_advertises_web_search=true`, `default_provider_route=true`와 정상 종료를 확인했다. thread/turn·실제 모델 호출은 0회다. **운영 로그인/실제 검색 시험이 아니라 비로그인 격리 환경의 설정·메타데이터 절차 검증**이다. 제공자 능력 metadata는 계정별 사용 가능 여부도 보장하지 않는다.

## 3. 명시적인 공개 검색 1건만 시험

설정 확인이 정상이어도 실제 노출·호출 성공은 별도 시험이 필요하다. 같은 앱 계정과 이미 성공한 모델 설정을 유지하고, 회사 자료 없이 예를 들어 “공식 기관의 공개 웹 문서 한 건을 검색해 제목·게시일·URL을 출처로 표시하는 짧은 보고서”를 **공개자료 조회 켬**으로 새 요청한다. 실패 접수도 앱 일일 한도를 소비하므로 자동 반복하지 않는다. 모델·CLI를 자동 교체하거나 연구를 자동 해제하지 않는다. 기존 research=false 성공을 다시 시험할 필요는 없다.

새 코드에서는 다음 필드만 작업 ID와 함께 기록한다: status, input.research(boolean), execution.completed(boolean), failure의 code/stage/research_calls/protocol_error/http_status, progress의 stage/research_calls, 숫자 timings. prompt·draft·blocks·검색어·출처 원문·error.message/trace는 공유하지 않는다. 현재 UI에서는 실패 단계와 안내를 확인할 수 있다. 전체 API 응답/CLI debug 로그를 그대로 복사하지 않는다.

- `research_unavailable`, 완료 검색 0건: 정상 생성/JSON과 검색 경로를 구분한다. 이 결과만으로 “모델이 도구를 못 받음”, “모델이 호출을 선택하지 않음”, “upstream이 거부/미제공” 중 하나를 확정하지 않는다.
- 공식 upstream 오류 종류 또는 HTTP 상태가 있으면 그 허용된 값만으로 rate/usage/connection/unauthorized 등을 분류한다. 없는 값을 추정해서 채우지 않는다.
- 완료 검색이 기록돼도 최종 ReportDraft 검증·내용 검토를 별도로 확인한다. 검색 횟수는 출처 사실성의 자동 보증이 아니다. 시험을 위해 승인/서버 저장을 누를 필요는 없다.

제공자 metadata가 정상인데 반복적으로 검색 이벤트 없이 실패한다면 **해당 실제 모델/계정/turn의 upstream 도구 노출·호출 실패가 남은 조사 대상**이다. 원문 모델 오류/개인 자료를 무작정 출력하지 말고 위 최소 metadata와 CLI 0.153.0/source SHA/시간만 보존해 운영자와 확인한다. 필요 시 별도 승인을 받은 격리된 공개자료 전용 A/B probe로 outputSchema 유무를 비교하되, 현재 앱에서 임의로 계약을 완화하거나 부분 JSON을 draft로 승인하지 않는다. 이 추가 실제 모델 probe는 구현/실행 완료로 보고하지 않는다.

A/B를 진행한다면 같은 고정 CLI·모델·설정·빈 작업 디렉터리·공개 합성 요청·기한을 사용하고 각각 **새 ephemeral thread**에서 실행한다. A는 기존 `ReportDraft.generation_schema()`, B는 outputSchema만 제외한다. 기존 read-only/no-approval/MCP·기타 도구 차단을 유지한다. terminal 상태·검색 시작/완료 수·허용 오류 코드/HTTP 상태·경과시간만 보존하고 B 출력은 앱 초안·미리보기·저장에 전달하지 않는다. 첫 비교 한 쌍만 명시적으로 실행하며 자동 반복하지 않는다. A 0건/B 1건 이상이면 schema 경로 상호작용의 후보를 좁히지만 모델의 비결정성 때문에 원인을 확정하지 않는다. 둘 다 0건이면 도구 미노출·선택·upstream 실패를 구분하지 못한다.

프로토콜 기준: [고정 app-server README](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/app-server/README.md), 설치된 0.153.0의 `app-server generate-ts`에서 생성되는 ClientRequest 및 ModelProviderCapabilitiesReadResponse 계약. capability metadata를 실제 검색 성공과 혼동하지 않는다.
