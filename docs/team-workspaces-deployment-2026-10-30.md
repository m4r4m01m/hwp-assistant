# 팀 문서함 실험 브랜치 배포

사용자가 2026-10-30에 최신 main을 실험 브랜치에 병합하고, 전체 회귀 검사 뒤 기존 홈서버를 실험 버전으로 교체하도록 요청했다. main에 실험 기능을 병합하는 작업은 포함하지 않는다.

## 범위와 소스

- 기준 main: `d9683d85498cb856fee67c5ba657182793fcdae9`
- 실험 원본: `ae2c97f9c317d35bccddd1b230a0348c55af4d2b`
- 검증용 PR: [#57](https://github.com/m4r4m01m/hwp-assistant/pull/57). main 병합용 PR이 아니다.
- 대상: `codex/experiment-team-workspaces-2026-10-28`

main의 Mattermost 기능과 실험의 공간·개인 문서·작업본을 함께 유지한다. 검색은 제목/본문 후보 및 지연 후 결과 재검사에 `document_read_scope`를 사용한다. 전송 직전 기존 `require_access(..., fresh=True)`도 유지한다. Mattermost 저장본 링크는 자동으로 공동편집에 참여하지 않으며, 명시적 편집은 `/edit-target`을 거친다.

실험 기능은 공간 4개, 작업본/검토 4개, 개인 문서/폴더 3개, 소속 1개 등 12개 테이블을 추가한다. 기존 문서·과거 버전·파일을 실험 공간으로 자동 변환하지 않는다. 배포 전 DB와 문서 파일의 동시 백업을 유지한다.

## 실행 방법

`HWP deployment`의 `Run workflow`에서 대상 실험 브랜치를 선택하고 `deploy=true`로 실행한다. CLI를 사용한다면:

```sh
gh workflow run hwp-deploy.yml \
  --repo m4r4m01m/hwp-assistant \
  --ref codex/experiment-team-workspaces-2026-10-28 \
  -f deploy=true
```

선택한 브랜치의 실행 당시 SHA를 검사·빌드·이미지 태그·운영 checkout에 동일하게 사용한다. 전체 Python/프런트엔드/UI 성공 뒤 GitHub-hosted runner에서 이미지·PostgreSQL·HTTPS·실제 엔진을 검증하고 게시한다. 이후 기존 `[self-hosted, linux, x64, hwp-deploy]` runner가 `/opt/hwp-assistant/production.env`와 기존 백업/교체 절차를 사용한다. `hwp-home-production` 동시성 그룹은 기존 배포와 공유한다.

수동 게시/배포는 `main`과 지정한 실험 브랜치만 허용한다. PR, push, 임의 브랜치는 게시하거나 운영 runner에 진입하지 않는다. runner 재등록·서버 Compose 수동 수정·초기화는 필요하지 않다. `hwp-production` Environment에 별도 branch 제한/승인자가 있으면 GitHub가 적용하는 해당 절차를 먼저 충족해야 한다. runner Offline/queued와 Environment 대기/차단을 구분한다.

`HWP deploy published images`는 계속 main의 성공한 게시 실행만 허용한다. 실험 배포 실패 시 원인을 해결한 뒤 같은 `HWP deployment` 실행의 실패 job 재실행을 사용한다. 보호 규칙을 완화하거나 PR 이벤트로 운영 배포를 대신하지 않는다.

## 검수와 복구

자동 검사는 합성 자료를 사용한다. 실제 Mattermost·Codex 호출 성공과 구분한다. 운영 배포 후 다음을 별도로 확인한다.

1. 운영 job의 백업·교체·로컬 HTTP 및 `https://pwsb-svr.taile7b6c4.ts.net/hwp-assistant/` 공개 HTTPS 검사 성공과 실행 SHA를 확인한다.
2. 기존 개인 계정으로 로그인하고 새 개인 문서의 격리, 팀 문서함, 자동 작업본 재사용, 다른 사용자 검토/통합을 합성 문서로 확인한다.
3. 기존 Mattermost 연결을 유지한 계정에서 `/hwp` 검색/초안 입력창/개인 메시지/웹 링크를 확인한다. 다른 팀·다른 사람의 개인 문서·미공유 작업본이 검색에 노출되지 않아야 한다. 설정과 계정 연결을 이 배포에서 재발급하지 않는다.
4. 원본 및 과거 버전이 보존되고, 재시작 후 새 작업본/폴더/권한이 유지되는지 확인한다.

기존 main으로 복귀해도 추가 테이블을 삭제하거나 DB를 자동으로 하향 이행하지 않는다. 특히 실험에서 만든 개인/팀 문서는 기존 main의 권한 코드가 해석하지 못하므로, 검수 자료 생성 후 단순 이미지 롤백은 접근 권한을 바꿀 수 있다. 먼저 쓰기를 멈추고 DB·파일을 함께 백업한 뒤 기존 main과의 권한 호환성을 검토한다. 배포 전 백업으로 되돌릴 경우에는 이후 생성 데이터의 보존/이관 계획과 별도 복구 승인이 필요하다. 운영 볼륨 삭제·자동 복원은 하지 않는다.

이 문서는 절차와 구현 범위다. 완료 여부는 `PROJECT_CONTEXT.md`의 정확한 SHA·CI·배포 실행 기록을 따른다.
