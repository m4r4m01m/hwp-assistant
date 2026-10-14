# 같은 문서의 늦은 생성 응답 보호 · 2026-10-06

이번 변경은 이전 생성 요청 A의 늦은 완료가 새 요청 B의 재확인 상태를 지우는 문제 한 건에 한정한다. 기본 AI `codex`, 선택 경로 `llm`, `context_used=false`는 그대로다. 승인 출력·영수증·저장 키·후속 편집 경로와 이전 실행 슬롯·설정 고정·ORM·권한·임대 구현은 수정하지 않았다.

## 시작 상태와 최종 소스

- HEAD: `main / 1e0538781e02ac85d095bb3c72bf844943c305b3`. 커밋·푸시·병합·원격 fetch/pull을 하지 않았다.
- 검토한 변경셋 `0a8bef6e932f590903e81ee60cfdf46fe9c3c7a59994711dae26024de8335843`의 실행 파일 90개가 시작 시 현재 파일과 모두 일치했다. Git tracked/untracked 목록을 따로 기록했다.
- 시작 파일 358개의 SHA-256을 기록했다. 기존 미커밋 파일을 포함해 문서/증거, data, 기본 public 엔진과 시험용 엔진 원본을 보호 대상으로 포함했다. 실제 수정 대상의 시작 사본도 별도로 보관했다.
- 최종 실행 변경셋: `46a208cd44a030f34fda0ad53032b8d0ae6178f68c82cda7717e12caa7d7dd09`. 실행 소스·시험·lock·스크립트 91개와 사용 엔진 7개를 [새 manifest](evidence/rewrite-late-response-manifest-2026-10-06.json)로 고정했다. 문서는 실행 ID에 포함하지 않고 검토 패키지에서 별도로 해시한다.

| 파일 | 이번 변경 |
|---|---|
| `frontend/src/main.tsx` | 기존 `frozen` 객체와 활성 ref의 identity를 확인한 뒤에만 성공/오류/취소 완료가 상태를 변경 |
| `frontend/tests/local-rewrite.spec.ts` | 같은 문서·같은 대화에서 패널 재마운트 및 늦은 성공/4xx 응답을 각각 재현하는 E2E 2개 추가 |
| `scripts/rewrite-request.test.mjs` | 실제 TSX 콜백과 실제 `ApiError`/`jsonRequest` 코드를 추출해 모의 API/ref로 순서를 고정하는 시험 8개 추가 |

세 실행 파일의 최종 SHA-256:

| 파일 | SHA-256 |
|---|---|
| `frontend/src/main.tsx` | `25210589a78c67d42f62bbdc8a0c6a16a325a6d2825c7c98d9432c3e68acfb30` |
| `frontend/tests/local-rewrite.spec.ts` | `012f064af86d534a0f87548d661ca1b6b7fc679d4d4c3e2736626826a8767127` |
| `scripts/rewrite-request.test.mjs` | `acb2c83ecc2a1dfc1e32ca1e4be69542fca5a4c7f7644c690d18453f8ef972ae` |

`rewrite.tsx`의 busy는 패널을 닫고 다시 열면 새 컴포넌트 상태가 된다. `collaboration.tsx`의 선택된 대화/`RewritePanel` 재마운트도 확인했다. Editor에 남은 요청 ref의 수명을 busy 버튼만으로 보호할 수 없으므로, 두 파일의 UI 구조를 바꾸지 않고 Editor의 응답 처리에 일치 검사를 넣었다. 새 상태 관리 체계나 요청 큐는 추가하지 않았다.

## 수정 전 재현과 수정

실제 TSX의 `requestRewrite`/`cancelRewrite`를 TypeScript AST로 찾아 실행했다. 콜백을 시험 파일에 복사한 별도 구현은 사용하지 않았다. API 응답은 제어 가능한 Promise이고 ref·문단 스냅샷은 합성 값이다. 이 결과 자체는 브라우저·엔진·모델·문서 유실 시험이 아니다.

수정 전 콜백 시험은 **5 passed / 3 failed**였다. A 성공과 4xx에서 B ref가 null이 됐다. 네트워크 실패에서는 B ref가 유지됐지만, 이미 중단한 A에 대해서도 “요청 원문과 ID를 보관했다”는 재확인 안내를 붙였다. 후자는 상태 삭제 결함과 구분한다. 늦은 취소 3종은 기존 ID 검사로도 B를 보존했고, 활성 요청의 정상 4xx 복구 2종도 통과했다.

수정 내용은 다음과 같다.

1. 성공 응답은 `rewriteRequest.current === frozen`인 경우에만 ref를 비운다.
2. catch 진입 시 다른 요청이 활성화됐으면 원래 오류를 그대로 전달한다. B ref, `reviewHold.current`, `holdingReview`에 손대지 않고 A용 재확인 안내도 덧붙이지 않는다. 여전히 활성인 요청만 기존 4xx 해제/이전 보류 상태 복원 또는 네트워크 실패 시 고정 요청 보관을 수행한다.
3. 취소는 호출 전에 ref를 잡아 두고, 완료 시 요청 ID와 동일 객체가 여전히 활성인지 함께 확인한다. 늦은 취소 실패는 기존처럼 상태를 변경하지 않는다.

승인 처리·영수증 전송·저장 재확인 코드는 변경하지 않았다. 기존 요청 ID·스냅샷·지시·기준 버전을 그대로 사용하므로, 새로운 세대 카운터나 상태 객체는 필요하지 않았다.

## 같은 문서 E2E의 장벽

기존 `local-rewrite.spec.ts`와 `test-isolated.ps1`의 실제 rhwp/합성 HTTP 모델 환경을 재사용했다. backend fixture, DB ID/상태, 실제 모델을 조작하지 않았다. 기존 모델 gate와 정상 API만 사용했다.

| 순서 | 제어와 관찰 |
|---|---|
| A 접수 | 생성 POST는 실제 합성 서버에 전달하고, 접수 응답만 Playwright route 장벽에서 보류 |
| A 모델 gate | `/proof`에서 A 호출이 gate에 들어왔음을 확인하고 실제 작업 API의 running을 확인 |
| 패널 재마운트 | 같은 문서를 유지한 채 대화 패널을 닫고 다시 열어 같은 대화를 선택 |
| A 논리 중단 | UI 중단 버튼 → 실제 API cancelled 확인. 모델 gate 해제 후에도 A의 생성 POST 응답은 계속 보류 |
| B 접수 | 새 지시와 현재 미저장 문단으로 생성. B의 HTTP 응답은 별도 장벽에서 보류. 정상 모의 결과 게시로 ready 확인 |
| 늦은 A 반환 | B가 활성인 동안 A의 실제 성공 응답 또는 합성 409를 반환. 전달 완료 및 브라우저 continuation 이후 보류 UI와 B 지시/스냅샷 확인 |
| B 응답 유실 | 서버 접수가 확인된 B 응답을 abort. 같은 B 지시로 재확인 |
| 재확인 결과 | B ID·전체 POST 입력·기준 버전 동일, 작업 2개·모의 모델 호출 2회 유지, B 생성 호출 1회 |
| 문서 보존 | 버전 1개·Application 0개 유지, 원본 SHA-256 동일, 대상/비대상 미저장 문장을 포함한 전체 export 바이트 동일, dirty 및 비교 보류 표시 유지 |

모든 HTTP 장벽과 모델 gate는 finally에서 해제하고 route 처리 종료를 기다린다. 모델 응답을 정상 게시한 것은 버튼의 running 조건을 충족하기 위한 실제 공개 API 경로이며 DB 값을 강제로 바꾸지 않았다. 같은 문서 시험을 기존 문서 A→B 전환 시험으로 대체하지 않았다.

수정 전 브라우저 성공 사례에서 B 재확인 ID가 바뀌는 것을 재현했다. 이어진 4xx 사례는 Windows worker가 코드 `3221226505`로 시작 직후 종료돼 제품 판정에 사용하지 않았다. 4xx만 별도 실행했을 때는 늦은 A 응답 뒤 비교/자동 저장 보류 UI가 사라지는 것을 재현했다. 수정 후 동일 두 사례는 모두 통과했다. 원시 실패 로그/trace는 로컬에만 보관하며 공유 자료에는 비민감 요약만 넣는다.

## 최종 변경셋의 회귀

실행 루트: `.cache/rewrite-late-response-2026-10-06/`. 마지막 실행 소스 수정 후 manifest를 만든 뒤 아래 검증을 수행했고, 검증 뒤에도 같은 해시임을 확인했다. 최종 결과는 [요약 JSON](evidence/rewrite-late-response-2026-10-06.json)에서 개별 장벽 증거·파일 해시와 연결한다.

| 명령 | 결과 |
|---|---|
| `node --test scripts/rewrite-request.test.mjs` | 8 passed / 0 failed / 0 skipped |
| `.venv/Scripts/python -m pytest -q --junitxml=.cache/rewrite-late-response-2026-10-06/final-pytest.xml` | 173 passed / 0 failed / 0 skipped, warning 1, 39.01초. 기존 안전 경계 35개 수집·통과 |
| `node frontend/node_modules/typescript/bin/tsc --noEmit --project frontend/tsconfig.json` | exit 0 |
| `node --test scripts/engine-build.test.mjs` | 11 passed / 0 failed / 0 skipped |
| `powershell -NoProfile -File scripts/test-isolated.ps1 -EnginePublic .cache/engine-release-2026-09-27/public -OutDir .cache/rewrite-late-response-2026-10-06/final-source-e2e` | 수집 30, 29 passed / 0 failed / 1 opt-in skipped, 2.0분. 인증 시험 통과 |
| 같은 명령에 `-OutDir .cache/rewrite-late-response-2026-10-06/final-built-e2e -BuiltApp` | 수집 18, 17 passed / 0 failed / 1 opt-in skipped, 1.4분 |

일반 source의 세 서버에서 엔진 관련 7개 HTTP 파일이 의도한 복사본과 같았고, 새 built의 세 서버에서는 앱 index/JS/CSS를 포함한 10개가 일치했다. 새 앱 JS는 `assets/index-CX8O9ijw.js`다. 파일별 실제 제공 해시는 요약 JSON에 있다. 엔진은 계속 rhwp 0.8.6, commit `e8800c8def63449808a4092798442652ed460552`, WASM SHA-256 `0e81cdeb4a61de8dd12be6d79d3bf06a8c1153525883e4112f4dcb1257d68195`인 기존 진단 엔진의 복사본을 사용했다.

기존 페이지 수 거부 복구, 승인·영수증·저장 응답 유실, 문서 전환 시험은 삭제/변경하지 않았다. source 시험은 인증 서버와 URL을 포함해 실행한다. built 시험은 현재 소스의 새 진단 앱 번들을 별도 디렉터리에 생성하고 실제 HTTP의 index/JS/CSS 및 엔진 해시를 대조한다. 기존 번들 결과를 재사용하거나 이전 시험 수를 합산하지 않는다. 기본 배포 엔진·빌드 guard를 바꾸지 않았으며 진단 빌드를 정식 배포 완료로 해석하지 않는다.

## 보존과 남은 범위

시작 파일 358개 중 수정한 기존 파일은 위 두 개뿐이다. 기존 DB/blob 56개, 문서/증거 38개, 기본 public 엔진 86개, 시험용 엔진 원본 82개를 포함한 나머지 356개는 동일 해시다. 기존 Git 미커밋 변경을 삭제하거나 초기화하지 않았다. 인증 설정을 수정하지 않았고, 실제 Codex 로그인/모델 호출이나 사용자가 열어 둔 브라우저/편집 버퍼 조작은 하지 않았다. 새 격리 DB·포트·headless 브라우저만 사용했다.

실패 재현을 포함한 격리 실행 5회의 서비스 포트 35개를 마지막에 다시 확인했으며 남은 리스너는 0개다. 시험 자료는 지우지 않고 로컬에 보관했다. 공유 검토 ZIP에는 이번 변경 diff, 선택 소스의 누적 `git diff HEAD`, 미추적 시험 파일 원문, 새 보고서·JSON·manifest만 포함하며 실문서·DB·인증 파일·runtime 토큰·원시 로그는 넣지 않는다.

콜백 시험은 모의 API/ref, E2E는 실제 rhwp+모의 HTTP 모델이다. 이번 새 E2E는 후보 생성/중단/재확인만 수행하며 사람 승인 사례가 아니다. 전체 기존 회귀의 승인 클릭도 자동 시험이다. 실제 모델 호출과 실제 사람 승인 증거는 이번에 0회다.

602초 재시작/만료 및 1시간 비교 시험은 **이 새 변경셋의 후속 검증**으로 남긴다. 짧은 회귀나 보류 표시 확인을 장시간 타이머 검증 성공으로 표시하지 않는다.
