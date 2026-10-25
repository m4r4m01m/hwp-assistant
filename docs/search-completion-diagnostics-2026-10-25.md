# 검색 완료 집계와 안전한 실행 진단 보완

기록일: **2026-10-25 KST**. 기준 main은 **`4e364c1a4e74eed5a6ad86a9694adb1189a54bee`**, branch는 `codex/search-completion-diagnostics-2026-10-25`다. 사용자의 직접 요청은 **“검색 완료를 왜 확인하지 못할까? 개선할 방법은 없나?”**였다. 그 개선 요청 범위에서 원 대화 root가 조사·구현·회귀·PR 준비를 인계했다. 처음에는 집계·진단을 보완하고 도구 노출 flag는 격리 진단 결과까지 보류했으며, A/B 뒤 root의 구현 인계에 따라 연구 ON 보고서에 한정한 web 직접 노출과 commit/push·Draft PR을 준비했다. 사용자가 특정 flag나 세부 실행 절차를 직접 지정·명시 승인한 것은 아니다. 새 코드의 main 병합·운영 배포는 별도 미승인·미실행이다. 클라우드 실제 모델 호출은 0회이며 운영 credential도 클라우드에 없다. 기존 배포 인수인계의 미커밋 문서 두 개는 원래 작업 트리에 그대로 보존한다.

제품 commit **[`081adee4ee7f14d712283dc80243a41390f1af81`](https://github.com/m4r4m01m/hwp-assistant/commit/081adee4ee7f14d712283dc80243a41390f1af81)**(parent exact main `4e364c1a`, tree `7f5dd1ba5b8c411968774057dbc61dc588306a42`)를 push하고 **[Draft PR #38](https://github.com/m4r4m01m/hwp-assistant/pull/38)**을 만들었다. 이후 문서 commit의 새 head에서는 CI를 다시 확인해야 한다. source·시험·운영 인계의 기계 판독 기록은 [검증 영수증](evidence/search-completion-diagnostics-2026-10-25.json)에 남긴다.

## 1. 확인한 결함과 운영 원인의 구분

[`report_stream.py`](../backend/app/report_stream.py)는 이벤트의 `item/completed` 검색은 세지만 terminal `turn/completed.items`의 `webSearch`를 처리하지 않았다. 합성 stdio에 완료된 검색 payload와 유효한 최종 ReportDraft를 함께 보내면 검색이 실제로 관측됐어도 0건이 되고, 연구 OFF의 summary 검색도 scope 검사에서 빠졌다.

같은 합성 알림을 exact main의 원본 코드와 수정 코드에 각각 재생한 결과다. 실제 모델·운영 API·DB를 호출하지 않았다. 아래 순서는 **완료/시작/미완료**이며 URL·사실·최신성 검증 수가 아니다.

| 합성 입력 | 기준 main | 수정 후 |
|---|---|---|
| 완료 검색이 summary에만 존재 | 0/0/0, notice `unverified` | 1/0/0, notice `completed` |
| started 이벤트 + summary의 완료 검색 | 0/1/1, notice `unverified` | 1/1/0, notice `completed` |
| 연구 OFF + summary의 검색 | draft 완료로 진행, notice `not_requested` | `scope_violation`으로 거부 |

`started_calls`는 **시작이 관측된 고유 검색 수**이며 요약에 남은 시작 스냅샷도 포함한다. 개별 시작 알림 수가 아니다. summary-only의 **1/0/0**은 완료 검색 payload 1개는 관측했지만 시작은 관측하지 않았다는 뜻이며 횟수를 가정해 채우지 않는다. B 진단의 terminal 검색 item 수 0과 이 합성 summary 재현은 별도 증거다.

**이 재현은 현재 운영에서 검색 0회가 나온 원인 증거가 아니다.** 고정 0.153의 실제 app-server는 현재 terminal items에 최종 agent 메시지만 넣는다. 검색 payload가 요약에 오는 경로를 처리하는 것은 계약 보강이다. 운영 검색 도구 노출·호출 여부와 upstream 실패 원인은 별도 진단으로 확인해야 한다.

## 2. 고정 0.153의 계약

기준은 `openai/codex`의 **`rust-v0.153.0`** 및 해당 CLI가 생성한 TypeScript schema다. 모델·CLI·provider·lock은 바꾸지 않았다.

| 항목 | 계약과 처리 |
|---|---|
| `WebSearchItem` | `id:string`, `query:string`, `action:WebSearchAction\|null`, `results:JsonValue[]\|null`. 별도 `status` 필드가 없으므로 임의 상태값을 요구하거나 추정하지 않음 |
| action | `search`, `openPage`, `findInPage`, `other`. Responses의 원시 `open_page` 등과 혼동하지 않음 |
| 시작 → 완료 | 시작의 빈 query·null action/results가 완료 때 채워지는 변화는 정상. 시작과 완료 전체 payload의 일치를 요구하지 않음 |
| 이벤트 완료 | `item/completed`가 authoritative completion이며 검색 결과의 사실성 검증을 뜻하지 않음 |
| 요약 fallback | 성공한 terminal turn의 완료 검색 payload만 처리. `action:null` 시작 스냅샷은 완료로 올리지 않음 |
| terminal 식별 | 실제 `TurnCompletedNotification`은 `threadId`와 `turn.id`를 사용. 별도 outer `turnId`가 필수라는 가정을 하지 않으며, 제공된 outer ID가 충돌하면 수용하지 않음 |

Primary source:

- [app-server lifecycle·summary·webSearch 설명](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/app-server/README.md#L1805)
- [현재 final-agent-only terminal summary 생성](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/app-server/src/bespoke_event_handling.rs#L1392)
- [검색 시작/완료 history payload](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/app-server-protocol/src/protocol/thread_history.rs#L634)
- [실제 검색 성공 뒤 완료 이벤트 발행](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/ext/web-search/src/tool.rs#L131)
- [WebSearchItem/action 구조](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/ext/items/src/web_search.rs#L13)

## 3. 변경 동작과 보존 경계

이벤트와 terminal summary는 같은 helper를 통과한다. 비어 있지 않은 문자열 ID를 검사하고 ID의 item type을 기억하며, 완료 payload의 fingerprint로 반복 전송을 한 번만 처리한다. 같은 ID의 서로 다른 완료 query/action/results 또는 agent text/phase/delivery는 protocol 오류다. **충돌 비교는 완료끼리만** 하며 시작 때 임시 query/action이 완료 payload에서 바뀌는 정상 변화를 허용한다. 반복 완료의 optional results 누락과 null은 같은 값으로 정규화한다. 과거 JSON 완료 메시지가 요약에 다시 나타나도 뒤의 non-JSON final을 취소하고 과거 응답을 되살릴 수 없다.

연구 OFF이면 이벤트·요약의 검색을 모두 거부한다. 다른 thread/turn은 현재 요청에 반영하지 않는다. failed/interrupted/in-progress terminal, commentary·async·delta만 있는 출력, timeout·취소·invalid ReportDraft는 draft ready로 승격하지 않는다. 최종 정상 완료·유효 ReportDraft, 기존 `require_completed`/`allow_unverified` 구분, 내용 검토 → 명시적 서식 준비 → 편집 → 사용자 확정 저장 순서를 유지한다.

새 집계는 앞으로 받는 실행에만 적용한다. 이미 동결된 draft/notice/hash, 역사 preview, 보완본의 경고 상속, 수동 편집·유실 저장 재시도의 HWPX 바이트를 소급 변경하지 않는다. 검색 완료 횟수는 출처 URL·인용·현재 사실의 검증 완료 표시가 아니다. sources의 기존 미검증 표시도 유지한다.

## 4. 성공한 경고 초안의 안전한 오류 진단

기존 실패 진단의 [`upstream_details`](../backend/app/report_errors.py) 허용 목록을 재사용해 실제 수신한 마지막 오류 종류와 유효한 정수 HTTP 상태만 성공 실행의 `execution.upstream_diagnostics`에 보존한다. 진단이 없으면 필드를 만들지 않는다. 예시는 합성 값이다.

```json
{"protocol_error":"httpConnectionFailed","http_status":503}
```

알 수 없는 후속 오류가 이전의 유효한 진단을 지우지 않는다. 새 오류 종류에 이전 HTTP 상태를 붙이지 않으며 새 관측 하나로 교체한다. 문자열·boolean·범위 밖 HTTP 상태, 미등록 오류 종류·원문 message·additionalDetails·credential·검색 질의·검색 결과는 진단에 넣지 않는다. 검색 progress activity도 질의·URL 대신 고정 안내를 사용한다.

이 필드는 **수신 오류 관측**이며 검색 장애의 확정 원인이나 검색 성공 증거가 아니다. draft와 `research_notice`에 넣지 않으므로 같은 입력·초안의 SHA를 진단 유무로 바꾸지 않는다. 같은 완료 작업을 다시 조회해도 모델·사용량을 추가 소비하지 않는다. 원문 오류를 운영 로그나 별도 파일에 수집하는 절차도 추가하지 않았다.

## 5. 검증 상태

Python **3.14.0**, 기존 backend/MCP requirements lock 환경에서 합성 자료만 사용했다. 새 회귀는 실제 stdio 재생과 fake RPC/API 테스트를 구분한다.

- summary-only·started+summary·이벤트/summary 반복·action 변형·ID 누락/잘못된 타입/충돌·연구 OFF·외부 thread/turn·시작 스냅샷·실패 turn·부분/invalid final·legacy hash·timeout을 검사한다.
- 진단 회귀는 성공 경고 draft의 실행 진단·raw 데이터 배제·SHA 불변·legacy·API ready 멱등·다른 thread/turn 분리와 실제 stdio 완료를 검사한다.
- flag 조건부 명령 검사를 포함한 새 회귀는 **102 passed·기존 warning 2개·2.81초**다. 앞선 flag 이전 집중 회귀와 합산하지 않는다.
- 집계·진단의 독립 검토에서 추가 차단 결함은 없었고 별도 회귀 28개가 통과했다. 추가 flag 독립 집중 검토도 **8 passed/69 deselected·기존 warning 2개**, 차단 finding 없음으로 인계됐다. 이 별도 부분집합들을 전체 고유 시험 수로 더하지 않는다.
- Python 배포 계약 **75개** 통과: deployment 13·isolated host preflight 4·CI 16·smoke 12·developer download 6·published run 24. published 검사 파일은 `scripts/deploy/test_published_run.py`다. 이 결과는 코드 배포나 실제 모델 성공이 아니다.
- 실제 고정 CLI **0.153.0**를 빈 임시 `CODEX_HOME`에서 실행해 initialize/config read/provider capabilities read만 확인했다. 연구 OFF는 `web_search=disabled`·direct namespaces null, ON은 live·`["web"]`였고 provider·sandbox·approval·disabled features는 동일했다. account/thread/turn/model 호출은 **0회**이며 자기 프로세스·reader 정리를 확인했다. archive SHA-256은 `35a82c153d83959de09c2cb84ac70ba69d05788aeeb08d4a95ca68e39f86680e`, binary SHA-256은 `fce635028842bfe9257140e8b7d53162732945e2f356fc35225be0702b4974be`다.
- flag 포함 최종 backend 전체는 **단일 실행 exit 0·JUnit 1,268 case·failure/error/skip 0·256.117초**로 확인했다. JUnit timestamp는 `2026-10-25T23:13:16.946062+09:00`이며 실행 중 제품 파일 2개는 전후 모두 제품 commit `081adee4`와 동일했다. stdout은 85%까지만 있고 최종 요약이 없어 **전체 warning 수는 미확정**이다. 집중 회귀의 warning 2개를 전체 실행에 복제하지 않는다. JUnit·정상 종료·source 대조를 완료 근거로 사용하며 stdout 요약이 있다고 쓰지 않는다.
- 후속 문서 commit의 새 head에서는 exact CI를 다시 확인한다. 이전 head의 성공으로 최종 head 성공을 대신하지 않는다.

frontend·고정 rhwp·문서 표준·DB schema·배포 설정은 이번 변경 대상이 아니다. frontend mock UI와 실제 rhwp E2E를 이번 후속에서 재실행하지 않았다. 새 코드가 배포된 앱 endpoint의 실제 검색·한컴·폐쇄망 실모델 검증도 하지 않았다. 아래 candidate의 별도 프로세스 검증과 구분하며 기존 배포 run의 통과 수를 이번 로컬 변경의 검증으로 대체하지 않는다.

## 6. 별도 운영 진단과 남은 판단

2026-10-25 KST 사용자 인계에서 원 대화의 운영자는 추론 없는 metadata로 ChatGPT 로그인, live/provider webSearch, 기본 OpenAI route가 유효함을 확인했다. exact `gpt-6-astra` metadata는 `use_responses_lite=true`, `supports_search_tool=true`, `tool_mode=code_mode_only`, `web_search_tool_type=text_and_image`, provider namespace tools 지원을 보였다. 이 값은 클라우드가 운영 credential로 조회한 것이 아니다.

독립 pinned source 검토에서 `code_mode_only`가 standalone `web.run`을 `functions.exec` 내부로 노출하는 경로와 `features.code_mode.direct_only_tool_namespaces=["web"]` 설정을 확인했다는 인계가 있다. standalone flag 자체는 lite 모델에서 자동 선택되므로 단순 feature false가 원인이라고 단정하지 않는다.

원 대화 운영자는 **운영 설정을 바꾸지 않는 격리 A/B 진단**을 마쳤다. A는 기존 명령, B는 오직 `-c features.code_mode.direct_only_tool_namespaces=["web"]`만 추가했으며 같은 `gpt-6-astra`·ReportDraft schema·합성 Python 공식 문서 주제·prompt를 썼다. 각 150초 상한·app idle 확인 후 단일 turn을 실행했다. 다음 수치는 원 대화 root의 직접 실행을 사용자가 인계한 것이며 클라우드 직접 모델 검증이 아니다.

| 관측 | A: 기존 명령 | B: web 직접 노출 추가 |
|---|---|---|
| 실행 시간 | 23.266초 | 26.934초 |
| 최종 출력 | 정상 완료·유효 draft | 정상 완료·유효 draft |
| 검색 started/completed/incomplete | 0/0/0 | 2/2/0 |
| notice 상태 | `unverified` | `completed` |
| terminal summary의 검색 item 수 | 0 | 0 |
| 오류 알림 | 없음 | 별도 수치 미인계 |

A/B 진단의 실모델 호출은 **2회**다. app DB 작업·문서 생성·운영 설정 변경은 0회였고 raw 출력·오류 원문·credential을 보존하지 않았다. 따라서 집계 누락보다 **도구 노출 형태가 검색 호출 여부에 영향을 준다는 직접 관측**이 추가됐다. 단일 A/B의 비결정성은 남아 있으며 모든 주제·시간·모델의 성공이나 검색 결과의 사실 검증을 보증하지 않는다. 같은 원인으로 모든 과거 0회를 확정하거나 이미 배포돼 복구됐다고 표현하지 않는다.

원 대화 root는 사용자 개선 요청 범위에서 이 결과에 따른 연구 ON 보고서의 명령 보완·회귀·commit/push·Draft PR 준비를 구현 인계했고, 제품 코드에 조건부 override를 반영했다. 연구 OFF는 기존 `web_search=disabled`를 유지하고 직접 노출 override를 넣지 않는다. 모델·CLI·provider를 교체하거나 추가 turn·유료 fallback·다른 도구 허용을 추가하지 않는다. 명령이 조건부 override를 정확히 전달하는 회귀와 기존 scope fence를 확인하고 위 제품 commit과 Draft PR을 준비했다. 새 코드의 main 병합·운영 배포는 별도 미승인이다.

## 7. Exact 제품 candidate의 별도 프로세스 검증

원 대화 root는 제품 commit **`081adee4ee7f14d712283dc80243a41390f1af81`**의 정확한 두 Python 파일 blob을 별도 프로세스 메모리에 불러와 candidate의 전체 `generate_stream` 경로를 실제 모델로 검증했다. wrapper가 flag를 추가하지 않았고 제품 코드에 있는 조건부 설정을 그대로 사용했다. 같은 `gpt-6-astra`·ReportDraft schema·공개 합성 Python 공식 문서 요청을 app idle 확인 후 **단일 turn·150초 상한**으로 실행했다. 원 대화 root의 직접 실행이며 앱 endpoint 호출이나 클라우드 모델 실행이 아니다.

| 관측 | Candidate 결과 |
|---|---|
| 실행 시간 | 32.111초 |
| 최종 출력 | 정상 완료·유효 ReportDraft |
| 검색 이벤트 started/completed/incomplete | 2/2/0 |
| notice | `completed` |
| upstream 오류 | 없음 |
| terminal summary의 검색 item 수 | 0 |
| 운영 변경 | 운영 파일·private env·app DB 문서/작업 불변 |

이 검증의 실제 모델 호출은 **1회**이고, 이번 검색 조사의 operator 호출 합계는 **A/B 2회 + candidate 1회 = 3회**다. raw 출력·오류 원문·credential은 보존하지 않았다. 실제 배포된 앱 경로의 검증, 전체 주제·시점·모델 성공 또는 검색 결과의 개별 사실 검증으로 확대하지 않는다. 기존 HWPX·동결 초안·수동 편집과 롤백 계약은 이번 문서 정정으로 바뀌지 않았다.
