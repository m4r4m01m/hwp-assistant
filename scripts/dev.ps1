<#
Windows developer setup + local launch. Installs project dependencies, never system tools.
Use a matching developer-engine.zip, or build the pinned core with Docker/native tools.
No model generation, credential copying, original DB migration, or existing process stop.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$NoInstall,
    [switch]$NoStart,
    [switch]$NoBrowser,
    [string]$EnginePublic = '',
    [string]$EngineArchive = '',
    [string]$EngineArchiveSha256 = '',
    [ValidateSet('Auto','Native','Docker')][string]$BuildEngine = 'Auto',
    [string]$DataDirectory = '.cache/developer/data',
    [ValidateRange(1024,65535)][int]$Port = 15190,
    [ValidateRange(1024,65535)][int]$ApiPort = 18190,
    [ValidateRange(0,60)][int]$SmokeSeconds = 0
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'developer-tools.ps1')
Push-Location $projectRoot
try {
    if (-not (Test-Path 'frontend/package-lock.json') -or -not (Test-Path 'backend/requirements-mcp.lock.txt') -or
        -not (Test-Path 'scripts/engine-lock.json') -or
        (Get-Content -Raw 'frontend/package.json' | ConvertFrom-Json).name -ne 'hwp-assistant') {
        throw 'This launcher must be inside the HWP Assistant project. No installation was started.'
    }
    Write-Host "[HWP] Project: $projectRoot"
    $checks = @(Get-DeveloperPrerequisites)
    $checks | Format-Table name,ok,version -AutoSize | Out-Host
    if (@($checks | Where-Object { -not $_.ok }).Count) {
        throw 'Install Git, Node 22.12+ (tested 24.15.0), Python 3.11+ (tested 3.14.4), then reopen this launcher. Official links: docs/developer-quickstart.md. System tools are never silently installed.'
    }
    $node = (Get-Command node).Source
    $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    $codexCommand = Get-Command codex -ErrorAction SilentlyContinue
    $codexExecutable = if ($env:HWP_CODEX_EXECUTABLE) { $env:HWP_CODEX_EXECUTABLE } elseif ($codexCommand) { $codexCommand.Source } else { '' }
    if ($codexExecutable) {
        # Login status does not start a model turn. Do not retain stdout/auth data.
        & (Get-Command python).Source (Join-Path $PSScriptRoot 'check-codex-login.py') $codexExecutable
        if ($LASTEXITCODE -eq 0) { Write-Host '[HWP] Codex login found. No AI request was made.' }
        else { Write-Host '[HWP] Codex login is required for AI: run codex login using your own ChatGPT account.' }
    } else {
        Write-Host '[HWP] Codex CLI not found. Editing works; AI needs Codex CLI + your own ChatGPT login. See docs/developer-quickstart.md.'
    }
    if ($CheckOnly) {
        Write-Host '[HWP] Prerequisite check complete. No installation or server startup.'
        return
    }
    $developerRoot = Join-Path $projectRoot '.cache/developer'
    New-Item -ItemType Directory -Force -Path $developerRoot | Out-Null
    $receiptPath = Join-Path $developerRoot 'dependencies.json'
    $expected = @{
        python_lock = (Get-FileHash 'backend/requirements.lock.txt' -Algorithm SHA256).Hash
        mcp_lock = (Get-FileHash 'backend/requirements-mcp.lock.txt' -Algorithm SHA256).Hash
        npm_lock = (Get-FileHash 'frontend/package-lock.json' -Algorithm SHA256).Hash
    }
    $installed = if (Test-Path -LiteralPath $receiptPath) { Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json } else { $null }
    if (-not (Test-Path -LiteralPath $python)) {
        if ($NoInstall) { throw '.venv is missing. Rerun without -NoInstall to create project-only Python dependencies.' }
        Assert-DeveloperInstallSafe
        Invoke-DeveloperStep (Get-Command python).Source @('-m','venv','.venv') 'Create project Python environment'
    }
    if (-not $NoInstall -and (-not $installed -or $installed.python_lock -ne $expected.python_lock -or $installed.mcp_lock -ne $expected.mcp_lock)) {
        Assert-DeveloperInstallSafe
        Invoke-DeveloperStep $python @('-m','pip','install','-r','backend/requirements-mcp.lock.txt') 'Install locked API and MCP dependencies'
    }
    if (-not $NoInstall -and (-not $installed -or $installed.npm_lock -ne $expected.npm_lock -or -not (Test-Path 'frontend/node_modules/.package-lock.json'))) {
        Assert-DeveloperInstallSafe
        $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
        Push-Location frontend
        try { Invoke-DeveloperStep $npm @('ci') 'Install locked frontend dependencies' } finally { Pop-Location }
    }
    Invoke-DeveloperStep $python @('-c','import fastapi, sqlalchemy, uvicorn, mcp') 'Check API/MCP imports'
    if (-not (Test-Path 'frontend/node_modules/typescript/bin/tsc')) { throw 'Frontend dependencies are missing. Rerun without -NoInstall.' }
    if (-not $NoInstall) { $expected | ConvertTo-Json | Set-Content -LiteralPath $receiptPath -Encoding utf8 }
    $engineRoot = if ($EnginePublic) { Resolve-DeveloperPath $EnginePublic } else { Join-Path $developerRoot 'public' }
    if ($BuildEngine -eq 'Auto' -and -not $EngineArchive -and -not (Test-Path (Join-Path $engineRoot 'rhwp/build-info.json')) -and (Test-Path 'developer-engine.zip')) {
        $EngineArchive = 'developer-engine.zip'
    }
    if (-not $EnginePublic -and -not $EngineArchive -and -not (Test-Path (Join-Path $engineRoot 'rhwp/build-info.json')) -and $BuildEngine -eq 'Auto') {
        Invoke-DeveloperStep $python @((Join-Path $PSScriptRoot 'download-developer-engine.py')) 'Download the hash-pinned developer engine (your GitHub clone identity)'
        $engineLock = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'developer-engine.lock.json') | ConvertFrom-Json
        $EngineArchive = Join-Path $developerRoot ('downloads/' + $engineLock.asset)
        $EngineArchiveSha256 = $engineLock.sha256
    }
    if ($EngineArchive) {
        $archive = Resolve-DeveloperPath $EngineArchive
        $importArgs = @((Join-Path $PSScriptRoot 'developer-engine-kit.mjs'),'unpack','--archive',$archive,'--out',$engineRoot)
        if ($EngineArchiveSha256) { $importArgs += @('--sha256',$EngineArchiveSha256) }
        if (-not (Test-Path (Join-Path $engineRoot 'rhwp/build-info.json'))) {
            Invoke-DeveloperStep $node $importArgs 'Verify and import developer engine kit'
        } else { Write-Host '[HWP] Existing engine retained; it will be verified below.' }
    }
    if (-not (Test-Path (Join-Path $engineRoot 'rhwp/build-info.json'))) {
        $docker = Get-Command docker -ErrorAction SilentlyContinue
        $useDocker = $BuildEngine -eq 'Docker'
        if ($BuildEngine -eq 'Auto' -and $docker) {
            & $docker.Source info --format '{{.OSType}}' 2>$null | Out-Null
            $useDocker = $LASTEXITCODE -eq 0
        }
        if ($useDocker) {
            Invoke-DeveloperStep $node @((Join-Path $PSScriptRoot 'export-release-core.mjs')) 'Build pinned release core (Docker Linux)'
            Invoke-DeveloperStep $node @((Join-Path $PSScriptRoot 'build-engine.mjs'),'--out',(Join-Path $engineRoot 'rhwp')) 'Build pinned release editor'
        } else {
            $bindgen = Join-Path $projectRoot '.cache/rust-toolchain/wasm-bindgen-0.2.127-x86_64-pc-windows-msvc/wasm-bindgen.exe'
            $rustup = Join-Path $projectRoot '.cache/rust-toolchain/cargo/bin/rustup.exe'
            if (-not (Test-Path -LiteralPath $bindgen) -or -not (Test-Path -LiteralPath $rustup)) {
                throw 'No verified engine kit or native build tools. Put developer-engine.zip and its .sha256 beside the launcher, or use Docker Linux (-BuildEngine Docker). Native prerequisite instructions: docs/developer-quickstart.md. No unpatched npm engine fallback.'
            }
            Invoke-DeveloperStep 'powershell.exe' @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'build-patched-core.ps1')) 'Build pinned native diagnostic core'
            Invoke-DeveloperStep $node @((Join-Path $PSScriptRoot 'build-engine.mjs'),'--diagnostic','--core-dir','.cache/rhwp-core-fix/pkg','--out',(Join-Path $engineRoot 'rhwp')) 'Build pinned diagnostic editor'
        }
    }
    Invoke-DeveloperStep $node @((Join-Path $PSScriptRoot 'developer-engine-kit.mjs'),'check','--public',$engineRoot) 'Verify current engine and bridge'
    if ($NoStart) { Write-Host '[HWP] Setup complete. Run Start HWP Assistant.cmd to start.'; return }
    $webPort = Get-DeveloperFreePort $Port
    $backendPort = Get-DeveloperFreePort $ApiPort @($webPort)
    $startArgs = @{ EnginePublic=$engineRoot; DataDirectory=(Resolve-DeveloperPath $DataDirectory); Port=$webPort; ApiPort=$backendPort; SmokeSeconds=$SmokeSeconds }
    if (-not $NoBrowser -and -not $SmokeSeconds) { $startArgs.OpenBrowser = $true }
    & (Join-Path $PSScriptRoot 'start-local.ps1') @startArgs
} finally { Pop-Location }
