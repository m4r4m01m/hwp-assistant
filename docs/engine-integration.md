# rhwp 통합 근거

확인일: 2026-09-26. 최신 main도 조사했지만 배포 일관성을 위해 npm 0.8.6의 gitHead `e8800c8def63449808a4092798442652ed460552`로 고정했다. SDK, Studio, WASM을 다른 커밋으로 섞지 않는다.

| 기능 | 실제 확인한 소스/계약 | 앱 사용 |
|---|---|---|
| 임베드 | `npm/editor/index.js`, `index.d.ts`, README의 `createEditor` / `studioUrl` | `/rhwp/index.html?chrome=embed`, 동일 출처 iframe |
| 파일 로드 | `loadFile(data, fileName, options)` | 서버의 버전 바이트, 기본 SDK가 숨기는 로드 안내를 `suppressDialogs:false`로 표시 |
| HWPX 출력 | SDK `exportHwpx`; Studio `WasmBridge.exportHwpxWithReport` | byte-only 출력 대신 손실 보고가 함께 있는 브리지 snapshot 사용 |
| 새 문서 | `main.ts`의 `createNewDocument`, `WasmBridge.createNewDocument` | 작은 비동기 브리지를 통해 정식 초기화/캔버스 경로 사용 |
| 변경 | SDK `onDocumentChanged`는 agent apply/revert 전용 | 일반 입력도 잡기 위해 내부 `document-mutated`를 read-only 구독 |
| 저장 완료 | `notifySaved` / `completeHostSave` | export 당시 revision과 epoch를 동기 확인한 뒤 서버 성공에 한해 dirty 해제 |
| 선택 | `getSelectionContext` | 본문 문단 target; SDK에 원문/서식 SHA 자체는 없음 |
| 원문 증거 | `document-agent/controller.ts`의 기존 `collectTargetEvidence` | 자체 브리지에서 재사용, SHA 정규형을 새로 복제하지 않음 |
| AI 적용 | `applyTextCommand` | epoch, seq, document SHA, 원문/서식/인접 문맥 SHA, target 검증 및 snapshot undo |
| 툴바·표 | Studio 기본 메뉴/도구 상자 | 직접 재사용. 자체 한글 엔진이나 HTML 대체 편집기 없음 |
| 외부 자원 | Studio vite define `RHWP_DISABLE_EXTERNAL_WEBFONTS` | 외부 폰트 끄고 번들 fonts/WASM 동봉 |

공식 소스: [SDK](https://github.com/edwardkim/rhwp/tree/e8800c8def63449808a4092798442652ed460552/npm/editor), [Studio main](https://github.com/edwardkim/rhwp/blob/e8800c8def63449808a4092798442652ed460552/rhwp-studio/src/main.ts), [exact command controller](https://github.com/edwardkim/rhwp/blob/e8800c8def63449808a4092798442652ed460552/rhwp-studio/src/document-agent/controller.ts), [출력 손실 보고](https://github.com/edwardkim/rhwp/blob/e8800c8def63449808a4092798442652ed460552/rhwp-studio/src/core/export-content-loss.ts), [embed 모드](https://github.com/edwardkim/rhwp/blob/e8800c8def63449808a4092798442652ed460552/rhwp-studio/src/ui/chrome-mode.ts).

우리 브리지는 공식 SDK API라고 주장하지 않는다. `EditorAdapter` 역시 우리 앱의 경계다. 별도 postMessage eval·전체 문자열 치환·문서 JSON 재생성을 추가하지 않는다. 브리지는 동일 출처 호스트에서만 접근한다. 호스트 저장은 반드시 API에서 세션/기준 버전/멱등성 검사 후 확정한다.

엔진 제약: 안전 AI 명령은 4,000 Unicode code point 이하, 단일 글자 서식의 본문 문단 전체에 한정된다. 컨트롤/필드/표 셀/머리말/각주/글상자는 제외된다. 문단의 줄바꿈 제어문자는 replacement에서 허용되지 않는다. 손실 보고는 알려진 직렬화 손실을 찾는 장치이며 모든 조판 차이를 판정하지 않는다.
