# 후보 생성 안전 경계 마감 · 2026-10-06

기본 문서 AI는 **Codex**다. `HWP_REWRITE_PROVIDER` 미설정 시 `codex`, 명시적 `llm` 경로는 선택 사항으로 유지했다. 이번 작업에서 실제 모델 설치·다운로드·실호출은 없었으며 로컬 모델 서버 준비를 선행 조건으로 요구하지 않았다. 컨텍스트는 [별도 설계](rewrite-context-design-2026-10-06.md)만 작성했다.

## 기준과 변경 식별

시작·종료 HEAD는 `main / 1e0538781e02ac85d095bb3c72bf844943c305b3`다. 시작부터 tracked 변경 21개와 untracked 파일 23개가 있었으며, 시작 파일 182개의 SHA-256 및 diff를 로컬에 보관한 뒤 수정했다. 이번 요청에서는 fetch/pull/merge/commit/push/index 변경을 하지 않았다.

최종 실행 변경셋 ID는 `0a8bef6e932f590903e81ee60cfdf46fe9c3c7a59994711dae26024de8335843`다. [실행 manifest](evidence/rewrite-boundaries-source-manifest-2026-10-06.json)는 HEAD 외에 tracked/untracked 실행 소스·시험·lock·스크립트 90개와 사용 엔진 7개(번들 6개 + build-info)의 개별 해시를 기록한다. 문서는 실행 변경셋 ID 대상에서 제외하고 공유 패키지 manifest에 따로 기록한다.

시작 시 이미 존재하던 파일 중 이번에 수정한 것은 17개다. 제품 코드는 `auth.py`, `codex_runner.py`, `collaboration.py`, `llm.py`, `main.py`, `rewrite.py`, `rewrite_contract.py`, `rewrite_jobs.py`, `frontend/src/main.tsx`이며 나머지는 관련 시험·격리 실행기다. 새 실행 코드는 `backend/app/rewrite_capacity.py`, `backend/app/cli_process.py`, 새 시험은 `backend/tests/test_cli_process.py`, 새 검증 도구는 `scripts/verify-served-build.mjs`다. 시작 전부터 untracked였던 파일도 이번 변경과 구별해 [요약 JSON](evidence/rewrite-boundaries-verified-2026-10-06.json)에 표시한다. 기존 문서·날짜별 증거·실패 기록·요구사항 기준선을 덮어쓰지 않았다.

## 재현과 최소 수정

접수한 경계 시험을 먼저 실행했을 때 전달받은 것과 같은 **3 failed**였다. 시험 삭제, skip/xfail 전환은 하지 않았다.

| 시작 실패 | 원인 | 수정 |
|---|---|---|
| 논리 중단 2개 뒤 세 번째 요청도 접수 | 물리 실행 상한 없음. 시험의 N=2는 기존 제품 사양이 아닌 제안값 | 공통 실행 슬롯 및 429. 시험에서 `HWP_REWRITE_MAX_ACTIVE=2` 명시 |
| 접수 뒤 모델/endpoint 변경이 기존 Codex 실행에 반영 | worker가 실행 때 전역 설정을 읽음 | 불변 `CodexSettings`를 접수 때 생성해 worker에 전달. 모델·endpoint 독립 매개변수 시험 |
| 다른 트랜잭션의 임대 변경이 cached Document에 반영되지 않음 | ORM identity map의 기존 값 재사용 | 문서 잠금과 필요한 객체의 최신 조회/refresh. dirty/new/deleted 상태 Session은 변경을 잃거나 autoflush하지 않고 거부 |

공통 슬롯은 **앱 인스턴스/프로세스 내부** 제한이다. 설정 `HWP_REWRITE_MAX_ACTIVE`는 1~64의 정수이고 개발 기본값은 제안값 2다. 운영 용량 측정 결과가 아니다. 새 Codex·llm 실행이 같은 상한을 사용하고 초과 시 429를 반환한다. 대기열·자동 재요청·자동 provider 변경은 없다. 같은 ID 재확인은 현재 권한·임대를 검사한 뒤 기존 작업을 반환하며 새 슬롯/호출을 만들지 않는다. commit/dispatch/파싱/게시/정상 종료/예기치 않은 실행기 예외에서 정확히 한 번 반환한다. 논리 중단·만료·ready는 반환 조건이 아니다.

추가 조사에서는 기존 `subprocess.run`을 쓰면 합성 부모가 정상 종료되거나 timeout으로 끝난 뒤에도 자식이 남는 것을 2개 시험으로 재현했다. Windows 실행은 시작 일시정지 → 이 실행 전용 Job Object에 할당 → 실행 재개 순서로 감싸고, 종료 시 해당 묶음의 종료·파이프 정리가 끝날 때까지 반환하지 않는다. 다른 개발 프로세스는 대상이 아니다. 이는 [Microsoft의 Job Object 수명 규칙](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)에 따른 자식 수명 관리다. 프로세스 생성 직후 job 할당 전 호스트 강제 종료 같은 crash 경계와 비 Windows 분기는 이번 실환경 시험 범위 밖이다. 논리 중단은 진행 중 CLI/HTTP의 즉시 물리 종료를 뜻하지 않는다. HTTP 클라이언트 종료/timeout도 원격 모델 서버의 추론 종료를 증명하지 않는다. 여러 앱 인스턴스·worker·서버·재시작을 포함한 전역 상한은 제공하지 않는다.

Codex 명세의 연결 주소는 `HWP_CODEX_API_URL`, 즉 **작업용 MCP가 호출할 HWP API 주소**다. 모델 서버 주소가 아니다. 명세에는 실행 파일, 설정 모델(미지정 시 null), 연결 주소, 프롬프트 계약, timeout, 필요한 실행 환경 복사본을 포함한다. 토큰은 별도 인메모리 실행 인자로만 전달한다. 인증 파일은 수집하지 않고 환경 복사본도 DB/공유 로그에 저장하지 않는다. 공개 실행 메타데이터는 비밀값·private endpoint를 제외한다. 기존 ChatGPT 로그인 검사, `HWP_CODEX_USER`, 두 개의 제한된 MCP 도구를 유지한다. CLI가 실제 선택한 모델을 관찰하지 못했으면 `CLI default; ID not confirmed`로 남긴다. 과거 작업의 미기록 모델을 현재 설정으로 채우지 않는다. LLM 설정 객체도 immutable이며 endpoint/model/key/timeout이 접수 후 바뀌지 않는다.

권한 정책은 이번에 명시적으로 보수화했다. 요청자의 editor 권한 회수·reader 강등 또는 임대 반납/종료 후 새 발급은 기존 권한/임대 변경 트랜잭션 안에서 당시 running 작업을 무효화한다. 재부여·재발급으로 복구하지 않는다. 정상 갱신과 무관한 타 사용자의 ACL 변경은 유효한 작업을 무효화하지 않는다. 이미 게시된 후보·Application·과거 이력은 삭제하지 않는다. 이를 반영해 기존 협업 시험의 “bob 권한 회수로 alice 임대도 해제” 기대와 두 fixture의 암묵적 임대 해제 의존을 수정했다.

미해결 승인 출력이 있으면 새 후보 생성과 다른 승인으로의 전환을 보류한다. 이유를 표시하고 새 지시 문자열을 유지한다. 기존 승인 출력/영수증/저장 키는 논리 중단과 별도로 보관한다. 승인 저장의 네트워크·권한 오류는 `저장 확인 실패`와 서버 반영 여부 미확인으로 표시하며, commit된 저장이 취소됐다고 표시하지 않는다. 페이지 수 거부의 엔진 동작은 수정하지 않았다.

## A~D 시험 연결

표의 경로 약어: `L` = `backend/tests/test_local_rewrite.py`, `R` = `backend/tests/test_rewrite.py`, `B` = `backend/tests/test_rewrite_boundaries.py`, `E` = `frontend/tests/local-rewrite.spec.ts`, `P` = `frontend/tests/rewrite-protection.spec.ts`. backend 결과는 최종 pytest XML의 실제 수집 결과로 연결한다. `B`의 35개 매개변수 사례가 모두 수집됐다.

| 조건 | 기존 시험 함수·핵심 assertion | 부족한 조합 | 추가/수정 시험 | 최종 결과 | 미검증 경계 |
|---|---|---|---|---|---|
| A 중단/만료 commit → 늦은 게시 | L `test_cancelled_task_does_not_publish_late_results_or_restart`, `test_expired_job_is_visible_as_expired_and_same_id_never_dispatches_again`; 후보 없음·호출 증가 없음 | 실제 Event로 순서 고정 | B `test_event_ordered_authority_transitions_do_not_revive_pending_work[cancel/expire]` | pytest 통과 | SQLite 합성 DB, 실제 모델 아님 |
| A 게시 commit → 늦은 중단/예외 | L `test_completed_task_cannot_be_cancelled_or_candidates_removed`; R `test_cancel_codex_submission_preserves_candidates_and_invalidates_run_token` | 게시 성공 후 worker 예외 | B `test_published_success_survives_late_cancel_and_failure`; 이력 전체 동일 | pytest 통과 | PostgreSQL 행 잠금 미검증 |
| A 권한 회수/강등→재부여, 반납→재발급, 정상 갱신 | L `test_permission_revocation_during_model_call_prevents_publication`, `test_generation_rechecks_state_without_holding_write_transaction` | 중간 상실 이력, 정상 갱신·무관한 ACL 구별 | B Event 시험 `release_reissue/renew/unrelated_acl/revoke_regrant/downgrade_regrant` | pytest 통과 | 다중 서버 경쟁 미검증 |
| A 최신 ORM 객체, pending 변경 보존 | 기존 worker는 호출 전/게시 때 새 transaction Session 사용 | cached Session·권한/임대 소유자 캐시·dirty Session | B `test_publication_lock_refreshes_preloaded_task_and_document`, `test_publication_checks_latest_authority_in_clean_sessions` 6조합, `test_lock_helper_refuses_dirty_session_without_flushing_or_discarding` | pytest 통과 | DB snapshot isolation과 ORM cache는 별개. PostgreSQL 결과 아님 |
| B PAGE_COUNT_CHANGED rollback | P `4000 Unicode codepoints roll back when pagination changes`; 원문·문서 해시·바이트 일치 | 제품 승인 기록 뒤 실제 거부 및 복구 | E `page-count rejection after approval preserves bytes and permits recovery` | 최종 source/built 결과 아래 참조 | 실제 rhwp, 모의 후보·자동 클릭 |
| B Application 보존·영수증 없음·버전 불변 | R `test_receipt_save_atomic_commit_idempotency_and_original_preservation`는 저장 원자성 검증 | 엔진 거부 자체와 연결 | 같은 E: approved 유지, saved_version null, receipt 요청 0, 버전 1; 비교 종료/수동 저장 후 2, 새 작업 승인 후 3 | 최종 source/built 결과 아래 참조 | 사람 판단·한컴 결과 아님 |
| C 생성 재확인/논리 중단 vs 승인 출력 | E `lost generation response reuses the original task and snapshot`, `approve/cancel resolves a lost request so a new instruction can start` | 미해결 영수증과 다른 작업 중단·새 지시의 결합 | E 기존 `lost receipt response...` 확장: 응답 gate로 다른 작업 고정, 중단 후 새 생성 보류·지시 보존·기존 영수증/출력 동일 | 최종 source/built 결과 아래 참조 | 일반 브라우저 crash 복구는 범위 밖 |
| C commit 응답 유실 + 후속 입력 | E 기존 `lost approved-save response...` | 추가 입력 및 권한 거부 응답의 화면 표시 | 기존 E 확장, permission response false/true 2조합: 같은 키/출력 재확인, 추가 입력 dirty, 명시 저장으로만 추가 버전 | 최종 source/built 결과 아래 참조 | UI의 403은 합성 응답; 실제 권한은 아래 별도 API 시험 |
| C A의 늦은 응답이 B를 건드리지 않음 | 컴포넌트 분리·effect cleanup 코드 존재 | 제품에서 HTTP 응답 장벽과 문서 전환 | E `late document A generation response cannot alter document B`; B snapshot·dirty·작업 목록 보존 | 최종 source/built 결과 아래 참조 | 생성 응답 경계. 모든 비동기 UI 작업 일반화 아님 |
| C 현재 권한 재검사, 저장 이력 유지 | R `test_other_document_reader_sees_candidates_history_but_cannot_apply`; 기존 저장 권한 검사 | 완료된 승인/영수증/저장 재시도 후 회수·강등 | B `test_committed_save_retries_recheck_current_access_without_rollback` 2조합; 4 endpoint 403/404, saved/바이트/버전 동일 | pytest 통과 | 합성 인증 계정·SQLite |
| D 같은 ID 동시 요청/다른 입력 | L `test_concurrent_same_id_dispatches_once`, `test_same_id_different_request_conflicts_without_duplicate_execution`; dispatch 1/409 | 새 슬롯 기능과 회귀 연결 | 기존 시험 재사용. B 포화 상태 재확인도 포함 | pytest 통과 | 프로세스 간 전역 슬롯 아님 |
| D 취소 반복, 실제 실행이 끝날 때 슬롯 반환 | 기존 논리 중단·timeout 시험 | 공통 실행 상한·ready 이후 cleanup·누수 | B `test_event_held_workers_keep_slots_after_logical_cancel`, `test_codex_and_llm_share_capacity_until_ready_worker_cleanup_finishes`, `test_slot_released_once_after_all_exit_paths` 5조합, `test_unexpected_runner_exception_still_releases_slot` | pytest 통과 | 원격 추론 종료는 모름 |
| D CLI 자식 종료 | 기존 `test_codex_timeout_keeps_selected_model_without_a_fallback`은 모의 CLI timeout | 실제 자식의 정상/timeout 후 생존 | `backend/tests/test_cli_process.py::test_runner_reaps_owned_descendants_before_return` 2조합 | pytest 통과 | 실제 Windows 합성 프로세스; Codex 실행 아님 |
| D 설정 고정, 미지정 모델, 기본 Codex | L `test_two_candidates_use_frozen_unsaved_input_without_codex_or_writes`; 기존 CLI 제한 도구 시험 | Codex 모델·endpoint 독립 확인, unknown, 불변 객체 | B `test_codex_dispatch_freezes_model_and_endpoint_at_task_acceptance[model/endpoint]`, `test_default_codex_unknown_model_stays_unknown_and_specs_are_immutable`, `test_local_model_endpoint_key_timeout_fixed_at_acceptance`, invalid capacity 4조합 | pytest 통과 | 실제 CLI/모델의 응답 품질 미검증 |

경쟁 시험은 Event/gate와 짧은 유한 timeout을 사용하고 finally에서 장벽을 풀고 만든 worker를 정리한다. ORM 시험은 별도 트랜잭션의 commit 뒤 읽기를 비교한다. 광범위한 expire_all, 임의 commit/rollback으로 Session 변경을 지우는 방식은 사용하지 않았다.

## 동일 최종 변경셋 회귀

공통 실행 루트는 `.cache/rewrite-boundaries-fix-2026-10-06/`다. 실행 manifest v2를 먼저 만든 뒤 아래 명령을 수행했다. 마지막에 같은 해시를 다시 대조한다.

| 명령 | 수집/통과/실패/skip | 근거 |
|---|---|---|
| `.venv/Scripts/python -m pytest -q --junitxml=.cache/rewrite-boundaries-fix-2026-10-06/final-v2-pytest.xml` | 173 / 173 / 0 / 0, warning 1 | `final-v2-pytest.xml`, 36.14초. Starlette/AnyIO deprecation |
| `node frontend/node_modules/typescript/bin/tsc --noEmit --project frontend/tsconfig.json` | exit 0, 개수 N/A | `final-v2-tsc.log` |
| `node --test scripts/engine-build.test.mjs` | 11 / 11 / 0 / 0 | `final-v2-engine-guard.log` |
| `powershell -NoProfile -File scripts/test-isolated.ps1 -EnginePublic .cache/engine-release-2026-09-27/public -OutDir .cache/rewrite-boundaries-fix-2026-10-06/final-source-e2e-v2` | 28 / 27 / 0 / 1 | 인증 시험 통과. 일반/인증/로컬 각 서버의 엔진 HTTP 바이트 7개 일치. 1.9분 |
| 위 명령의 `-OutDir .../final-built-e2e-v2 -BuiltApp` | 16 / 15 / 0 / 1 | 이번 소스로 새 진단 앱 번들 생성, 서버별 index/JS/CSS+엔진 10개 일치. 1.2분. 일반 source 전용 동적 import 시험은 범위 밖 |
| `cd frontend; npm.cmd run build` | exit 1, 배포 차단 유지 | `Missing pinned core/bundle provenance`. 기본 엔진의 배포 근거가 없어 guard에서 중단. 이를 진단 빌드 성공으로 대체 보고하지 않음 |

최종 이전 결과는 합산하지 않는다. 첫 manifest `4cfe1e06…`의 pytest는 168 passed / 5 failed였으며, 불변 설정 객체에 직접 mock 함수를 대입하던 기존 `test_llm.py`가 실패했다. assertion을 그대로 두고 클래스 수준 monkeypatch로 바꿨다. 그 이전 source E2E 27 passed / 1 opt-in skip과 TypeScript·빌드 보호 결과도 이전 변경셋 결과로 보관했다. 최종 표는 수정 후 전체 재실행만 포함한다.

페이지 복구 초기 시험은 후속 저장 시작 전부터 남아 있던 “저장 완료” 표시를 완료 장벽으로 오인해 너무 일찍 버전 수를 읽었다. Application의 saved 상태를 기다리도록 시험을 고쳤고 엔진 보호는 수정하지 않았다. 영수증 조합 시험의 다른 작업은 고정 sleep 대신 provider 응답 gate가 진입했음을 확인해 취소 순서를 고정했다. 모호한 alert 선택자도 범위를 좁혔다. 중간 실패는 로컬에 남기되 runtime 토큰이 있을 수 있는 원시 로그는 공유하지 않는다.

## 엔진과 증거의 범위

사용 엔진은 기존 진단 엔진의 **복사본**이다. rhwp 0.8.6 / commit `e8800c8def63449808a4092798442652ed460552`, WASM SHA-256 `0e81cdeb4a61de8dd12be6d79d3bf06a8c1153525883e4112f4dcb1257d68195`. 엔진을 다시 만들거나 기본 `frontend/public`을 교체하지 않았다. 앱 진단 번들은 최종 소스로 새로 만들고 HTTP로 제공된 index/JS/CSS 및 엔진 바이트를 로컬 산출물과 대조한다. `scripts/verify-served-build.mjs`의 결과를 요약 JSON에 포함한다.

정식 배포 guard는 그대로다. Windows no-opt 진단 코어의 성공은 최적화 배포 provenance의 완료를 뜻하지 않는다. 기본 배포 빌드의 현재 차단은 별도 기록한다. Docker/PostgreSQL/nginx 배포는 실행하지 않았다. built preview는 nginx 설정의 헤더를 사용하는 Vite 서버이며 nginx 실행 시험이 아니다.

| 증거 종류 | 이번 범위 |
|---|---|
| 모의 CLI / 모의 HTTP 모델 | 접수 설정·두 후보 계약·실패·순서·재시도 시험에 사용 |
| 실제 Windows 프로세스 | 합성 부모/자식의 물리 cleanup 2개 시험 |
| 실제 rhwp 엔진 | 실제 WASM의 적용 거부·바이트 보존·저장·재열기 시험 |
| 승인 클릭 | Playwright 자동 클릭. 사람 승인으로 계산하지 않음 |
| 실제 Codex/로컬 모델 호출 | 0회, 요청 범위에서 제외 |
| 실제 사람 승인·한컴·폐쇄망·PostgreSQL | 이번 검증 0회. 과거 증거와 합산하지 않음 |

## 장시간 기록·보존·다음 작업

기존 1시간(실제 비교 보류 3,600.191초, 120회 임대 갱신) 및 602초 재시작/만료 증거는 역사적 기록 그대로 보존한다. 이번에는 임대 종료 무효화·실행 수명/cleanup·승인 저장 확인 상태가 바뀌었으므로 **두 장시간 시나리오 모두 새 변경셋의 재검증 대상**이다. 600초 상수·30초 갱신/20초 자동 저장 주기 자체는 바꾸지 않았다. 짧은 회귀의 통과를 장시간 재검증 통과로 표현하지 않으며 기본 시험에 1시간 대기를 추가하지 않았다.

보존 확인: 시작 파일 182개 중 165개는 그대로이며, 변경된 17개는 위 승인 범위 소스·시험뿐이다. 기존 DB/blob 56개와 파일 목록, 과거 문서/증거 34개, 요구사항·bridge·lock·설정 예시 등 보호 대상 98개의 해시가 같다. 인증 파일을 수집/수정하지 않았고 사용자의 열린 앱/버퍼를 제어하지 않았다. 시험은 새 headless 브라우저, 새 포트, 새 SQLite/파일 디렉터리에서 수행했다. 최종 실행 해시는 전후 동일했다. 격리 실행 7회의 서비스 포트 49개를 마지막에 읽기 전용으로 다시 확인했으며 남은 리스너는 0개다.

다음 가장 작은 작업은 **격리 실행기의 602초 재시작/만료 시나리오를 이 최종 manifest에서 다시 실행하고 새 증거를 추가하는 것**이다. 실제 모델 서버 준비는 필요하지 않다. 1시간 비교·자동 저장 시나리오는 그 다음 재검증 대상이며, 실제 Codex 품질·사람 승인·한컴 결과는 별도의 후속 범위다.

직접 검토 자료는 비민감 문서·요약 JSON·선택 실행 소스의 `git diff HEAD`와 diff에 포함되지 않는 untracked 소스/시험을 별도로 제공한다. 현재 변경과 시작 상태 간 diff도 포함한다. 실문서·DB·인증 파일·runtime 토큰·원시 실패 로그·전체 `.cache`는 패키지에서 제외한다. Git index를 조작하거나 원격에 푸시할 필요가 없다.
