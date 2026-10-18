# 기존 문서의 표준 블록 삽입 · 2026-10-11

템플릿 마켓 1차 이후 TM-07과 TM-08의 첫 범위다. 기존 문서의 본문 문단 뒤에 기본 기준서의 제목·요약 문단·담당/기한 조치표를 추가한다. 등록한 임의 HWPX에서 일부를 잘라 붙이는 기능과 보고서 생성기의 템플릿 선택은 이번 범위가 아니다.

## 사용자 흐름

1. 저장된 문서를 편집기로 열고 본문 문단에 커서를 놓는다.
2. `표준 블록 삽입`에서 블록과 필요한 입력값을 선택한다.
3. 현재 미저장 HWPX를 고정해 별도 엔진에서 삽입 결과를 렌더링한다.
4. 원문·기존 표·서식·리소스 보존 검증 후 미리보기를 확인한다.
5. `확인한 블록 삽입`을 누르면 현재 문서·기준 버전·임대·선택 위치를 다시 검사한 뒤 편집기로 반영한다.
6. 기존 자동 저장 또는 수동 저장으로 새 버전을 게시한다. 미리보기와 삽입 자체는 서버 저장 성공이 아니다.

기본 블록의 변수는 선언된 입력 칸에서만 사용한다. 문서 전체의 같은 문자열이나 `{{변수}}`를 찾아 바꾸지 않는다. 사용자가 직접 입력한 글이며 모델 호출·AI 승인·컨텍스트 설정을 추가하지 않는다.

## 구조와 보호 경계

기존 SDK의 exact command는 단일 본문 문단 교체 계약이다. 구조 삽입에 재사용하거나 기존 페이지 수 보호를 낮추지 않는다. 엔진·읽기 전용 bridge·기본 AI 제공자는 그대로 유지한다.

서버는 현재 편집 권한·임대·기준 버전을 검사하고 받은 미저장 HWPX에 새 블록만 조합한다. 기준서의 글꼴·테두리·문단·글자·탭·스타일 ID를 새 ID에 매핑하며 기존 정의와 문단을 수정하지 않는다. 다른 ZIP 항목과 BinData는 보존한다. 원본 및 과거 blob에 쓰지 않고 문서나 버전을 만들지 않는다.

클라이언트는 기존 편집기를 계속 보관한 상태로 별도 staging 편집기에만 결과를 로드한다. 실제 엔진의 export를 서버가 재검사한다. 적용 직전 권한 검사 이후에는 기존 편집기와 고정 입력의 identity·epoch·변경 순서·선택·원문 및 전체 export를 동기적으로 대조하고 이미 준비한 편집기를 채택한다. 이 과정에서 저장 기준 바이트를 삽입 결과로 바꾸지 않는다. 기존 frozen 저장 요청·키·승인 출력도 바꾸지 않는다.

준비 중 직접 편집한 내용은 유지하며, 고정 입력이 오래되었으면 삽입을 거부한다. 네트워크 실패는 같은 입력과 요청 ID로 다시 확인한다. 취소·닫기·늦은 응답은 해당 준비 작업의 임시 편집기와 보류만 정리한다. AI 비교·승인 적용·미해결 저장과 겹치지 않도록 독립적인 블록 보류를 사용한다.

## 지원 범위

| 항목 | 이번 범위 |
|---|---|
| 제목 | 고정 기준서의 파란 상하선·제목 글꼴 |
| 요약 문단 | □, 15pt, 내어쓰기, 괄호 말머리 볼드 |
| 조치표 | 머리글과 조치 두 행, 편집 가능한 셀, 회색 머리글·검정 선·좌우 바깥선 생략 |
| 삽입 위치 | 단일 구역·단일 단의 일반 본문 문단 뒤 |
| 입력 한도 | 10MB 이하 HWPX, 선언된 유한 길이 변수 |
| 복합 위치 | 표 셀·개체 내부·혼합 서식 문단은 삽입 위치로 거부. 필드 포함 문서는 이번 범위에서 전체 거부 |
| 삽입 취소 | 결과가 그대로이고 아직 후속 편집·저장을 시작하지 않았다면 `방금 삽입 취소`로 이전 버퍼 복귀. 이후에는 문서의 일반 편집·버전 복구 사용 |
| 임의 등록 양식의 조각 | 미지원. 스타일·이미지·필드의 별도 호환 검증 필요 |
| 자동 목차 | TM-09 후속. 제목·실제 렌더링 쪽수 참조 및 편집 후 갱신 정책 필요 |
| 한컴·사람 검수 | 자동 시험과 별도로 기록 |

제목·본문·표의 기준 출처는 `docs/document-writing-standard.md`, `backend/app/document_standard.py`, 해시 고정 `backend/app/assets/document-standard/reference.hwpx`와 `profile.json`이다. 이전 보고서와 frozen 미리보기의 배치 정책은 변경하지 않는다.

## 검증 기록

실제 엔진의 재내보내기에서 재현한 차이만 보완했다. 블록 전용 XML 직렬화가 기존 번호 스타일의 고정 접두사를 명시하며 전역 ElementTree 등록 상태에 의존하지 않는다. 새로 복사하는 글자 스타일의 비활성 효과와 무채움 색상만 엔진이 방출하는 값·순서로 완성한다. OUTLINE 0의 구역 번호 참조는 구역 설정과 번호 정의를 함께 검사한다. XML text/tail의 CR은 숫자 참조로 보존한다. 기존 서식과 보존 검사의 의미는 유지했다.

새 시험은 입력 계약·권한·임대·기준 버전·변조 거부 39개와 실제 rhwp 브라우저 4개다. 브라우저 시험은 제목·요약·조치표의 실제 렌더링과 기존 표/미저장 내용, 프레임 유지·정확한 삽입 취소·저장 재열기, 준비 및 저장 응답 유실의 고정 재시도/후속 편집, 편집 후 거부와 닫힌 준비의 늦은 완료, 실제 인증·권한 회수/reader 보호를 검증한다. 수동 저장 시나리오의 20초 tick은 시험에서 제어하고 준비 중 직접 호출해 자동 저장 보류를 확인한다. SDK 타이머나 제품 자동 저장은 바꾸지 않았다.

최종 소스는 `f38b9066e7356a02e9be887fd51564ddd839e3f5483397b7eccdc538648283ba`다. HEAD는 `1e0538781e02ac85d095bb3c72bf844943c305b3`, 미커밋 소스·lock·시험 스크립트 139개와 사용 엔진 83개의 개별 해시를 [최종 manifest](evidence/template-blocks-manifest-final-2026-10-11.json)에 남겼다. 이전 마켓 manifest 이후 변경은 9개 파일이며 엔진·bridge·표준 원본/profile은 동일하다.

| 같은 최종 변경셋의 명령 | 결과 |
|---|---|
| `.venv/Scripts/python -m pytest -q --basetemp=.cache/template-blocks-2026-10-11/pytest-final-v2` | 326 passed, 0 failed, 0 skipped, 152.12초. 기존 Starlette/AnyIO deprecation 1개, access violation 진단 없음 |
| `node frontend/node_modules/typescript/bin/tsc --noEmit --project frontend/tsconfig.json` | 통과 |
| `node --test scripts/engine-build.test.mjs` | 11 passed, 0 failed/skipped |
| `cd frontend; npm run build` | exit 1, 기존 pinned core/bundle provenance 누락으로 차단. 기본 보호를 낮추지 않음 |
| 아래 source E2E R1 | 52 collected: 41 passed, 1 failed, 10 skipped, 4.2분. 해당 시험 실행 전 worker 종료(code 3221226505), 원인 미확정. 실패 기록 보존 |
| 아래 source E2E R2 | 같은 소스 단독 전체 재실행. 52 collected: 42 passed, 0 failed, 10 skipped, 3.3분, exit 0 |
| 아래 새 진단 bundle E2E | 13 passed, 0 failed/skipped, 1.7분. 현재 소스로 새 번들을 만들고 served bytes 대조 |

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-isolated.ps1 -EnginePublic .cache/engine-release-2026-09-27/public -OutDir .cache/template-blocks-source-final-2026-10-11
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-isolated.ps1 -EnginePublic .cache/engine-release-2026-09-27/public -OutDir .cache/template-blocks-source-final-r2-2026-10-11
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-isolated.ps1 -EnginePublic .cache/engine-release-2026-09-27/public -BuiltApp -ReportMock -TestPattern 'block insert|template market|REAL HWPX template|MOCK report' -OutDir .cache/template-blocks-built-final-2026-10-11
```

이전 변경셋 `baf34b...`의 324개 전체 pytest와 4개 블록 E2E는 CR 보완 전 증거다. 이번 전체 실행에 합산하지 않는다. 초기 브라우저 실패는 삽입 불가 첫 문단을 선택한 시험 helper, 시작 시간 초과, 그리고 실제 prefix/새 스타일 기본값/투명색 차이로 구분해 `.cache/block-insert-source-targeted*`에 보존했다. 일부 targeted pytest 시작에 Windows access violation thread 진단이 있었지만 exit 0으로 완료됐으며, 최종 326개 전체 실행은 이 진단 없이 통과했다. 이전 602초/1시간 결과는 역사적 증거다. 이번 짧은 회귀로 새 장시간 검증을 주장하지 않는다.

시험은 합성 문서와 실제 자체 호스팅 rhwp, 모의 모델을 사용한다. 이번 작업의 실제 모델 호출과 사람 승인은 0회다. 한컴에서의 확인은 별도 사용자 검수다. 현재 일반 배포 provenance, 임의 양식 조각 이식, 혼합 서식/필드 삽입 위치, 자동 목차는 후속으로 남긴다.

source 회귀의 10개 skip은 장시간 시험 1개와 별도 모의/기록 보고서 환경 9개다. 별도 bundle 실행은 그중 모의 보고서 5개를 포함하며 실제 모델 시험으로 세지 않는다. R1 실패는 통과 수에 포함하거나 R2와 합산하지 않는다.

[합성 문서의 실제 화면](evidence/template-blocks-2026-10-11.png)과 최종 비민감 요약 JSON은 별도로 보관한다. 실문서·DB·인증·토큰·브라우저 trace·전체 `.cache`는 직접 검토용 제공 파일에 포함하지 않는다.

## 서비스와 보존 결과

검증 후 `scripts/start-user-review.ps1`로 [확인용 서비스](http://127.0.0.1:15180/)를 다시 띄웠다. 실행 디렉터리는 `.cache/user-review/runs/20261011-214955-f4e5bc5f`다. UI/API 200, 새 블록 3개, served JS/CSS가 위에서 시험한 새 진단 bundle과 바이트 단위로 같음을 확인했다. 기본 제공자는 Codex이며 시작 시 모델을 호출하지 않는다.

원본·과거 저장파일 69개, 기준서/profile, 엔진/bridge의 해시가 동일하다. 기존 두 DB의 행 정규화 해시와 개수를 대조했다(문서의 lease_token/lease_until만 제외). 확인용 문서 12개·버전 14개·기존 보고서 작업 7개와 이름/기준 버전/권한 등 영속 필드가 동일하다. 기존 소유 서비스만 종료했으며 격리 source R1/R2와 bundle 실행이 남긴 listener는 0개다. 사용자 브라우저에 입력하거나 새로고침하지 않았다. commit/push/merge는 하지 않았다.

사용자는 미저장 편집을 저장한 뒤 새로고침하고, 저장된 문서의 일반 본문 문단에 커서를 둔 다음 `블록 삽입 열기`로 확인할 수 있다. 입력 → 실제 미리보기 → `확인한 블록 삽입` → 문서 편집/저장 순서다. 아직 기존 필드·혼합 서식 문단·표 셀 안을 삽입 위치로 지원하지 않는다.

[최종 요약 JSON](evidence/template-blocks-2026-10-11.json)과 [코드 검토 ZIP](evidence/template-blocks-review-2026-10-11.zip)에 증거와 소스를 묶었다. ZIP에는 git diff HEAD와 미추적 신규 소스/시험을 따로 포함한다. 전체 `.cache`와 실문서·표준 원본 binary·DB·인증·토큰·엔진 binary는 제외하며 필요한 해시는 manifest로 제공한다.
