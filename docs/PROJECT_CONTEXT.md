# HWP Assistant 프로젝트 컨텍스트

## 대제목 구성·주석 중복·저장 후 편집 전환 — 2026-10-26

QA `codex/local-ui-qa-2026-10-22`, 구현 기준 HEAD `14e52b23fdf41ddf8effcd0a92f219e5d667469f`의 후속이다. 아래의 인쇄 가독성·경영진 문구 개선을 포함한 작업을 같은 QA 브랜치에 기록한다. 원격 main은 확인 당시 `635293e`로 전진했지만 이번 변경에 병합하거나 운영을 배포하지 않는다. CLI 보존 브랜치도 유지한다. [이번 검증 근거](evidence/report-outline-and-save-2026-10-26.json).

작성 요청에서 문서 양식과 독립적인 **대제목 구성**을 선택한다. 상황·대응 보고는 개요→현황→대응→향후 계획, 의사결정 보고는 검토 배경→대안 비교→권고안→승인 요청 사항이며 3~6개 제목을 직접 편집·재배열할 수 있다. 선택한 구성은 요청 해시·재확인·후속 검토에 고정하고 완료된 출력이 정확한 순서를 지켰는지 검증한다. 자동 구성과 과거 입력/미리보기는 유지한다. 새 주석 정책 v2는 같은 절의 이어진 본문에 같은 출처 설명을 한 번 표시하며 다른 절·표에서의 근접 주석은 유지한다.

저장 후 임시 미리보기를 입력 불가 상태로 계속 보여 주어 멈춘 것처럼 보이는 현상을 별도 실제 rhwp 화면에서 재현했다. 정상 저장 뒤에는 일반 문서 편집기로 바로 연결한다. 저장 요청 뒤 추가된 미저장 편집이 있으면 기존 복구 흐름을 유지한다. 사용자 원래 Chrome 창은 자동 제어 도구가 URL을 확인하지 못해 관측을 중단했으며 해당 창의 모든 멈춤 원인을 확인했다고 주장하지 않는다.

집중 백엔드 구성/입력 42건·주석 47건, 모의 UI 7건, TypeScript 및 별도 진단 프런트 빌드가 통과했다. 실제 Codex 1회로 정확한 4절 구성과 개요의 판단·제안/완료 구분을 확인했고 rhwp 49개 exact 명령·서식·5쪽 내보내기/재열기를 검증했다. 같은 지역 범위의 출처 설명 중복은 0개다. 새 내용이 이전보다 길어 마지막 쪽에 여백이 남으며, 5쪽/주석 9개를 동일 내용의 감소 효과로 주장하지 않는다. 별도 동일 초안 비교는 본문·표 보존, 주석 6→5개, 4쪽 유지였다. 실제 저장 전환 시험은 저장/임대 응답을 모의 처리해 원본 바이트와 재열기 바이트 일치·입력 리비전 증가를 확인했고 사용자 DB에는 쓰지 않았다.

작업이 없는지 확인한 뒤 정확한 PID·시작 시각이 일치하는 API만 재시작해 `15330`/`18330`에 적용했다. 원본 입력·초안·해시·승인과 사용자 문서는 보존했다. 화면·문서와 개인 런타임은 무시된 QA 캐시에만 둔다. 실제 사람 검수·원래 Chrome 상태의 직접 진단·모든 외부 근거의 독립 사실 검증·Hancom·전체 회귀·엔진 재빌드·운영 배포는 이번 검증 범위에 포함하지 않는다.

## 경영진 보고 내용 품질 — 2026-10-26

QA `codex/local-ui-qa-2026-10-22`, 기준 HEAD `14e52b23fdf41ddf8effcd0a92f219e5d667469f` 위의 미커밋 후속이다. 새 작성 지시를 결론·판단 우선으로 바꾸고, 별도 요약이 꺼져도 첫 본문에 핵심 답을 남긴다. 형식적인 표·도식·강조 수량과 글자 수 권장을 걷어내며 기술 세부사항은 독자의 결정에 필요한 영향·조치로 설명한다. 조사 범위·중요 조건은 유지하고 반복 설명·확인 공백을 정리한다. 스키마 검증 한도, 프로필·저장 표준·동결 초안은 변경하지 않았다.

새 reference 계획의 `reference-note-groups-v1`은 □와 하위 설명의 출처를 논점 끝에서 한 번 표시한다. 동일 출처 표는 `자료: 제목`으로 모으며 원래 셀별 인용 위치를 계획에 보존한다. 여러 출처를 쓰는 표의 개별 표식은 유지한다. [작성 기준](document-writing-standard.md), [검증 근거](evidence/report-executive-quality-2026-10-26.json).

집중 시험 고유 69건(생성 입력·동결 보존 24, 주석 45)이 통과했고 최종 문구 보완 후 세 프로필의 모의 stdio 3건을 재확인했다(중복 합산하지 않음). 같은 공개 요청·등록 표준으로 실제 Codex `gpt-6.1-sol`/medium을 2회 호출하고 실제 rhwp에서 각각 63개·60개 exact 명령, 서식, 내보내기·재열기를 확인했다. 1차는 기술 설명과 마지막 한 문단 문제가 남아 지시를 더 보완했다. 최종은 원본 6쪽→5쪽, 주석 20개/844자→13개/505자, 확인 사항 4개→2개이며 첫 본문에 핵심 판단이 나온다. 새로 조사한 내용·출처가 달라진 비교이므로 동일 내용의 배치 효과로 해석하지 않는다. 원본·등록 표준·서버 초안 입력/해시/승인은 불변이며 사용자 문서/DB에는 저장하지 않았다.

이 작업의 API만 안전하게 재시작해 `15330`/`18330`에 적용했고 HTTP 200을 확인했다. 검수 화면과 새 HWPX는 무시된 로컬 캐시에 보관한다. 기관별 최신 조치 완료 여부와 자료별 범위·시점은 사람 검수 우선 항목이며 모든 외부 자료의 독립 사실 검증 완료를 주장하지 않는다. 실제 사람 승인·Hancom·전체 회귀·엔진 재빌드·main/운영 배포·커밋/푸시는 수행하지 않았다.

## 보고서 인쇄 가독성 — 2026-10-25 후속

QA 브랜치 `codex/local-ui-qa-2026-10-22`, 기준 `14e52b23fdf41ddf8effcd0a92f219e5d667469f` 위의 로컬 변경이다. 새 reference 계획에서만 문단·표 아래 출처를 제목으로 표시하고 URL·발행일은 ReportDraft에 보존한다. 확인 사항도 사용 중인 문단 기호·들여쓰기를 따르며, 명시적인 문서·출처 문맥의 알려진 ID 범위를 지역 주석으로 연결한다. 새 표는 짧은 구분 열과 설명·식별자·윗첨자에 필요한 폭을 배분한다. 전체 표 폭·글꼴·저장한 정렬은 유지하고 과거 계획은 정책 표식으로 구분한다. 새 생성 지시와 스키마에는 출처 제목·주소·날짜 분리 및 확인 사항의 출처 토큰 규칙을 보완했다.

집중 생성/검토 25건, 주석/확인 사항 36건, 새 표 12건·기존 표 11건·rich/문단 간격/지역 주석 44건이 통과했다(그룹 간 지역 주석 시험은 중복되므로 합산하지 않는다). 일부 Windows sandbox 임시폴더/모의 subprocess 시험은 해당 경로만 호스트의 독립 임시폴더에서 재확인했다. 실제 rhwp에서는 같은 검수 초안·등록 표준을 새 비교본으로만 배치해 85개 exact 영수증·서식 검증과 내보내기·재열기를 확인했다. 6쪽→5쪽, URL 포함 주석 14개→0개, 출처 설명 2,435자→1,214자, 표의 식별자·윗첨자 동행을 확인했다. 원본·표준·초안·설정 해시가 유지됐고 사용자 문서/DB에는 저장하지 않았다. 검수 문서와 화면은 무시된 개인 캐시에만 두며 저장소에는 넣지 않는다. [검증 범위](evidence/report-print-readability-2026-10-25.json).

생성 중인 작업이 없는지 확인한 뒤 이 작업의 API만 재시작해 `15330`/`18330`에 적용했고 UI·API의 HTTP 200을 확인했다. 사용자 탭을 새로고침하거나 기존 동결 미리보기를 바꾸지는 않았다. 실제 모델 재호출·Hancom 검수·전체 회귀·엔진 재빌드·main/운영 배포는 수행하지 않았다. 이번 변경의 커밋/원격 푸시는 아직 하지 않았다. 앞선 CLI 실행 브랜치의 푸시 `8e4b310`은 이 변경 전 버전이다.

## 표준 편집 마무리 — 2026-10-25 후속 검수

후속 제출 준비: 이 절과 아래 편집 디자인 변경은 QA 커밋 `41a64bd`로 기록하고 CLI 실행 브랜치에 병합·푸시했다(통합 커밋 `f864655`). 새 엔진 kit `developer-engine-2026-10-25-editor-designs`의 게시 자산 SHA와 실행기 다운로드를 확인했다. 이후 문구 정리에서는 입력 범위를 `-20% ~ +50%`로 표시하고 줄바꿈·공백 압축·자간 설명만 간소화했다. 기존 정책 27건과 TypeScript를 확인했으며 설정/해시 계약은 바꾸지 않았다. 사용자 보고서 1건을 별도 읽기 전용 화면에서 검토했지만 원본은 수정하지 않았고 문서 내용/파일은 Git에 포함하지 않는다.

같은 QA 미커밋 작업에서 기준값 복귀 시 변경 색상을 제거하고, 저장 성공 시 저장한 예시를 새 비교 기준으로 삼도록 수정했다. 선택값의 표시상 동등성(생략된 기본값·동일 수치·검증된 ORIGINAL 별칭)과 저장되는 원본 설정/해시를 분리한다. 동일한 값은 재요청하지 않으며 중복 기본 선택지 대신 실제 값에 `✓ 기준`을 표시한다. 역할별 원본 줄바꿈처럼 명시값 하나와 같지 않은 설정은 별도 선택으로 보존한다. 저장 방법 라디오 배치와 자간의 입력 범위·현재 보정값 설명도 수정했다.

공백 압축은 고정 엔진의 지원 범위에서 선택형 30%·50%를 추가했다. 실제 WORD 줄바꿈·자간 0에서 예시 ‘추진 일정’의 ‘확인을’이 0%에서는 다음 줄, 20/30/50%에서는 앞줄에 놓였다. 다른 긴 문장은 네 값 모두 같았다. 조건에 따라 적용되는 빈칸 압축이며, 모든 줄이 달라지는 기능으로 안내하지 않는다. 합성 본문 전체와 3쪽 배치는 보존됐다.

집중 백엔드 27건·모의 UI 3건·실제 rhwp 2건과 TypeScript/진단 프런트 빌드가 통과했다. 최종 실제 시험에서 기준값 재선택 요청 0, 기준 복원/저장 직후 변경 표시 0, 저장한 20% 재선택 요청 0을 확인했다. 테스트 중 소스 갱신으로 영향을 받은 한 경로만 최종 코드에서 다시 확인했다. 전체 회귀·실제 AI 호출·한컴 검수·main/운영 배포는 수행하지 않았다. `15330`/`18330`에 적용되어 있으며, 기존 문서와 별도 CLI 보존 브랜치는 유지한다. [검증 근거](evidence/standard-editor-polish-2026-10-25.json).

## 문서 표준 편집 멈춤 수정 — 2026-10-25 로컬 후속

사용자가 확인한 `15330` 시험 앱에서 줄 끝 공백 압축 후 자간 입력 시 미리보기가 멈추는 경합을 실제 rhwp로 재현했다. 숨은 편집기가 입력 초점을 가져가 문서가 변경되고, 다음 샘플을 열 때 보이지 않는 미저장 확인창을 기다리는 원인이었다. 임시 렌더러 내부도 입력 불가로 만들고 그 렌더러의 파일 교체에만 확인창 생략을 적용했다. 이전 요청 취소 시 해당 엔진을 폐기·재생성하며 이미지 decode에도 취소와 10초 제한을 적용한다. 일반 문서 편집의 저장 확인 기본값은 유지한다.

선택한 줄바꿈·공백 압축 값에 맞는 도움말을 표시하고, 비교/한 쪽 보기 선택 표시와 반복 클릭 동작을 수정했다. 실제 rhwp 3건(입력 조합·로드 중 입력 경합·기존 별첨 1~3 및 기준서 왕복), 모의 UI 고유 5건, 백엔드 정책 22건, TypeScript와 별도 진단 프런트 빌드가 통과했다. 기존 표준·기준서 해시와 합성 본문 보존을 확인했다. 사용자 브라우저를 새로고침하지 않았으며 `15330`의 제공 코드·API 설명에 수정이 반영됐음을 확인했다. 실제 AI 호출·한컴 검수·운영 배포는 수행하지 않았다. [검증 근거](evidence/standard-editor-fix-2026-10-25.json).

기준 HEAD는 `a013b8633e2d488d0c1a4799f9698ec310bcb92d`, 브랜치는 `codex/local-ui-qa-2026-10-22`이며 아래 편집 디자인 WIP와 함께 미커밋 상태다. main·CLI 보존 브랜치와 기존 `15201` 실행 환경은 변경하지 않았다.

## 편집 디자인·AI 추천 — 2026-10-25 로컬 추가 작업

QA `codex/local-ui-qa-2026-10-22`의 `a013b8633e2d488d0c1a4799f9698ec310bcb92d` 위 미커밋 변경이다. [구현·검증 범위](editor-designs-2026-10-25.md), [검증 근거](evidence/editor-designs-2026-10-25.json)를 함께 읽는다. 양식 보관함의 표·제목 예시는 기존 문서 편집으로 연결하며, 선택한 부분에 디자인을 적용하거나 문서 전체에 등록 표준의 지원 서식을 적용할 수 있다. 실제 rhwp 전후 비교·확대, 취소·되돌리기와 기존 저장 경계를 사용한다. 문서 구조의 자동 재작성이나 별첨 간 표지·목차 구성 변환은 포함하지 않는다.

문서 AI 대화는 질문 시점의 미저장 문서와 현재 표·문단을 읽고 사용 가능한 디자인 ID·해시를 제안한다. 제안 클릭은 실제 비교 화면을 열며 적용은 별도 선택이다. 실제 Codex `gpt-6.1-sol` 1회로 미저장 표 문구 확인·추천·미리보기 및 명시적 적용 전 버퍼/서버 불변을 확인했다. 집중 백엔드 고유 79건(서식 21 + AI 58), 모의 UI 14건, 선택 브리지 단위 5건, 실제 rhwp 디자인 4건 + 기존 블록 삽입 회귀 1건이 통과했다. TypeScript와 잠금 파일의 Vite 7.3.7을 사용한 별도 프런트 빌드도 통과했다. 제목선과 다음 표의 겹침은 실제 화면에서 확인 후 간격을 보완하고 재검수했다. 전체 회귀·최적화 엔진·한컴 검수·운영 배포를 새로 수행한 것은 아니다.

시험용 앱은 `http://127.0.0.1:15330/`, API `18330`, 합성 데이터 `.cache/editor-design-20261025/qa/`에만 연결된다. 기존 `15201` 앱의 세 프로세스 PID·시작 시각과 HTTP 200을 최종 재확인했고, 사용자 데이터·바탕화면 실행기는 변경하지 않았다. 이번 진단 엔진은 새 읽기 전용 위치 브리지를 포함하므로 이전 개발자 엔진 키트와 혼용하지 않는다.

앞서 별도 보존·푸시한 실행용 브랜치 [`codex/local-cli-ready-2026-10-25`](https://github.com/m4r4m01m/hwp-assistant/tree/codex/local-cli-ready-2026-10-25)의 `d8b93346a1b5caec1db670c1d656d8c94245321a`는 이 미커밋 기능을 포함하지 않는다. 그 브랜치와 main/운영은 이번 재개 작업에서 변경하지 않았다.

## 로컬 QA 동기화 — 2026-10-25 추가 관측

Windows QA 브랜치 `codex/local-ui-qa-2026-10-22`를 `e0e49f7`에서 main `49dda2c8de06288111216a61ba720dc148834257`까지 fast-forward했다. QA 작업은 이미 main에 포함되어 있었으며 이력을 재작성하지 않았다. 아래의 클라우드 기록과 별개로, 로컬 집중 백엔드 고유 125개 통과·POSIX 전용 3개 제외, TypeScript, 프런트 단위 12개, 모의 UI 19개, 실제 rhwp 3개 시나리오를 확인했다. 새 Codex MCP 테스트의 Linux 경로 문자열 가정을 TOML 해석으로 수정하고 관련 5개를 재검증했다. 전체 회귀나 실제 모델 호출을 새로 수행한 것은 아니다.

검증한 별도 실행 환경 `.cache/main-sync-20261025/source`로 `http://127.0.0.1:15201/`과 기존 바탕화면 실행기를 전환했다. 기존 저장소의 문서 10개·버전 13개·표준 4개·파일 17개를 백업하고 기존 행/파일의 변경·누락 0건을 확인했다. 계정/사용량 테이블 2개만 추가됐으며 로컬 비밀번호 계정은 꺼진 상태다. Codex ChatGPT 로그인과 보고서·문단 수정·문서 대화의 configured/allowed를 확인했고 모델은 호출하지 않았다. 기존 사용자 탭은 새로고침하지 않았다. 현재 엔진은 검증된 이전 **진단 빌드** 재사용이며 클라우드의 최적화 빌드와 구분한다. [검증 근거](evidence/local-main-sync-2026-10-25.json). 이 로컬 경로·포트는 다른 PC나 운영 서버에 그대로 적용하지 않는다.

갱신일: **2026-10-25 (한국 시간)**. 통합 기준: [main `295b6e19a81738cb0156889020f21d691df5db27`](https://github.com/m4r4m01m/hwp-assistant/commit/295b6e19a81738cb0156889020f21d691df5db27), [문서 PR #34](https://github.com/m4r4m01m/hwp-assistant/pull/34). 제품 코드는 #33 기준이다. 후속 클라우드 점검 코드 [8a8c9afae4bd809362b8f2eb87507a10e44e1ad3](https://github.com/m4r4m01m/hwp-assistant/commit/8a8c9afae4bd809362b8f2eb87507a10e44e1ad3)는 별도 `codex/cloud-reliability-2026-10-25` 브랜치에 있으며 **main 병합·운영 배포 전**이다. 후속 결과와 한계는 [10절](#10-2026-10-25-클라우드-신뢰성-점검)을 우선한다. 이 파일은 ChatGPT Work와 Codex가 함께 읽는 인수인계 기준이다. 이후 작업에서는 실제 최신 main·CI·운영 상태를 다시 확인한다.

## 1. 읽는 순서와 증거 기준

1. [루트 AGENTS.md](../AGENTS.md)의 작업·보존 규칙을 읽는다.
2. 이 문서에서 현재 상태·미완료·운영 절차를 확인한다.
3. 해당 기능의 코드와 아래 문서·PR·정확한 커밋의 Actions를 대조한다.

현재 코드/잠금 파일/워크플로와 실행 증거를 우선한다. 날짜가 있는 설계·대화·검증 보고는 당시의 기록이다. 대화의 assistant 완료 보고, CI 성공, 이미지 게시, 서버 배포, 실제 모델 성공, 사람의 호환성 검수는 서로 다른 증거다. 첨부파일 원본이나 로컬 캐시가 없는 기록은 재현 확인한 것으로 표현하지 않는다. 이 문서는 접근 가능한 저장소·이슈/PR·Actions·관련 대화를 조사한 요약이며, 모든 과거 대화/첨부의 완전한 사본은 아니다.

## 2. 목표와 환경 제약

- 바이브 코딩 챌린지/PoC에서 출발한 HWPX 문서함과 AI 작성·편집 지원 도구다. 문서 보존, 비교·승인 후 수정, 기존 한글 작업과의 왕복이 우선이다. [계획 v0.4](internal-doc-assistant-plan-v0.4.md)는 요구사항 기준으로 보존하며 완료 보고로 바꾸지 않는다.
- 개인/공중망 개발·홈서버 PoC와 회사 폐쇄망은 분리한다. 두 망을 연결하거나 내부 문서를 외부 모델에 보내는 전제를 두지 않는다. 외부 개발·시험은 합성 자료를 사용한다.
- 회사 모델 명칭 **“Qwen 3.8”은 사용자 전달 정보**다. 정확한 모델 ID·API·크기·라이선스는 미확인이다. 다른 모델명으로 추정해 바꾸지 않는다. 홈서버 Codex는 인터넷 의존 경로이므로 폐쇄망 AI 검증을 대체하지 않는다.
- 초기 운영은 본인·소수 사용자 시험과 심사 시연을 염두에 뒀다. 최소 비용을 지향하고 유료 AI 자동 대체는 하지 않는다. 하드웨어 증설·처리량·모든 브라우저의 로딩 시간 개선은 확인 없이 보장하지 않는다.
- 실행 환경이 서버의 사설망에 접근하지 못할 수 있다. 접근 실패를 서버 고장으로 단정하거나 SSH 개인키·인증파일 공유로 해결하지 않는다.

## 3. 현재 기능과 아키텍처

| 영역 | 현재 main의 동작과 경계 |
|---|---|
| 문서함·편집 | HWPX 업로드/새 문서/수동 편집/자동 저장/다른 이름 저장/다운로드. 원본·과거 버전은 불변이며 복원도 새 버전이다. HWPX가 정본이다. |
| 저장·권한 | FastAPI + SQLAlchemy + 파일 저장소. 문서 행 잠금, ACL, 편집 임대·기준 버전, 멱등 키를 검사하고 파일 영속화와 DB 확정 후 저장 완료를 알린다. 다운로드는 서버 저장과 다르다. |
| 버전 비교 | 실제 페이지와 문단·서식·표 변경, 수정 계정·중간 저장 이력 비교. 두 버전을 순서대로 선택한다. 비교가 자동 병합을 제공하지는 않는다. |
| AI 문단 수정 | 미저장 원문을 포함한 고정 스냅샷 → 두 후보 → 비교/승인 → 문서·선택·원문 fence 재검사 → rhwp 명령 적용 → 새 버전 저장. 안전 적용 가능 범위 밖은 거부한다. |
| 보고서 | 실제 Codex app-server delta, 초안 내용 검토·별도 보완 작업, 문서 표준/별첨 1~3/실제 렌더링 쪽수 목차, 표·편집 가능한 흐름도. 완료·검증된 draft만 임시 미리보기에 적용하고 사용자가 확정·서버 저장한다. |
| 문서 대화·컨텍스트 | 권한 있는 문서의 사실·작성 기준·선택 원문과 대화/후보/적용 이력. 문서 독자도 모든 작성자의 대화를 읽을 수 있으나 승인·편집·저장은 불가하다. 작성자 전용 비공개 대화로 오해하지 않는다. |
| 양식·문서 표준 | HWPX 양식 등록·검색·새 문서 생성, 제한된 제목/요약/담당·기한 블록, 표준 편집·검증된 예시·등록. 고정 기준서와 과거 표준/초안은 덮어쓰지 않는다. |
| 계정 | 선택적으로 비밀번호 로그인/가입/guest, 기존 토큰·만료/폐기 가능한 세션과 공존. AI는 별도 ACL이다. guest는 하나의 공용 문서 주체다. |
| 반응성 | 정적 압축/해시 캐시·검색·모바일 패널, 로그인 화면의 낮은 우선순위 SRI 자원 다운로드. 다운로드 완료가 WASM 컴파일/문서 배치 완료는 아니다. |

`backend/app`가 인증·저장·AI 작업을, `frontend/src`의 React/TypeScript와 `EditorAdapter`가 UI·편집기를 담당한다. rhwp 0.8.6은 [engine lock](../scripts/engine-lock.json), 소스 패치와 [bridge](../scripts/assistant-bridge.ts.txt)로 고정한다. npm 원본 WASM이나 출처 없는 빌드로 바꾸지 않는다. 기본 release 빌드는 패치·해시·실제 엔진 검사·고지·출처를 확인하며 진단 코어를 거부한다.

네이티브 기본 저장소는 SQLite `data/metadata.db`와 `data/files/`다. 실행기가 별도 경로를 지정할 수 있으므로 항상 실제 `DATABASE_URL`/`STORAGE_DIR`을 확인한다. 과거 Windows `.cache`·포트·바로가기를 새 PC에 존재하는 경로로 가정하지 않는다. 운영은 PostgreSQL과 파일 볼륨, **db/api/web 3개 서비스·API 1 worker**다. worker/서버를 늘리려면 프로세스별 로그인 제한·작업 용량 제어를 공유 저장소로 옮기는 설계가 필요하다.

## 4. 주요 연혁과 결정 근거

아래 날짜는 KST 기준이다. 세부 구현·증거는 링크한 원문을 보존한다.

| 시점 | 작업·결정·검증 |
|---|---|
| 2026-09-23~27 | 요구사항·망 분리·비용/보존 원칙 확정, [PR #19](https://github.com/m4r4m01m/hwp-assistant/pull/19) 초기 구현. 실제 Codex+MCP의 사람 승인·저장과 한컴 2018 단순 문단/2×2 표 일부 왕복 확인. [9/27 검증](verification-2026-09-27.md). |
| 2026-10-06 | 로컬/사내 OpenAI 호환 HTTP 모델을 문단 두 후보 흐름에 연결, 늦은 응답/재시작/원문 경계 보강. 실제 rhwp + **모의 HTTP 모델**, 웹 저장·재가져오기 3회 확인. 실제 사내 모델은 미연결. [10/06 검증](verification-2026-10-06.md). |
| 2026-10-07~18 | 보고서 기본 서식·실제 스트리밍·표/흐름도·양식/블록·목차·문서 표준·개발 실행기 확장, [PR #20](https://github.com/m4r4m01m/hwp-assistant/pull/20). [10/17 인수인계](development-handoff-2026-10-17.md), [10/18 검수 안내](implementation-status-and-user-review-2026-10-18.md). |
| 2026-10-19~23 | 버전 비교·수정자 추적, 내용 검토 후 별도 보고서 보완, 문서함/표준 메모. [버전 비교](version-comparison.md), [검토 흐름](report-review-workflow-2026-10-20.md), [메모 인수인계](development-handoff-library-notes-2026-10-23.md). |
| 2026-10-22 | [#21](https://github.com/m4r4m01m/hwp-assistant/pull/21) Funnel 설계 → [#22](https://github.com/m4r4m01m/hwp-assistant/pull/22) 운영 Compose/23000/하위 경로 파이프라인 → [#23](https://github.com/m4r4m01m/hwp-assistant/pull/23) programmatic Vite build의 base 누락 회귀 수정. 개발 루트 경로와 운영 접두 경로를 각각 검사한다. |
| 2026-10-23 | [#24](https://github.com/m4r4m01m/hwp-assistant/pull/24) 반응성·세션·모바일·보안, [#25](https://github.com/m4r4m01m/hwp-assistant/pull/25) QA 통합 및 권한/예산/비동기 회귀 보완. [별도 로컬 환경 검증](development-handoff-main-adoption-2026-10-23.md)은 실제 모델 3회/자동 승인 시험을 기록하며 사람 검수와 구분한다. |
| 2026-10-23 운영 관측 | “서버 통신 성능 점검” 대화에서 사용자는 호스트 nginx upstream을 HTTP/1.1로 바꾸고 재시작했다고 보고했다. 이후 압축 전달이 개선됐다는 첨부 기반 분석은 과거 관측이며 이번에 원 첨부를 재검증하지 않았다. 압축 전송량·WASM 초기화·문서 배치 시간을 구분하며 캐시/prewarm 효과를 측정한다. |
| 2026-10-24 | [#26](https://github.com/m4r4m01m/hwp-assistant/pull/26) 로컬 runner 자료 제외, [#27](https://github.com/m4r4m01m/hwp-assistant/pull/27) 검증·게시된 이미지 재빌드 없는 배포. 최신 main 태그가 아니라 실제 게시 run의 SHA를 사용한다. |
| 2026-10-24 | [#29](https://github.com/m4r4m01m/hwp-assistant/pull/29) 표준 편집/공통 업무 예시/검증 미리보기, 당시 백엔드 903개. 사용자는 브랜치 계보가 보이는 merge commit을 선호한다. squash 뒤 [동일 tree 이력 연결](https://github.com/m4r4m01m/hwp-assistant/commit/1b4e1a054c47e4ee4087b3f8cea1b0cfadf032e5)을 했으며 후속 통합은 이력을 보존한다. |
| 2026-10-24 | [#30](https://github.com/m4r4m01m/hwp-assistant/pull/30) 재시작 후 HTTPS 502: gateway overlay/Docker DNS 재조회/readiness 보완. readiness는 읽기만 재시도하고 가입·업로드 POST를 통째 재실행하지 않는다. |
| 2026-10-25 | [#31](https://github.com/m4r4m01m/hwp-assistant/pull/31), `0c65905`: Codex runtime·비밀번호 계정·로그인 시 WASM 사전 다운로드, 백엔드 **941개** 및 병합 후 CI 통과. 해당 작업의 실제 모델 호출은 0회. |
| 2026-10-25 | [#32](https://github.com/m4r4m01m/hwp-assistant/pull/32), `cbeb74f`: #31과 **파일/tree 동일**, 기존 구현 브랜치의 이력 연결. 새 제품 변경으로 중복 계산하지 않는다. |
| 2026-10-25 | [#33](https://github.com/m4r4m01m/hwp-assistant/pull/33), `95eb0da`: QA와 최신 계정/권한/한도 통합, AI 연결 안내·디자인 조각 소유권/동시 저장·요일 보존·등록 재시도·표지/목차/표준 설정 보강. 백엔드 **1,021개**로 증가. |
| 2026-10-25 17:42 | [수동 배포 run 40177050099](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099)에서 #33 SHA의 검증·이미지 게시·홈서버 배포·공개 경로 검사 성공. 서버 device login/실제 모델 응답 완료를 뜻하지 않는다. |

## 5. 검증된 상태와 아직 확인할 것

**최신 확인 증거:** #33 병합 후 [HWP deployment 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174658302), [frontend build path](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174657544), [deployment-only 검사](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174657560)는 성공했다. 일반 push에서는 게시/운영 배포 job이 건너뛰어지는 것이 정상이다.

**후속 확인:** #34의 정확한 `295b6e1`에서 [수동 run 40180482900](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40180482900)의 검사·게시·홈서버 배포 5개 job이 성공했고, 배포 job 종료는 **2026-10-25 18:33:29 KST**였다. Python 1,021개, frontend unit 26개, mocked UI 35개, TLS 1개, 배포 브라우저 2 passed/1 skipped가 로그에 기록됐다. 같은 SHA의 push 검사도 성공했으며 게시·배포 2개 skip은 정상이다. 아래 #33 기록을 대체하는 후속 운영 증거이며 실제 Codex 모델 성공을 증명하지 않는다.

후속 [수동 run](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099)의 [Python job](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099/job/121119882634)에서 **1,021 passed, 2 warnings**를 확인했다. 경고는 의존성의 httpx/TestClient·anyio portal 폐기 예정 API다. [게시 job](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099/job/121120768129)과 [홈서버 배포 job](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40177050099/job/121121722084)도 성공했다. 이는 해당 시점/SHA의 기록이며 이후 서버 설정까지 보장하지 않는다.

| 구분 | 확인된 범위 / 제한 |
|---|---|
| #33 PR 기록 | frontend unit 26, Node 계약 82, mocked UI 35. 실제 고정 rhwp 시나리오 고유 74개 통과/조건부 skip 6개. 모의 AI와 실제 모델을 혼동하지 않는다. |
| 계정·실제 컨테이너 | [PR 통합 run](https://github.com/m4r4m01m/hwp-assistant/actions/runs/40174233009): PostgreSQL/API/web/신뢰한 HTTPS, guest·개인 문서 저장/재열기, 바이트 무결성·격리, 세션/재로그인·API 재시작, 실제 자원 캐시 재사용. CI에서 Codex 로그인이나 모델 호출은 하지 않는다. |
| 실제 모델 | 9/27과 10/23의 별도 로컬 기록은 존재한다. **최신 홈서버 계정/세 기능 조합의 실제 모델 성공은 미확인**이다. |
| 한컴·브라우저 | 과거 한컴 2018 단순 문서/표 일부 왕복만 확인. 125% 편집 화면 표 외곽선 시각 차이, 복잡한 그림/필드/반복 왕복, 최신 Windows·한컴·Safari/Firefox 실기기는 추가 검수 대상이다. |
| 폐쇄망·복구·부하 | 실제 사내 모델·외부 차단 신규 세션 전체 경로, 격리 환경 복원 리허설, 디자인 조각 동시 저장의 PostgreSQL 부하 검증은 미완료다. |

조사 시점 standalone issue는 반환되지 않았고, 열린 [PR #28](https://github.com/m4r4m01m/hwp-assistant/pull/28)은 **Draft·미병합**이다. 외부 LLM adapter/HTTP 보고서 provider를 main 기능으로 설명하거나 그 코드가 배포됐다고 가정하지 않는다.

## 6. 운영: 계정·Codex·환경변수

### 6.1 마지막 사용자 보고와 원인

이 절의 ACL/login 관측은 #34 문서화 당시 기록이다. 후속 2026-10-25 사용자 인계에서는 운영 Codex 로그인·허용이 확인됐고 보고서 검색 완료 0건 문제가 관측됐다. [10절](#10-2026-10-25-클라우드-신뢰성-점검)의 후속 근거를 우선하며, 아래 미완료 표현을 현재 로그아웃 상태로 단정하지 않는다.

사용자는 운영 API에서 `HWP_CODEX_USER=local`, `HWP_CODEX_ALLOWED_USERS` 빈 값과 “서버에서 Codex ChatGPT 로그인이 필요합니다” 안내를 확인했다. 이어 **`/opt/hwp-assistant/production.env`에는 두 ACL 키 자체가 없다**고 정정했다. 로그인/허용목록 변경 완료 보고는 없다. 이번 문서화 작업에서 서버의 private env·인증파일을 열거나 설정을 변경하지 않았다.

이는 [Compose](../compose.deploy.yaml)의 `${HWP_CODEX_USER:-local}`, `${HWP_CODEX_ALLOWED_USERS:-}` 기본값과 일치한다. **파일에 키가 있어야 컨테이너에 값이 생기는 구조가 아니다.** 셸 환경변수도 Compose 보간에 영향을 준다. 파일 전체나 expanded `docker compose config`를 출력하지 말고 필요한 비밀 아닌 항목만 점검한다.

[실제 ACL 코드](../backend/app/codex_runtime.py): 허용목록이 비면 `HWP_CODEX_USER` 하나, 비어 있지 않으면 쉼표 목록만 사용한다. `local`도 유지하려면 목록에 포함한다. `*`/끝 쉼표는 유효하지 않다. ACL은 **앱 ID**이며 ChatGPT 이메일·회원 생성·Codex 로그인이 아니다. `guest` 허용 시 동일 guest로 접속하는 모든 체험자에게 적용된다.

### 6.2 저장 위치와 계정 관리

| 정보 | 위치·보존 원칙 |
|---|---|
| 비밀번호 회원·guest | PostgreSQL의 `hwp_assistant` DB → `public.user_accounts` 테이블, salted scrypt 해시. 개별 계정 JSON 파일이 아니다. |
| DB·브라우저 세션·사용량 | `hwp-assistant-prod-metadata` → DB `/var/lib/postgresql/data`. `auth_sessions`, `codex_usage`도 DB에 영속화된다. |
| 기존 자동화 토큰 | private env의 `HWP_AUTH_TOKENS`. 비밀번호 계정/세션과 별개다. 토큰 원문은 기록하지 않는다. |
| 원본·버전 파일 | `hwp-assistant-prod-documents` → API `/data/files`. DB와 함께 백업한다. |
| Codex ChatGPT 인증 | `hwp-assistant-prod-codex-state` → API `/home/app/.codex` (`CODEX_HOME`). device login 후 private `auth.json` 생성. 내용·일회용 코드·인증 URL의 비밀값을 공유하지 않는다. |
| 배포·백업 | `/opt/hwp-assistant/state/last-success.json`, `/opt/hwp-assistant/backups/`. env와 Codex 인증은 DB/파일 자동 백업에 포함하지 않는다. |

계정 기능/가입은 각각 `HWP_ACCOUNTS_ENABLED`/`HWP_REGISTRATION_ENABLED`로 명시적으로 켜며 기본값은 0이다. 공개 가입은 guest/local·토큰/AI 허용 ID·과거 문서 주체를 예약해 선점을 막는다. 사전 허용 ID는 운영자 CLI로 만든다. `local`은 예약 ID라 비밀번호 계정으로 만들 수 없다. 아래 예시는 실제 인적정보가 아닌 합성 계정이다.

```sh
docker exec -it hwp-assistant-prod-api-1 python -m app.account_admin create \
  --user sampleuser --name '테스트 사용자' --employee TEST-001
docker exec -it hwp-assistant-prod-api-1 python -m app.account_admin password --user sampleuser
docker exec -it hwp-assistant-prod-api-1 python -m app.account_admin disable --user sampleuser
```

비밀번호는 터미널에서 두 번 입력하며 명령줄 인자로 주지 않는다. `create`는 기존 계정을 덮어쓰지 않는다. 재설정/활성 상태 변경은 비밀번호 세션을 무효화한다. 비밀번호 재설정은 별도 토큰을 교체하지 않으며, 비활성화는 해당 계정의 토큰 접근도 막는다. 사번 입력은 재직 확인/SSO가 아니다. 비밀번호 분실 복구 웹 UI는 없다.

호스트 경로가 필요하면 `docker volume inspect <위 볼륨명> --format '{{.Name}} -> {{.Mountpoint}}'`로 해당 볼륨만 확인한다. 실행 중 PGDATA나 인증파일을 직접 편집하지 않는다. 회원 점검은 필요한 최소 컬럼/집계만 조회하고 이름·사번·해시를 결과물로 옮기지 않는다.

### 6.3 서버 Codex 로그인과 실제 AI 확인

서버의 해당 컨테이너에 로그인해야 한다. 개인 PC/호스트의 Codex 인증이 자동 공유되지 않는다.

```sh
docker exec -it hwp-assistant-prod-api-1 codex login --device-auth
docker exec hwp-assistant-prod-api-1 codex login status
```

일회용 코드는 본인이 브라우저에서 처리하고 대화/PR에 붙이지 않는다. 앱은 `Logged in using ChatGPT` 상태를 확인하며 점검은 최대 5초/캐시 15초다. 인증파일 변경만으로는 이미지 재빌드가 필요하지 않다. 로그인 확인 후 새로고침하고 **합성 자료로 보고서·문서 대화·문단 수정**을 각각 완료까지 시험한다. 문단 수정은 후보 비교/승인·저장·재열기, 보고서는 검토·서식·확정 저장·재열기, 대화는 문서/버전 불변을 확인한다.

서버 인증 하나를 명시적으로 허용한 앱 계정들이 사용한다. 사용자별 Codex 인증 분리는 미구현이다. 앱 ACL은 서비스 이용 조건에 대한 허가를 대신하지 않는다. [기존 PoC 설계](poc-codex-accounts-prewarm-2026-10-25.md)의 이용 범위 구분도 확인한다.

운영 Compose 기본은 공통 동시 AI 작업 **1건**, DB 일일 cap **전체 합산 60건**, UTC 자정=한국 09:00 갱신이다. 구독 잔량이나 1인당 보장량이 아니다. 접수 후 실패/불확실 결과도 사용량을 소비하고 같은 작업 결과 재조회는 새 호출로 세지 않는다. 네이티브 기본 cap 0과 구분한다. 인증·한도·네트워크 실패에도 편집/저장은 유지한다. `HWP_CODEX_MODEL`/실행 설정을 확인하기 전 특정 모델이 선택됐다고 단정하지 않는다.

### 6.4 환경변수만 바꿀 때: 현재 이미지로 API 재생성

운영 파일을 보호된 위치에 백업하고 원하는 ACL 키를 **추가 또는 기존 줄 수정**한다. 예를 들어 `HWP_CODEX_ALLOWED_USERS=local,guest`는 두 앱 ID의 실행을 허용하지만 계정을 만들지는 않는다. 운영자가 원하는 실제 ID를 정하고 계정 존재/이용 범위를 확인해야 한다. 기존 DB 비밀번호·토큰·소유권·포트는 보존한다.

환경변수 변경은 `docker restart`로 적용되지 않는다. 진행 중 작업·미저장 탭을 정리하고 [백업 절차](deployment-runbook.md)를 수행한 뒤, **현재 배포에 사용한 Compose 파일**과 **실행 중인 이미지 SHA**로 API만 재생성한다. 새 main SHA를 이미지 태그로 추정하지 않는다. 아래는 기존 운영 이름/태그 형식의 서버에서 실행하는 절차이며 이 문서화 작업에서 실행한 명령이 아니다.

```sh
(
  set -eu
  api_container=hwp-assistant-prod-api-1
  deployed_dir="$(docker inspect "$api_container" --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}')"
  deployed_image="$(docker inspect "$api_container" --format '{{.Config.Image}}')"
  deployed_image_id="$(docker inspect "$api_container" --format '{{.Image}}')"
  cd "$deployed_dir"
  test -f compose.deploy.yaml
  export HWP_IMAGE_PREFIX="${deployed_image%-api:*}"
  export HWP_IMAGE_TAG="${deployed_image##*:}"
  test "${#HWP_IMAGE_TAG}" -eq 40
  case "$HWP_IMAGE_TAG" in *[!0-9a-f]*) exit 1;; esac
  test "$deployed_image" = "$HWP_IMAGE_PREFIX-api:$HWP_IMAGE_TAG"
  test "$(docker image inspect "$deployed_image" --format '{{.Id}}')" = "$deployed_image_id"
  # 현재 checkout이 실행 중인 이미지의 배포 source와 일치해야 한다.
  test "$(git rev-parse HEAD)" = "$HWP_IMAGE_TAG"
  git diff --quiet HEAD -- compose.deploy.yaml
  unset HWP_CODEX_USER HWP_CODEX_ALLOWED_USERS
  docker compose --env-file /opt/hwp-assistant/production.env \
    -p hwp-assistant-prod -f compose.deploy.yaml \
    up -d --no-deps --no-build --pull never --force-recreate \
    --wait --wait-timeout 180 api
)
```

파일/checkout/태그가 다르면 중단하고 배포 영수증의 source와 일치하는 보존 checkout을 찾는다. 다른 셸 변수나 `.env`가 추가로 덮어쓰는지도 확인한다. 계정 기능 등 다른 키를 변경한다면 그 키의 셸 override도 제거한다. 재생성 후 아래 두 비밀 아닌 ACL 값과 로그인 상태, 앱의 권한/실제 작업을 확인한다.

```sh
docker exec hwp-assistant-prod-api-1 sh -c \
  'printf "HWP_CODEX_USER=%s\nHWP_CODEX_ALLOWED_USERS=%s\n" "$HWP_CODEX_USER" "$HWP_CODEX_ALLOWED_USERS"'
docker exec hwp-assistant-prod-api-1 codex login status
```

### 6.5 배포와 장애 복구

- [게시 이미지 배포](deploy-published-images.md)는 성공한 게시 run과 main 계보를 검증하고 그 **원래 source SHA**를 체크아웃한다. 문서 PR 병합만으로 이미지를 다시 만들거나 서버를 바꾸지 않는다.
- 기존 서버에서 `scripts/deploy/init.py`를 재실행하지 않는다. `deploy.sh`는 필요한 새 Codex 볼륨만 보충하며 기존 DB/문서를 보존한다. 운영 Compose와 개발 Compose는 합치지 않는다. API/DB/RPC 포트를 외부에 열지 않는다.
- 백업은 앱 writer를 멈추고 DB custom dump·문서 archive·해시·영수증을 일관된 세트로 만든다. private env는 별도 보호 백업, Codex 인증 소실은 재로그인으로 복구한다. 브라우저 미저장 버퍼는 서버 백업 대상이 아니므로 복구용 다운로드로 보존한다.
- 백업 실패 시 교체를 중단하고 기존 앱을 재개한다. 기동 실패 시 DB 삭제/자동 downgrade를 하지 않는다. 이전 이미지의 스키마 호환성을 확인하거나 **별도 새 볼륨/DB**에 복원을 검증한 후 전환한다. 원본 운영 볼륨 삭제·전체 prune·실행 중 PGDATA 복제는 금지한다.
- 재시작 후 502는 먼저 해당 gateway/하위 경로/readiness·DNS를 점검한다. 로그인·업로드·가입을 반복 POST해 복구하려 하지 않는다. [#30](https://github.com/m4r4m01m/hwp-assistant/pull/30)의 실제 수정과 현재 workflow를 기준으로 판단한다.

## 7. 다음 작업과 알려진 한계

1. 후속 사용자 인계에서 서버 Codex 로그인·앱 ID 허용은 확인됐으나 세 실제 AI 경로 완료는 미확인이다. [10절](#10-2026-10-25-클라우드-신뢰성-점검)의 새 진단과 합성 자료로 실제 검색/보고서·대화·문단 수정을 완료까지 검수한다. 인증파일이나 private env를 수집하지 않는다.
2. 최신 Windows/한컴·Safari/Firefox 실제 장치, 복잡한 문서 왕복과 표 외곽선 시각 차이를 검수한다. 일반 HWPX 왕복 결함이 블록 보호 기능으로 해결됐다고 표현하지 않는다.
3. 개발자 엔진 kit와 현재 bridge의 불일치를 해소·게시하고 fresh clone 설치를 재검증한다. 기존 kit로 모든 환경에서 원클릭 실행된다고 보장하지 않는다.
4. 사내 모델의 정확한 계약을 확인하고, 의존성/이미지/폰트 사전 반입 → 외부 통신 차단 → 새 브라우저 → 내부 실모델 → 편집/승인/저장/복원 전체 흐름을 별도로 검증한다.
5. 백업의 다른 장치 보관·격리 복원 리허설과 PostgreSQL 동시 저장 부하를 확인한다. 다중 worker, SSO, 운영 계정 웹 관리·분실 복구, 실시간 공동편집/자동 병합은 완료 범위가 아니다.
6. [PR #28](https://github.com/m4r4m01m/hwp-assistant/pull/28)은 별도 검토를 유지한다. 원래 사용자가 요청한 Draft 분리를 존중하고 이 문서화 PR에 제품 코드를 섞지 않는다.

AI 출력 계약/원문 검사는 사실성·회사 규정 적합성의 자동 판정을 뜻하지 않는다. 표 셀 수동 편집과 AI 안전 적용은 범위가 다르다. 일반 저장 실패 큐는 브라우저 종료 후 완전 복원된다고 보장하지 않는다.

## 8. 기존 문서 인덱스와 상충 해석

| 주제 | 우선 참조 |
|---|---|
| 요구사항·전체 상태 | [계획 v0.4](internal-doc-assistant-plan-v0.4.md), [10/18 검수 안내](implementation-status-and-user-review-2026-10-18.md), 이 문서 |
| 보고서·표준 | [문서작성 표준](document-writing-standard.md), [내용 검토/보완](report-review-workflow-2026-10-20.md), [실제 streaming](report-stream-2026-10-08.md), [표/흐름도](rich-report-2026-10-08.md), [양식/목차](report-template-toc-2026-10-12.md) |
| 권한·AI·저장 | [협업/MCP](collaboration-and-mcp.md), [로컬 모델](local-model-rewrite.md), [원문 경계](rewrite-boundaries-verified-2026-10-06.md), [버전 비교](version-comparison.md), [저장소 점검](storage-audit.md) |
| 설치·엔진 | [개발자 quickstart](developer-quickstart.md), [엔진 패치 빌드](engine-empty-fill-fix.md), [엔진 통합](engine-integration.md) |
| 운영 | [runbook](deployment-runbook.md), [첫 연결](deployment-first-activation.md), [게시 이미지 배포](deploy-published-images.md), [Codex/계정/prewarm](poc-codex-accounts-prewarm-2026-10-25.md), [반응성/보안](responsiveness-security-2026-10-22.md) |
| 과거 증거 | [9/27](verification-2026-09-27.md), [10/06](verification-2026-10-06.md), [10/23 로컬 실제 모델](development-handoff-main-adoption-2026-10-23.md), [호환성](compatibility.md), [커밋된 evidence](evidence/) |

- [remaining-work.md](remaining-work.md)의 10/06 배포 보류·PostgreSQL 미검증·보고서/블록 미구현은 **그 당시** 상태다. 위 PR/CI로 확인한 구현·배포 현황이 현재 기준이다.
- README·PoC 설계의 “검토 브랜치/미병합”과 초기 runbook의 “배포 미실행” 범위는 해당 작성 시점 기록이다. #31~33 및 후속 실제 배포 Actions를 함께 읽는다.
- 협업/MCP 문서의 과거 “가입/블록 없음”·MCP 선택 설치 설명을 현재 Docker 동작으로 복제하지 않는다. native에서 선택 설치였던 MCP는 현재 API 이미지에 포함된다. production의 토큰 최소 길이도 native와 다를 수 있어 실제 검증 코드를 따른다.
- 날짜가 붙은 Windows 경로·포트·성능 수치·테스트 수는 당시 환경의 증거다. 이 문서의 최신 source 밖에 자동 적용하지 않는다.

조사한 관련 대화(접근 권한 있는 계정에서만 조회 가능, 공개 GitHub 증거와 구분):

- [세 기능 통합 검증](https://chatgpt.com/c/6adcd8a9-e108-83ec-9b15-faccb3e497b9): 서버 로그인 필요·ACL 결과·env 키 부재의 사용자 보고, 장기 인수인계 요청.
- [브랜치 검증 및 병합](https://chatgpt.com/c/6addaa65-8a5c-83e8-8c4a-14606abdd70d), [브랜치 병합 및 테스트](https://chatgpt.com/c/6adc41d2-2da4-83ee-9e41-6ec9ee14d8b5): 이력 보존 선호와 #29~33 통합 맥락.
- [LLM API 연동 방향 제안](https://chatgpt.com/c/6adbc8c3-bb98-83e8-be28-519872884aef), [성능 보안 UI 점검 & 자동 배포 환경 구성](https://chatgpt.com/c/6ad9c305-73d0-83e8-9d10-b8c506dbd87b), [작업 재개 요청 & 보완사항 제안 요청](https://chatgpt.com/c/6adb14e2-3898-83ee-90f0-a8551bc1c9bb): 외부 adapter 초안과 배포/QA 분리.
- [서버 통신 성능 점검](https://chatgpt.com/c/6adb043c-89e4-83ee-8d95-584199ed26ea), [최근 작업 확인](https://chatgpt.com/c/6ad8c09b-819c-83ec-ae06-7488be0e9ea5), [깃헙 레포 작업사항 읽기](https://chatgpt.com/c/6abb24a0-a634-83ee-844a-234f4be1708d): 초기 운영/네트워크 관측. 원 첨부를 이번에 재검증한 것으로 보지 않는다.

## 9. ChatGPT Work와 Codex에서 다시 사용하는 방법

이 파일 저장은 **ChatGPT 계정 Memory를 강제로 저장하거나 다른 도구로 자동 공유하는 작업이 아니다.** 이 환경에는 계정 메모리 쓰기 도구가 없고, Codex가 ChatGPT의 저장된 기억을 자동 상속한다고 가정하지 않는다. 대신 버전 관리되는 두 파일을 공통 근거로 사용한다.

**Codex:** 최신 main을 보존 방식으로 가져온 저장소를 프로젝트로 열고 새 작업을 시작한다. 루트 `AGENTS.md`는 Codex의 프로젝트 지침 발견 대상이며, 여기서 이 파일을 먼저 읽도록 연결했다. 새 세션에서 “적용 중인 지침 파일과 PROJECT_CONTEXT의 기준 SHA, 현재 branch/HEAD를 먼저 확인해 줘”라고 요청한다. 상위/하위 `AGENTS.override.md`나 개인 지침이 있으면 우선순위를 확인한다. 상세 문서가 자동 전부 주입된다고 가정하지 않는다. [공식 AGENTS.md 발견/확인 안내](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

**ChatGPT Work:** 이 private GitHub 저장소에 권한이 있는 연결 도구로 최신 main의 `AGENTS.md`와 `docs/PROJECT_CONTEXT.md`를 먼저 읽도록 요청한다. 연결이 없는 환경에서는 두 파일을 Work 프로젝트/대화의 파일로 첨부하고 **기준 SHA도 함께 전달**한다. 첨부본은 자동으로 GitHub 최신본과 동기화되지 않으므로 다음 작업 때 갱신한다. 단순 로컬 파일 경로나 private 링크만으로 접근할 수 있다고 가정하지 않는다. [Work의 파일·프로젝트 활용](https://learn.chatgpt.com/docs/web), [컨텍스트 접근 범위](https://learn.chatgpt.com/docs/enterprise/work-admin-faq).

재개 요청 예시:

> `m4r4m01m/hwp-assistant` 최신 main의 `AGENTS.md`와 `docs/PROJECT_CONTEXT.md`를 먼저 읽고 기준 SHA와 실제 HEAD를 대조해 줘. 현재 코드·정확한 Actions 결과를 우선하고, 과거 검증/현재 운영/미확인을 구분해 이어서 작업해 줘. 비밀정보와 실제 회사 자료는 가져오지 마.

의미 있는 결정·병합·실행 검증 후에는 이 문서의 갱신일·source SHA·PR/Actions·관측 환경·미완료·다음 단계를 함께 갱신한다. 테스트 숫자만 올리거나 오래된 운영 상태를 그대로 복제하지 않는다.

## 10. 2026-10-25 클라우드 신뢰성 점검

### 10.1 범위와 증거 구분

사용자는 맥북을 계속 켜둘 수 없어 클라우드에서 점검·수정·검증·PR 준비까지 수행하도록 요청했다. 최신 main `295b6e19a81738cb0156889020f21d691df5db27`과 AGENTS/이 문서 전체를 확인한 뒤 독립 브랜치를 만들었다. 로컬 원 대화의 체크아웃·미저장 작업·과거 HWPX·운영 볼륨·계정·인증파일은 수정하지 않았다. GitHub 연결로 source를 확보했고, 큰 과거 로드맵 PNG는 다운로드 도구 제한으로 로컬에 가져오지 않았지만 **원격 기준 tree를 보존하여 변경 대상에서 제외**했다.

클라우드에서 공개 HTTPS GET으로 `/hwp-assistant/` 200, `/api/health`의 `ok=true`, `/api/auth/status`의 accounts/registration/guest 모두 false를 직접 확인했다. 저장소의 `scripts/deploy/smoke.py --url https://pseudowasabi-svr.taile7b6c4.ts.net/hwp-assistant/`도 경로·보호 API 401·앱/편집/비교 정적 자원·WASM 무결성 검사를 통과했다. 로그인·쿠키·업로드·문서 수정은 요청/실행하지 않았다. 운영 자격은 클라우드에 제공되지 않았으며 기존 토큰을 추정하거나 인증파일을 수집하지 않았다.

사용자가 별도 로컬 read-only 인계로 제공한 관측은 **2026-10-25 KST 사용자 관측**이다: 운영 Codex 세 기능 configured/allowed 및 ChatGPT 로그인 확인, HTTP chat/review 미설정, `/ai/models` 503. 이는 별도 HTTP LLM의 미설정이며 Codex 보고서 실패와 혼동하지 않는다. 클라우드에서 해당 인증 후 검사를 직접 성공시킨 것으로 쓰지 않는다.

후속 사용자 인계에서 **research=false 합성 회의실 예약 보고서 1건**이 실제 운영 Codex app-server의 `gpt-6-astra`로 completed=true·status=ready가 됐다. startup 1,847ms/first text 6,330ms/generation 19,347ms/text 13,017ms, 검증된 ReportDraft·SHA 생성 → 내용 검토 정지 → 사용자의 문서 만들기 → 실제 fixed rhwp 미리보기 2쪽까지 확인했다. approval=null이고 확정/서버 저장 버튼은 누르지 않아 기존 문서·버전이 불변이었다. **기본 모델·JSON·서식 경로의 1건 성공 근거이며 research=true 웹 검색 복구나 서버 저장 성공은 아니다.** 클라우드 직접 실모델 시험으로 계산하지 않는다. 사용자 로컬 Python 3.14.7의 main 1,021 passed/기존 warning 2개/197.14초, 배포 계약 75개, frontend 합산 143개+tsc 성공도 별도 인계 기록이다. 코드 수정·커밋·새 배포는 하지 않았다는 인계를 유지한다.

### 10.2 보고서 원인과 수정

사용자 관측의 실패 4건은 research=true, 실제 소요 99.5/126.43/111.23/112.6초, 마지막 writing·research_calls=0·draft 없음이었다. 두 출력에 웹 검색 도구 오류가 명시됐다는 인계를 받았다. **240초 timeout 전에 끝났고, 완료된 공개자료 조회가 없는 연구 요청을 거부하는 코드 경로와 일치한다.** upstream 웹 검색 자체가 왜 사용 불가였는지는 실제 모델 시험 없이 확정·해결 완료로 표현하지 않는다.

후속 코드의 변경:

**검색 실행 command·도구 설정·web_search=live 경로 자체는 기존 main과 동일하다.** 아래 변경은 진단·완료 판정·정리의 개선이며 운영 웹 검색 기능의 실제 복구를 뜻하지 않는다. 사용자에게 원래 운영 오류 전체가 해결됐다고 안내하지 않는다. [최소 검색 진단 절차](codex-research-diagnostics-2026-10-25.md)에 모델 호출 없는 설정/제공자 메타데이터 점검과 명시적인 합성 검색 시험의 구분을 남겼다.

- `execution.failure`에 고정 code·stage·완료 검색 수와 제한된 검증 경로/공식 upstream 오류 종류/HTTP 상태만 기록한다. 원문 예외·모델 본문·자격정보는 진단에 기록하지 않는다. 기존 실패 작업에 원인을 추정해 소급 저장하지 않는다.
- 검색 완료 없음, 로그인/확인 지연, 모델 실패, 연결 단절, 출력 계약·크기·내용 검증 오류, 시간 만료를 서로 다른 한국어 안내로 표시한다. 실패 후 부분 내용은 표시 전용이며 preview·서식·승인·저장 대상으로 승격하지 않는다. 복구 버튼은 요청 화면만 열며 연구를 자동 해제하거나 생성 POST를 재실행하지 않는다.
- 고정 Codex 0.153.0의 완료 계약에 맞춰 final/legacy 완료 메시지와 `turn/completed.items`의 최종 메시지를 사용한다. async/commentary·delta만 있는 출력은 완료 draft가 아니다. 재시도 알림은 같은 turn의 최종 상태를 기다리며 앱이 별도 모델 호출을 자동 생성하지 않는다.
- 표·흐름도·본문 길이의 기존 validator 제한을 신규 생성 schema에도 전달한다. 역사 draft/서식 정책을 완화하거나 바꾸지 않는다.
- 별도 세션의 자식이 파이프를 보유하면 deadline 후 정리가 무기한 대기하는 POSIX 결함을 재현해 비차단 입출력·DefaultSelector·유한 정리로 수정했다. FD 1024 초과, UTF-8 분할, 막힌 stdin, 상속 파이프 회귀를 시험했다. Windows Job 경로는 보존했으며 이번 환경에서 Windows 실기기를 검수한 것은 아니다.

연구 자동 해제·유료 fallback·추가 연구 preflight turn·저장 경계 우회는 도입하지 않았다. owner/status fence, 동일 요청 결과 재조회, 독립 새 요청 ID, 내용 검토 → 서식 → 사용자 확정 저장을 유지한다.

### 10.3 로그인 자원 준비와 샘플 계정

`totalBytes += await consume(...)`가 병렬 worker 사이에서 공유 합계를 덮어쓰는 결함을 수정했다. 청크마다 동기적으로 공통 합계를 확인하고 64MiB 초과/실패 즉시 모든 worker를 취소한다. 한 자원 32MiB·24개·SRI·정확한 하위 경로·저속/절약/비활성 보류·제한된 재시도를 유지한다.

SRI 다운로드 후 실제 `WebAssembly.compile`을 수행하고 다운로드·컴파일·후속 엔진 초기화/문서 배치를 구분한다. WASM 인스턴스나 숨은 편집기를 만들지 않으며 로그인은 기다리지 않는다. 대기 45초 뒤 늦은 native 결과를 무시한다. iframe에서 브라우저 코드 캐시 재사용 및 실제 최초 문서 지연 개선은 보장하지 않는다.

기존 기억한 ID 최대 5개는 사전 구성된 샘플 계정 선택과 별개였다. 새 `HWP_SAMPLE_ACCOUNTS=[]`는 기본 비공개/비활성이며 운영자가 공개 합성 ID·라벨을 최대 8개 지정해야 한다. 기존 활성 member 비밀번호 계정만 카드에 표시하고 미생성 ID의 공개 가입 선점을 막는다. 비밀번호·계정 자동 생성/초기화·AI ACL 확대는 하지 않는다. 서로 다른 ID의 문서는 기존 ACL로 격리되고 같은 샘플 ID 이용자들은 문서함을 공유한다. [설정·보존 절차](poc-codex-accounts-prewarm-2026-10-25.md)를 따른다. **현재 운영은 계정 기능이 꺼져 있어 새 이미지·설정 적용 전에는 토큰 화면이 정상**이다.

사용자가 확정한 실제 구성은 **체험 계정 2~3개 선택 → 각 계정 비밀번호 로그인**이다. 최대 8개는 코드의 안전 상한이며 실제로 8개를 만들거나 공개하도록 정한 것이 아니다. 운영 문서에 2개(선택적 세 번째)의 공개 설정, 기존 ID·토큰·과거 소유권·가상 사번 충돌 사전 확인, 대화형 계정별 암호 생성과 격리 확인 절차를 준비했다. 실제 비밀번호·ACL·private env 적용은 미실행이며 운영자 판단 대상이다.

### 10.4 실행 환경·검증·남은 판단

검증한 제품 코드 SHA는 `8a8c9afae4bd809362b8f2eb87507a10e44e1ad3`이며 이후 이 인수인계 갱신은 문서 변경이다. 클라우드 Linux, CPython **3.14.0**과 두 requirements lock, Node **24.15.0**과 package lock을 사용했다. Playwright 1.56.1의 Chromium 141 build 1194 headless-shell을 사용했다. 기본 Chromium 채널은 이 클라우드의 socket 권한 제한으로 시작하지 못했고, 설정에서 지원하는 headless-shell로 실행했다.

| 검사 | 확인된 결과 / 실제 범위 |
|---|---|
| backend 전체 | **단일 전체 실행 1,071 passed, 기존 warning 2개, 244.27초**. subprocess exit=0·JUnit 1,071건·실패/오류/skip 0건·수집 case 일치 확인. 별도 676개+396개 분할의 고유 1,071개도 대조했다. backend 137개 파일 전후 해시 불변 |
| Python 배포 계약 | 75개 통과: deployment 13, isolated host preflight 4, CI 16, smoke 12, developer download 6, published run 24 |
| 보고서 집중 회귀 | 76 passed, 기존 의존성 warning 2개. 합성 실제 stdio이며 실제 모델 호출 0회 |
| frontend | tsc 통과, unit 30/30, mock UI 40/40 |
| Node 계약·build path | 82/82 통과, public path 감사·배포 셸 문법 통과 |
| 실제 고정 Codex | 공식 0.153.0 archive SHA-256 확인 후 CLI flags·app-server initialize/account-read·packaged MCP smoke 통과. 로그인/모델 호출 0회 |
| 최적화 rhwp | 공개 운영 build receipt의 upstream/패치/bridge/바인딩과 7개 정적 자산 SHA-256을 확인. diagnostic=false, WASM `3eb52da9c5602c8036fd70ce0f5f759588224be0069eafb04aed39845310667b` |
| 실제 엔진 build/serve | 검증된 배포 엔진을 재사용한 현재 앱 `/hwp-assistant/` 빌드·served bytes 검사 통과. Docker/Rust 부재로 엔진 소스 재빌드는 하지 않음 |
| 실제 엔진 Node | fill 보존/재열기, 선택·줄바꿈·다중 페이지, 블록 배치/왕복 차이 감지 통과 |
| 실제 엔진 browser | 8 passed / 1 skipped. 격리 SQLite·임시 정적/proxy, 합성 AI. 생성/편집/업로드/버전/재열기, 승인·stale·undo, 응답 유실 재시도, 내용 검토·서식·확정 저장, rich 표/흐름도, 스트림·취소 확인. skip은 실제 Codex 기록물 부재 |

운영 엔진의 해시 검증 재사용은 소스 재빌드나 새 운영 배포를 뜻하지 않는다. manifest에 없는 폰트 자산은 임의로 내려받지 않았으며 완전한 폰트/시각/한컴 검수로 일반화하지 않는다. 이번 로컬 통합은 PostgreSQL·실제 nginx·HTTPS 재시작을 검증한 것이 아니므로 PR의 일회용 컨테이너 CI와 구분한다. 실제 모델 upstream 검색·운영 토큰/샘플 로그인, Safari/Firefox/Windows·한컴·폐쇄망 모델은 미검증이다.

초기 단일 backend 실행은 exit 0이지만 약 60% 뒤 최종 요약이 없어 통과 증거로 계산하지 않았다. 원인은 재현되지 않아 단정하지 않는다. 이후 **분할 두 실행 및 최종 단일 전체 실행 각각의 JUnit·정상 종료·요약과 수집 목록**을 확인했다. 최종 단일 실행이 앞선 불완전 출력을 대체한다. PR CI는 그 정확한 head의 Actions를 함께 확인한다. 비밀정보 없는 수치·환경·한계는 [검증 영수증](evidence/cloud-reliability-2026-10-25.json)에 기록했다.

main 병합·이미지 게시·운영 배포는 별도 사용자 결정이 필요하며 이번 작업에서 수행하지 않는다. 계정 설정을 적용하려면 공개할 합성 ID·비밀번호 계정·별도 AI ACL을 운영자가 선택한다. 보고서 연구 기능은 새 이미지에서 합성 주제로 실제 검색 완료와 최종 draft → 내용 검토 → 서식 → 확정 저장·재열기를 추가 확인해야 한다. 공개 검색 불가가 계속되면 새 진단 종류로 운영 upstream을 점검하고, 연구 옵션 해제는 사용자가 직접 제공 자료 기반 요청을 선택할 때만 사용한다.

DB schema migration은 추가하지 않았다. 적용 전 기존 일관 백업과 실제 게시 SHA 확인을 유지한다. 롤백은 새 이미지/환경을 덮어 추정하지 말고 마지막 성공 배포 영수증의 기존 이미지로 되돌리며 `HWP_SAMPLE_ACCOUNTS=[]`를 사용한다. 기존 계정·문서·버전·세션/사용량 테이블과 세 운영 볼륨은 삭제·초기화하지 않는다.
