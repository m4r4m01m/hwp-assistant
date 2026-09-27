# Native diagnostic WASM build. Does not replace the running app's public engine.
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location $taskRoot
& .venv/Scripts/python scripts/prepare-patched-core.py
if ($LASTEXITCODE -ne 0) { throw 'Pinned core preparation failed' }
$env:RUSTUP_HOME = Join-Path $taskRoot '.cache/rust-toolchain/rustup'
$env:CARGO_HOME = Join-Path $taskRoot '.cache/rust-toolchain/cargo'
$env:PATH = "$env:CARGO_HOME/bin;$env:PATH"
$env:CARGO_TARGET_DIR = Join-Path $taskRoot '.cache/rhwp-core-fix/target/pr-review'
$bindgen = Join-Path $taskRoot '.cache/rust-toolchain/wasm-bindgen-0.2.127-x86_64-pc-windows-msvc/wasm-bindgen.exe'
if (-not (Test-Path -LiteralPath $bindgen)) { throw 'Install pinned wasm-bindgen 0.2.127 first; see docs/engine-empty-fill-fix.md' }
if ((& $bindgen --version) -ne 'wasm-bindgen 0.2.127') { throw 'Unexpected wasm-bindgen version' }
if ((& rustc +1.93.1 --version) -notmatch '^rustc 1\.93\.1 ') { throw 'Unexpected Rust version' }
Push-Location .cache/rhwp-core-fix
try {
    & rustfmt --check --edition 2021 src/serializer/hwpx/header.rs src/serializer/hwpx/shape.rs src/serializer/hwpx/char_shapes.rs
    if ($LASTEXITCODE -ne 0) { throw 'Patched Rust format check failed' }
    & cargo build --locked -p rhwp --lib --release --target wasm32-unknown-unknown
    if ($LASTEXITCODE -ne 0) { throw 'WASM compilation failed' }
    & $bindgen --target web --out-dir pkg target/pr-review/wasm32-unknown-unknown/release/rhwp.wasm
    if ($LASTEXITCODE -ne 0) { throw 'WASM binding generation failed' }
} finally { Pop-Location }
& node scripts/record-patched-core.mjs
if ($LASTEXITCODE -ne 0) { throw 'Core provenance recording failed' }
& node scripts/test-engine-fill.mjs .cache/rhwp-core-fix/pkg
if ($LASTEXITCODE -ne 0) { throw 'Real WASM fill regression failed' }
