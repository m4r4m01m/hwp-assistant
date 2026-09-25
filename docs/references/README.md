# 자료·산출물 이관 기록

기준일: 2026-09-25 · 문서 정리 v0.5 / 저장소 접근 기록 갱신 2026-09-26

## 1. 이번에 실제 작성·확보한 것

현재 문서는 이전 설계서와 후속 사용자 지시를 바탕으로 재작성했다. 최신 방향은 LLM 우선 검증, A=AI/B=시스템 병렬 진행안, 외부 저비용 개발, 사내 기존 모델 활용, HWPX 왕복 보존이다.

| 자료 | 이번 처리 | 보관 위치 |
|---|---|---|
| 초기 통합 설계안 MD | 원본 바이트 미확보, 추출 텍스트 확보·보관 | [초기안](../archive/internal-doc-assistant-plan.md) |
| v0.2 통합 설계안 MD | 원본 바이트 미확보, 추출 텍스트 확보·보관 | [v0.2](../archive/internal-doc-assistant-plan-v0.2.md) |
| v0.3 통합 설계안 MD | 원본 바이트 미확보, 추출 텍스트 확보·보관 | [v0.3](../archive/internal-doc-assistant-plan-v0.3.md) |
| v0.4 통합 설계안 MD | 기존 작업 컨테이너의 추출 텍스트 확보·보관 | [v0.4](../archive/internal-doc-assistant-plan-v0.4.md) |
| 후속 사용자 지시 | 현재 프로젝트 대화 및 관련 과거 대화 맥락에서 반영 | [결정 기록](../decisions-and-open-questions.md) |

파일 목록상의 원본 크기와 이번 확보 텍스트의 크기는 다르다. 정밀 내역은 [source-manifest.json](source-manifest.json)에 기록한다. 문서 원본과 바이트 동일한 백업을 만들었다고 표현하지 않는다.

## 2. 원본을 포함하지 않은 자료

| 과거 ZIP 자료명 | 목록상 크기 | 이번 상태 |
|---|---:|---|
| internal-doc-assistant-design-kit.zip | 44,621 bytes | 목록 존재만 확인, 원본 미포함 |
| internal-doc-assistant-design-kit-v0.2.zip | 55,606 bytes | 목록 존재만 확인, 원본 미포함 |
| internal-doc-assistant-design-kit-v0.3.zip | 70,325 bytes | 목록 존재만 확인, 원본 미포함 |
| internal-doc-assistant-design-kit-v0.4.zip | 56,827 bytes | 원본 가져오기 시도 실패, 미포함 |

ZIP 내부 파일을 열거나 추출하지 못했다. 보관 MD에 포함된 코드 블록·WBS·지시서 텍스트는 유지했지만 이를 원래 ZIP 구성 파일로 복원했다고 주장하지 않는다. 이번에 만드는 ZIP은 **새 문서 묶음**이며 위 과거 ZIP과 다르다.

사무관리규정·시행세칙 참고 사진은 이전 보관 이력이 있으나 이번 문서 묶음에 포함하지 않았다. 조문을 새로 판독·확정하지 않았으며 실제 규칙 구현 근거로 사용한 것으로 표시하지 않는다. 인증정보·내부 주소·업무 문서·모델·폰트 원본도 포함하지 않는다.

## 3. GitHub 접근과 반영 기준

2026-09-25에는 계정 조회만 성공하고 저장소 조회가 404로 실패했다. 당시에는 정리본 ZIP과 패치만 보관했고 전체 문서의 원격 반영을 완료하지 못했다.

2026-09-26 재시도에서 GitHub API로 다음을 확인했다.

| 항목 | 확인값 |
|---|---|
| 저장소 | `m4r4m01m/hwp-assistant` |
| 저장소 ID | `1386590343` |
| 소유자 유형 | Organization |
| 공개 범위 | private |
| 기본 브랜치 | main |
| 연결 권한 | pull=true, push=true |
| 반영 전 main 커밋 | `c697b6f7a83d97b5ecc86510faa89a154aff39b5` |
| 반영 전 파일 | `docs/README.md` 1개 |
| 기존 README blob | `9db5d99aacff9da35c773760430f5fee596a4e9b` |

이번 변경은 위 기준 커밋의 기존 README를 갱신하고 현재 기준 문서·이전 설계서 보관본·출처 명세를 `docs/`에 추가한다. 기존 커밋 이력은 유지하고 강제 푸시하지 않는다. 저장소의 공개 범위나 접근 설정은 바꾸지 않는다. 실제 반영 커밋은 이 파일의 Git 기록으로 확인한다.

## 4. 이전 접근 오류 기록의 해석

이전 404는 당시 연결에서 관찰한 결과이며 현재 접근 상태를 뜻하지 않는다. 접근 문제 안내 때문에 보관했던 외부 자료는 아래와 같다. 이 문서 반영 작업에서는 외부 안내의 최신성 검증을 다시 수행하지 않았다.

- [GitHub REST API troubleshooting](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api)
- [OpenAI — Connecting GitHub to ChatGPT](https://help.openai.com/en/articles/11145903-connecting-github-to-chatgpt)

저장소를 공개로 되돌리거나 인증 토큰을 문서·대화에 포함하지 않는다. 비공개 상태여도 사내 자료의 반출·공유 승인을 별도로 확인한다.

## 5. 검증 범위

문서 반영 전 검증 대상은 UTF-8 텍스트, 현재 문서의 로컬 상대 링크, JSON 파싱, 확보 텍스트/보관본의 해시, 원본 문서 묶음 ZIP 무결성, 반영 전 원격 트리와 변경 경로다. 반영 후에는 원격 main의 커밋과 파일별 Git blob 해시를 로컬 준비본과 대조한다. 보관본에 남은 옛 ZIP 경로·외부 URL은 현재 문서 상대 링크 검사 대상이 아니다.

앱 테스트·HWPX 왕복·모델 호출·배포 시험을 수행한 것으로 쓰지 않는다. 문서 패치를 로컬 시험한 결과를 원격 커밋 성공으로 쓰지 않는다.
