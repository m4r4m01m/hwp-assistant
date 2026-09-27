# 한글 2018의 채우기 없음 출력 수정

고정 rhwp 0.8.6(`e8800c8def63449808a4092798442652ed460552`)의 HWPX 색상 출력에만 적용하는 다운스트림 패치다. 원본·과거 HWPX를 후처리하지 않는다. 최초 진단은 빈 winBrush 생략이었지만 한컴에서 글자 음영의 검은 띠가 남았다. 최종 패치는 한컴 2018 자체 저장본에서 확인한 `#FFFFFFFF` 센티넬을 header/shape/char_shapes의 색상 출력 함수에 사용한다. 실제 검정 `#000000`과 흰색 `#FFFFFF`는 다른 값으로 보존한다.

실행 결과와 한컴 증거는 [검증 기록](verification-2026-09-27.md)에 별도로 추가한다. 이 문서의 명령 존재만으로 실행 성공을 의미하지 않는다.

## 전제와 범위

- 기존 `frontend/node_modules`, `.cache/rhwp`의 고정 체크아웃, 프로젝트 `.venv`가 필요하다.
- Windows의 Rust 1.93.1 / wasm32-unknown-unknown과 wasm-bindgen 0.2.127을 사용한다. 현재 PC에는 작업 전용 `.cache/rust-toolchain`에 설치했다. 전역 PATH나 사용자 인증을 변경하지 않았다.
- Visual Studio C++ Build Tools는 이 PC의 기존 설치를 사용한다.
- 이 빌드는 Cargo release 최적화까지 수행하지만 **wasm-opt와 표준 Docker 배포 빌드는 미검증**이다. 업스트림 지침에 따라 native 진단 빌드로 구분한다.
- 기본 `npm run engine`은 이제 `.cache/rhwp-core-release/pkg`의 패치·해시·최적화 기록을 요구한다. 누락 시 미수정 npm 코어로 대체하지 않고 중단한다. 기존 실행 중인 15174의 엔진은 그대로 보존돼 있다.

## 정식 빌드 경로

Docker Linux engine이 있는 환경에서 `frontend` 폴더의 `npm run engine:core`, `npm run engine`, `npm run build` 순으로 실행한다. 또는 루트에서 `docker compose build web`으로 코어·Studio·앱·nginx 이미지를 함께 빌드한다. 실행 레시피는 `frontend/Dockerfile`에 통합했다. **이 PC에는 Docker가 없어 컨테이너 실행 결과는 미검증**이다.

레시피는 Rust 1.93.1 / wasm-pack 0.15.0 / wasm-bindgen 0.2.127 / Binaryen 117을 고정한다. Binaryen Linux amd64 압축 파일은 공식 SHA-256으로 확인한다. 현재 레시피의 지원 대상은 linux/amd64다. wasm-pack의 Cargo metadata와 build 모두 upstream wrapper로 `--locked`를 적용하고, 최적화는 별도 명령으로 실행해 실제 입력·출력·실행 파일 해시를 기록한다. 최적화가 생략됐는데 성공으로 기록되는 경로를 허용하지 않는다.

`scripts/engine-lock.json`은 기준 커밋·패치·수정 소스 해시를 기록한다. `build-info.json`은 실제 배포 JS/WASM/HTML 파일 해시와 브리지 해시를 추가로 보관한다. `node scripts/check-engine-bundle.mjs frontend/dist/rhwp`로 배포 파일을 다시 확인할 수 있다. 이 로컬 출처 기록은 서명된 공급망 증명이나 전체 호환성 인증은 아니다.

Docker export 폴더에 검증된 코어가 있으면 그대로 사용하며, 다른 파일이 있으면 덮어쓰지 않고 오류를 낸다. 기존 vendor checkout의 commit이나 허용하지 않은 소스 변경도 덮어쓰지 않는다. 기본 엔진을 실제 교체하기 전에는 열린 문서의 저장 상태를 별도로 확인해야 한다.

## 작업 전용 도구 설치 위치

Rust 공식 `rustup-init.exe`를 `.cache/rust-toolchain`에 내려받고 공식 SHA-256과 대조한 뒤 다음 인자로 설치했다. 설치 파일·인증 파일을 저장소에 포함하지 않는다.

```powershell
$env:RUSTUP_HOME = Join-Path (Get-Location) '.cache/rust-toolchain/rustup'
$env:CARGO_HOME = Join-Path (Get-Location) '.cache/rust-toolchain/cargo'
& .cache/rust-toolchain/rustup-init.exe -y --no-modify-path --profile minimal --default-toolchain 1.93.1 --target wasm32-unknown-unknown
```

wasm-bindgen 공식 릴리스 `0.2.127`의 `wasm-bindgen-0.2.127-x86_64-pc-windows-msvc.tar.gz`를 `.cache/rust-toolchain`에 풀면 `build-patched-core.ps1`이 그 경로를 사용한다. 다운로드 주소는 `https://github.com/wasm-bindgen/wasm-bindgen/releases/download/0.2.127/wasm-bindgen-0.2.127-x86_64-pc-windows-msvc.tar.gz`다. 임의 최신 버전으로 대체하지 않는다.

## 재현 명령

프로젝트 루트의 PowerShell에서:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-patched-core.ps1
node scripts/build-engine.mjs --diagnostic --core-dir .cache/rhwp-core-fix/pkg --out .cache/hancom-next-preview/public/rhwp
node scripts/serve-engine-preview.mjs --public .cache/hancom-next-preview/public --port 15179
```

과거 15176 시험 서버와 증거 폴더는 그대로 두고 새 폴더/포트를 사용한다. 현재의 완성 번들 시험 창은 15178이다. 서버 명령은 터미널을 점유하므로 하네스는 두 번째 PowerShell에서 실행한다. 기존 API 18001도 계속 실행 중이어야 한다. 다른 포트는 `--port`로 지정할 수 있으며 사용 중인 포트를 빼앗거나 기존 프로세스를 종료하지 않는다.

첫 명령은 고정 소스를 별도 빌드 폴더에 준비하고 변경된 원본 소스를 발견하면 중단한다. 패치가 적용된 파일 전체와 Cargo.lock을 확인하고, `cargo build --locked -p rhwp --lib --release --target wasm32-unknown-unknown`, 고정 wasm-bindgen, 실제 WASM 채우기 회귀 검사를 수행한다. 둘째 명령은 기존 Studio·SDK·브리지와 수정 코어를 묶어 지정 폴더에 출력한다. `build-info.json`의 patch/WASM/binding/source 해시로 식별한다.

앱 전체의 진단 번들이 필요하면 기존 `frontend/dist`를 덮어쓰지 않고 다음처럼 만든다. TypeScript 검사도 먼저 실행한다.

```powershell
cd frontend
npm exec tsc -- --noEmit
cd ..
node scripts/build-frontend.mjs --diagnostic --public .cache/hancom-next-preview/public --out .cache/hancom-next-preview/app-dist
node scripts/check-engine-bundle.mjs .cache/hancom-next-preview/app-dist/rhwp --diagnostic
# 별도 터미널에서 완성 앱과 nginx.conf의 보안 헤더로 시험 (nginx 컨테이너 자체는 아님):
node scripts/serve-built-preview.mjs --diagnostic --dist .cache/hancom-next-preview/app-dist --port 15180
```

새 번들 해시 기록이 없는 과거 시험 폴더는 먼저 위의 명시적 진단 엔진 빌드로 새 별도 폴더에 생성한다. 과거 증거 파일을 갱신하거나 이전 시험 성공을 소급하지 않는다.

```powershell
node scripts/test-engine-fill.mjs .cache/rhwp-core-fix/pkg
node scripts/verify-hancom-fill.mjs --url http://127.0.0.1:15179 --out .cache/hancom-next-preview/evidence
```

브라우저 하네스는 합성 문서만 만들며 **AI 호출을 차단**한다. 사용자 승인 시험을 대신하지 않는다. 제품 UI로 두 번 저장하고 이전 버전 보존·다운로드·웹 재열기를 검사한다. 성공 후에도 사용자가 닫을 때까지 브라우저를 유지한다. 같은 증거 폴더의 재사용은 거부하므로 추가 실행에는 새 `--out`을 지정한다.

한컴에서 직접 다른 이름으로 저장한 파일이 실제로 생긴 다음에만 다음 단계를 실행한다.

```powershell
node scripts/verify-hancom-fill.mjs --url http://127.0.0.1:15179 --out .cache/hancom-next-preview/evidence --input .cache/hancom-next-preview/evidence/hancom-saved.hwpx
```

이 입력에는 한컴에서 직접 추가한 `한컴 수정 확인: 흰 배경에서 수정하고 저장했습니다.` 문단이 있어야 한다. 파일은 제품 UI로 별도 문서에 업로드하며 기존 문서에 덮어쓰지 않는다. 한컴 UI 조작을 이 명령이 대신 수행하지 않는다.
