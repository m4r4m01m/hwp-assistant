# 보고서 프로필 선택과 생성 단계 목차 · 2026-10-12

TM-10의 목표는 사용자가 검증된 보고서 프로필을 선택하고, 새 한글 초안의 실제 쪽수 목차를 준비한 뒤 편집·명시적 서버 저장으로 이어지는 흐름이다. 현재 구현과 독립 감사를 진행 중이며 이 문서의 최종 시험 결과는 아직 확정하지 않았다. 등록한 임의 마켓 HWPX를 보고서 프로필로 해석하거나 모든 양식의 자동 제목 인식을 완료로 표시하지 않는다.

[2026-10-11 목차 구현](toc-2026-10-11.md)·[당시 WBS](wbs-progress-toc-2026-10-11.md)·기존 manifest/검토 ZIP은 변경 당시의 증거로 보존한다. 이번 진행은 [새 WBS](wbs-progress-report-toc-2026-10-12.md)에 기록한다.

## 출발점과 지원 범위

출발 소스 집합은 `dc20addeba04c978a4dcbcfdd5ab3fc651140a9c260b5646f8a068665acaaa2f`이고 HEAD는 `1e0538781e02ac85d095bb3c72bf844943c305b3`다. 감사 시작 시 [기존 최종 manifest](evidence/toc-manifest-final-2026-10-11.json)의 145개 소스·lock·시험 경로가 현재 파일 해시와 같았다. 후속 구현은 미커밋·미추적 변경으로 이어가며 HEAD만으로 변경셋을 식별하지 않는다.

이번 선택 범위는 hash-pinned `reference.hwpx`·`profile.json`과 기존 `document_standard.py`를 사용하는 검증된 표준 보고서 프로필이다. 임의 템플릿 업로드를 새 보고서 profile로 변환하거나 모델·유료 서비스로 서식을 자동 대체하는 경로는 제외한다. 프로필 선택 ID·리비전·실제 값·입력 해시를 새 요청에서 고정하는 계약을 검증해야 한다.

현재 catalog의 세 선택지는 `standard-business`(표준 업무보고), `executive-decision`(경영진 의사결정 보고), `response-plan`(대응·추진계획 보고)이다. 세 프로필은 같은 고정 한글 서식을 사용하며 목적·작성 기준만 달리한다. catalog에는 ID·리비전·실제 이름/설명/작성 기준과 표준 ID·reference/profile SHA, 이 값 전체의 profile SHA를 반환한다. 신규 요청의 `template` 전체값을 현재 catalog와 대조하고 그대로 `ReportTask.input`과 입력 해시에 고정한다. 생성 지시는 이 실제 작성 기준을 사용한다.

기존 요청 ID 재확인은 현재 catalog를 다시 해석하지 않고 저장된 입력 해시를 대조한다. 프로필을 보내지 않은 과거 입력은 `exclude_none`으로 원래 `id`·`prompt`·`research` 세 필드 해시를 유지한다. 같은 ID에 다른 프로필값을 제출하거나 신규 요청에서 stale/위조 선택을 제출하면 409로 거부한다.

| 새 preview의 구현 범위 | 현재 코드 계약 |
|---|---|
| 정책 | `template`이 있는 신규 preview에 `toc_policy=report-native-toc-v1`. 정책 없는 과거 frozen preview는 이전 경로 유지 |
| 자동 제목 | 고정 plan의 `kind=heading`: 핵심 요약·각 절·있으면 출처 및 확인 사항. 모두 1계층. 문서 제목·표/그림 캡션·본문 소제목은 제외 |
| ID | preview UUID와 exact command ID에서 제목 UUIDv5, preview UUID에서 목차 UUIDv5. 엔진 숫자 문단 ID는 영속 식별자가 아님 |
| 배치 | 문서 제목 뒤 관리 목차 추가, 같은 native 문서의 실제 물리 쪽수 사용. 최대 3회 수렴 |
| 갱신 | 현재 미저장 export를 고정해 기존 목차/제목 UUID 전체 순서를 유지. 수동 목차 손상·삭제/중복/미지원 제목이면 거부 |
| 표준 고정 | 신규 preview·최초 render·TOC 조합/검증에서 입력의 표준 ID/ref/profile SHA와 현재 자산 대조. 기존 frozen 결과 재확인은 재배치하지 않음 |
| 중단 | 취소 ID와 기준 layout SHA를 보존. 128개 중단 기록 한도 뒤에는 새 준비를 거부하고 현재 출력 유지 |

TM-09 일반 편집기의 수동 선택 2계층 목차와 이번 보고서 생성의 자동 1계층 목차를 구분한다. 표·흐름도·본문 강조는 기존 rich layout의 내용을 보존하는 대상이며 새 이미지 생성 모델을 호출하지 않는다.

## 구현·감사 경계

1. Codex 생성 결과가 완료·검증된 `ReportDraft`여야 HWPX에 들어간다. 부분 JSON과 텍스트 델타는 기존 표시용 경로를 유지한다.
2. 별도 preview에서 기존 exact commands·영수증·내용·페이지 수 보호를 통과하고 rich layout을 완료한 뒤 제목 UUID를 연결한다. 새 AI 글을 목차 단계에서 생성하지 않는다.
3. 일반 문서의 임대 API를 우회하지 않는다. 보고서 owner·상태·preview ID·layout SHA 경계 안에서 TM-09의 순수 TOC 조합·보존 검증과 같은 native 페이지 증거를 재사용한다.
4. stable live/staging host를 유지한다. 현재 사용자 초안을 준비 결과의 load 대상으로 사용하지 않고, 최대 3회 실제 배치 수렴·검증 뒤 owner·native epoch/revision·SDK 상태·전체 export 바이트를 대조해 채택한다.
5. 최초 `ready` 전에는 실제 제목 페이지와 목차 숫자가 같아야 한다. 이후 수동 편집에는 stale 표시와 명시적 목차 갱신을 제공하고 비대상 편집을 유지한다.
6. 응답 유실은 단계별 고정 입력·ID·출력으로 재확인한다. `pendingSave`·`savingBody` 동안 layout·템플릿 변경을 막고, 저장 commit 뒤 응답 유실에도 같은 preview/layout/output/save ID를 유지한다.
7. `초안 확정·서버 저장`은 눈앞의 수동 편집까지 승인하는 기존 의미를 유지한다. 미리보기·목차 준비만으로 문서·버전·blob을 게시하지 않으며 실제 영속화·DB commit을 확인한 뒤 저장 완료를 표시한다.

toc 정책이 없는 과거 frozen preview는 이전 배치 경로를 유지해야 한다. 기존 report 입력에 없던 profile 기본값을 추가했다는 이유로 과거 동일 ID 재확인을 다른 입력으로 처리하지 않는다. 제목 UUID 삭제·중복·미지원 전환, 관리 목차의 수동 손상 및 스타일 슬롯 부족은 현재 내용을 보존하며 거부해야 한다.

## 사용자 흐름과 API 계약

현재 구현의 사용자 흐름은 `보고서 작성` → `보고서 템플릿` 선택 → 작성 요청 입력 → Codex 초안 생성 → exact 내용 적용·rich 서식 적용 → 실제 목차 쪽수 검증 → 편집 가능한 초안이다. 생성 내용은 기존 실제 text delta로 표시하며 목차 단계에서 새 모델을 호출하지 않는다. 세 프로필은 같은 표준 양식을 사용한다. 과거 목록의 frozen preview에 새로운 정책을 소급 적용하지 않는다.

목차 검증 완료 뒤 본문을 편집하면 `갱신 필요`를 표시한다. `목차 쪽수 갱신`은 현재 미저장 내용을 별도 stage에 고정하고 검증한 결과만 채택한다. 본문·제목 변화로 수렴하지 않거나 보존 조건이 깨지면 현재 초안과 후속 편집을 유지한다. 수동 편집의 최종 출력은 기존 `초안 확정·서버 저장` 버튼으로 승인한다. 미저장 후속 편집이 있는 저장 재확인은 먼저 고정했던 출력만 서버에 게시하며, 더 늦은 편집은 현재 초안에 남아 다운로드할 수 있다.

접수·목차 준비·검증·중단·저장 응답이 유실되면 같은 입력을 재확인한다. 접수 재확인 중 프로필 선택을 잠그며, 미해결 저장 중에는 목차 변경을 잠근다. 취소 결과가 확인되지 않으면 기존 요청 ID를 보관하고 같은 취소만 재확인한다. 취소/실패 처리에서 현재 live 초안을 서버 결과로 다시 load하지 않는다.

| 경로 | 고정 입력과 확인 경계 |
|---|---|
| `GET /api/report-templates` | 현재 검증된 프로필 실제값과 SHA 반환. 늦은 catalog 응답은 현재 선택 상태를 덮지 않음 |
| `POST /api/report-tasks` | ID·prompt·research·선택한 template 전체값. 동일 ID에는 동일 입력 해시만 허용 |
| `POST /api/report-tasks/{id}/preview/toc-prepare` | request/preview ID·현재 layout SHA·현재 미저장 source 바이트/SHA. 제목/표준/원본을 고정하고 첫 pass 반환 |
| `…/toc-preview` | 같은 request/preview/base와 다음 pass 1~3, 직전 native 측정 페이지 전체. 입력이 다른 재확인은 거부 |
| `…/toc-verify` | 같은 pass의 실제 엔진 export와 같은 iframe native layout evidence. 수렴한 출력/SHA/목차 기록만 원자적으로 게시 |
| `…/toc-cancel` | 같은 request/preview/base. 완료 이후 중단은 성공 기록을 지우지 않고 최신 preview 메타데이터를 반환 |
| `…/save` | 기존 save ID·preview ID·최종 layout SHA·승인한 실제 출력. commit 후 재확인은 같은 문서/버전을 반환 |

신규 목차 경로는 항상 현재 보고서 owner·ready 상태·draft/preview/layout 기준과 미승인 상태를 다시 검사한다. 이 경로는 일반 문서를 게시하는 기능이 아니며 보고서 승인 저장 전에는 문서/버전이 생성되지 않는다. 확인용 서비스 반영과 최종 사용자 확인 순서는 아래 최종 결과가 확정된 뒤 기록한다.

## 완료 조건과 시험 구분

| 중간 소스의 실행 | 결과와 해석 |
|---|---|
| 프로필 계약 R1 | 새 fixture를 3개로 unpack한 오류와 `secured` import 누락으로 제품 시험 전 fixture error 9개. Windows typing access violation 진단도 출력됨. 최종 제품 결과로 계산하지 않음 |
| `.cache/report-template-toc-2026-10-12/profiles-r2.log` | fixture/import 보완 후 프로필 계약 9 passed/0 failed/0 skipped, 1.84초. 기존 Starlette 경고 1개·Windows native 진단 없음. 새 최종 변경셋의 전체 pytest에 합산하지 않음 |
| 프로필 계약 R3 | 실제 stdio 합성 fixture의 turn 입력에서 frozen 프로필값 전달을 추가 확인하여 10 passed/0 failed/0 skipped, 2.47초, exit 0. 기존 Starlette 경고 1개와 Windows importlib/access violation 진단을 함께 보존. 진단 없는 최종 전체 실행으로 계산하지 않음 |
| `.cache/report-toc-targeted-r5-2026-10-12.log` | 보고서 TOC 39개+기존 preview/rich 24개, 일괄 63 passed/0 failed/0 skipped, 24.03초, exit 0. 표준 값의 계산 전후 대조와 계산 중 변경 12개 조건 포함. 최종 전체 소스 회귀와 별도 |
| 신규 프로필/TOC source E2E R1 | 프로필 1개 통과와 실제 native 목차·표 보존 확인 뒤, 표준 제목 `< 목 차 >`를 `목차`로 기대한 시험 1개 실패. 제품 수정 없이 기대값만 보완. 최종 성공 결과로 계산하지 않음 |
| `.cache/report-toc-source-targeted-r2-2026-10-12` | 프로필 1개+신규 TOC 6개, 일괄 7 passed/0 failed/0 skipped, Playwright 1.2분, script exit 0. 실제 rhwp·모의 보고서이며 새 실제 모델 호출 0회. `remaining_listeners=[]`. source manifest 확정 전 중간 결과 |

독립 정적 검토에서 단일 취소 ID가 A 취소→B 취소 뒤 A 재접수를 허용하는 경계와, 요청 접수 뒤 표준 자산 변경 시 첫 preview가 입력의 표준 SHA와 달라지는 경계를 찾았다. 취소 journal·상한과 신규 preview/render·TOC 표준 SHA guard 및 위 R5의 제어 시험을 연결했다. 프런트의 실패 후 취소 응답이 늦게 끝나 같은 보고서의 새 작업 표시를 덮는 경계도 operation/task identity의 재검사로 보완한 코드를 읽었다. 실제 엔진 R2는 단계별 응답 유실·commit 뒤 후속 편집·저장 응답 유실·취소 응답 유실/다른 preview·legacy 보존을 확인했으며 동일 최종 변경셋의 전체 회귀는 별도로 진행한다.

계산 후 상태 fence 시험은 두 번째 repository 조회에서 바뀐 상태를 관측하도록 제어해 409와 execution 미게시를 확인한다. SQLite가 writer 경쟁을 억제하는 환경에서 실제 동시 DB writer 시험을 통과했다고 표현하지 않는다. 백엔드 시험의 페이지 evidence는 합성 값이며 같은 iframe native 쪽수 확인은 실제 엔진 E2E로 구분한다.

| 검증 | 이번 최종 결과 |
|---|---|
| 새 최종 source·lock·시험 manifest 및 이전 manifest 대조 | 수집 전 |
| 전체 pytest | 실행 전 |
| TypeScript | 실행 전 |
| 엔진 보호·callback/저장 시험 | 실행 전 |
| 기본 배포 provenance 가드 | 최종 확인 전. 기존 보호를 낮추지 않음 |
| 일반·인증 source 전체 E2E | 실행 전 |
| 현재 소스로 만든 별도 진단 앱 bundle·관련 E2E | 실행 전 |
| 원본·과거 blob·표준 자산·두 DB·기존 사용자 변경 보존 | 최종 대조 전 |
| 확인용 서비스·실제 served 파일 대조 | 반영 전 |

새 기능의 실제 엔진 시험에는 긴 제목·두 자리 쪽수·표/흐름도·강조 보존, 최초 ready 이전 실제 수렴, 수동 편집 뒤 명시적 갱신, 늦은 응답·닫기·staging 정리, 프로필 입력 고정 및 응답 유실 재시도를 포함한다. 같은 최종 변경셋의 실행별 수집·passed/failed/skipped와 command를 기록하며 과거 시험 수와 합산하지 않는다.

기존 TM-09의 371 pytest·37 node·source 46 passed/10 skipped·별도 bundle 17 passed는 2026-10-11의 역사적 증거다. 이번 TM-10의 결과로 재분류하지 않는다. 기존 602초·1시간 시험도 후속 소스에서 새로 실행한 장시간 결과로 계산하지 않는다.

이번 구현 검증의 새 실제 모델 호출은 0회로 유지한다. 모의 보고서 입력·실제 rhwp·자동 클릭·실제 사람 승인·한컴 검수를 구분한다. 실제 사람 승인과 한컴 한글 왕복은 별도 수동 검증이며 증거가 없으면 완료로 표시하지 않는다.

## 직접 검토와 후속

최종 검토 자료에는 새 비민감 문서·요약 JSON·개별 해시 manifest·git diff HEAD와 미추적 신규 핵심 소스/시험을 분리해 포함한다. 실문서·DB·token·인증 파일·전체 `.cache`·브라우저 trace·엔진 binary는 제공 대상에서 제외한다. 기존 원본과 과거 버전, 이전 날짜별 증거를 덮어쓰지 않고 commit/push/merge는 하지 않는다.

확인용 서비스의 새 앱 바이트 반영은 아직 최종 확인 전이다. 임의 마켓 HWPX의 보고서 프로필 변환·템플릿 편집/조각 이식·정상 배포 provenance·실제 격리/한컴 검수는 후속 범위다.
