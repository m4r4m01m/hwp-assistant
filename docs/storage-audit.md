# SQLite와 HWPX 파일의 읽기 전용 무결성 점검

`scripts/audit-storage.py`는 DB에 기록된 문서 버전과 불변 HWPX 파일이 일치하는지 확인한다. 2026-10-06 운영 후속 작업으로 추가했다. 앱 시작·마이그레이션·저장·복구·삭제·AI 호출은 수행하지 않는다.

## 실행

저장소 루트에서 DB와 **그 DB에 대응하는** 파일 저장소를 명시한다. 보고서는 새 파일만 만들며 기존 파일이나 원본 저장소 내부에 쓸 수 없다.

```powershell
.venv/Scripts/python scripts/audit-storage.py `
  --database data/metadata.db --storage data/files

# .cache 등 이미 있는 별도 폴더의 새로운 경로를 지정한다.
.venv/Scripts/python scripts/audit-storage.py `
  --database data/metadata.db --storage data/files `
  --report .cache/storage-audit-새로운실행이름.json
```

SQLite를 `mode=ro`로 열어 메모리에 일관된 스냅샷을 만든 뒤 원본 DB 연결을 닫는다. 이후 스냅샷과 HWPX를 읽어서 검사한다. WAL이 있는 DB도 SQLite snapshot API로 읽으며 실행 중인 DB 파일을 단순 복사하지 않는다. 파일 본문·문서명·대화 내용·키·임대 토큰을 보고서로 내보내지 않는다.

| 검사 | 실패 코드 예 |
|---|---|
| SQLite 무결성·외래 키 | `sqlite_integrity_failed`, `foreign_key_violation` |
| 문서의 최신 버전과 문서 연결 | `invalid_current_version` |
| 기준 버전의 문서 일치·순환 계보 | `invalid_base_version`, `version_lineage_cycle` |
| UUID 저장 키·경로 경계·링크 | `unsafe_storage_key` |
| 참조된 파일 존재·읽기 가능 여부 | `missing_blob`, `blob_not_file`, `unreadable_blob` |
| 읽는 동안 파일 불변·크기·SHA-256 | `blob_changed_during_read`, `size_mismatch`, `hash_mismatch` |
| 양식·후보 작업·승인 후보·저장 버전의 연결 | `template_document_mismatch`, `task_document_mismatch`, `application_candidate_mismatch`, `application_saved_version_mismatch` |

종료 코드는 `0` 통과, `1` 점검상 불일치, `2` 입력 경로·스키마·보고서 경로 등으로 검사 완료 불가다. `--report` JSON의 `issues`는 해당 식별자와 코드를 제공한다. 콘솔은 합계만 보여준다. 누락 파일을 만들거나 해시를 DB에 맞춰 고치는 기능은 없다. 실패 시 기존 DB·파일·보고서를 보존한 상태로 원인을 조사한다.

DB 스냅샷에 참조되지 않은 파일은 개수만 기록한다. 실행 중 저장이 새 파일을 만들거나 실패 후보가 격리된 경우도 있으므로, 이 개수를 삭제 목록으로 사용하지 않는다. 자동 정리하지 않으며 불일치 판정에도 합치지 않는다.

## 확인한 범위와 한계

합성 DB/파일에서 정상 상태·파일 누락·바이트 손상·경로 이탈·잘못된 최신/기준 버전·순환 계보를 시험했고, 점검 전후 DB와 파일 해시가 같음을 확인했다. 기존 문서함은 2026-10-06 실행에서 문서 33개, 버전/파일 55개, 참조되지 않은 파일 0개로 검사에 통과했다. 원본 보고서는 로컬 `.cache/local-rewrite-2026-10-06-054056/original-storage-audit.json`에 있으며 문서/DB 자체는 저장소 산출물에 포함하지 않는다.

통과는 그 스냅샷의 메타데이터·파일 크기·해시 일치를 뜻한다. HWPX 레이아웃·내용 의미·한컴 호환성·실모델 결과를 검증하지 않는다. 파일은 DB 스냅샷 이후에 읽으므로, 앱의 “한번 확정한 blob을 변경하지 않는다”는 원칙이 전제다. PostgreSQL에서는 이 도구를 사용할 수 없다.

이 도구는 백업이 아니다. DB+파일의 일관된 백업, 별도 환경 복구 시험, 오래된/격리 파일의 보존 정책과 정리는 남은 운영 작업이다. 기존 DB나 저장소에 덮어쓰는 복구 절차를 자동으로 실행하지 않는다.
