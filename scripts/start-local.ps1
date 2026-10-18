<#
Start the current app for local development, using a verified engine and separate persistent document store.
No model is called at startup. Existing data, engine, login and shell environment are preserved.
Keep this terminal open; Ctrl+C stops only the servers launched here. No installation/download.
#>
[CmdletBinding()]
param(
  [string]$EnginePublic = '.cache/developer/public',
  [string]$DataDirectory = '',
  [ValidateRange(1024,65535)][int]$Port = 15190,
  [ValidateRange(1024,65535)][int]$ApiPort = 18190,
  [switch]$OpenBrowser,
  [ValidateRange(0,60)][int]$SmokeSeconds = 0
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
$node = (Get-Command node -ErrorAction Stop).Source
$engineSource = if ([IO.Path]::IsPathRooted($EnginePublic)) { [IO.Path]::GetFullPath($EnginePublic) } else { [IO.Path]::GetFullPath((Join-Path $projectRoot $EnginePublic)) }
if ($Port -eq $ApiPort) { throw 'API and web ports must differ.' }
foreach ($chosenPort in @($Port,$ApiPort)) {
  if (Get-NetTCPConnection -State Listen -LocalPort $chosenPort -ErrorAction SilentlyContinue) {
    throw "Port $chosenPort is already in use. Choose another -Port/-ApiPort. Existing services were not stopped."
  }
}
if (-not (Test-Path -LiteralPath $python)) { throw 'The existing .venv runtime is required. No installation was attempted.' }
& $node (Join-Path $PSScriptRoot 'check-engine-bundle.mjs') (Join-Path $engineSource 'rhwp') --diagnostic
if ($LASTEXITCODE -ne 0) { throw 'The supplied diagnostic engine did not pass verification.' }
& $node (Join-Path $PSScriptRoot 'developer-engine-kit.mjs') check --public $engineSource
if ($LASTEXITCODE -ne 0) { throw 'The engine bridge or license notices do not match this checkout.' }
$engineInfo = Get-Content -Raw -LiteralPath (Join-Path $engineSource 'rhwp/build-info.json') | ConvertFrom-Json
$diagnostic = [bool]$engineInfo.diagnostic
$reviewRoot = Join-Path $projectRoot '.cache/developer'
$dataRoot = if ($DataDirectory) {
  if ([IO.Path]::IsPathRooted($DataDirectory)) { [IO.Path]::GetFullPath($DataDirectory) }
  else { [IO.Path]::GetFullPath((Join-Path $projectRoot $DataDirectory)) }
} else { Join-Path $reviewRoot 'data' }
$runRoot = Join-Path $reviewRoot ('runs/' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
New-Item -ItemType Directory -Path $runRoot | Out-Null
Copy-Item -LiteralPath $engineSource -Destination (Join-Path $runRoot 'public') -Recurse
& $node (Join-Path $projectRoot 'frontend/node_modules/typescript/bin/tsc') --noEmit --project (Join-Path $projectRoot 'frontend/tsconfig.json')
if ($LASTEXITCODE -ne 0) { throw 'TypeScript failed; no servers started.' }
$buildArguments = @((Join-Path $PSScriptRoot 'build-frontend.mjs'),'--public',(Join-Path $runRoot 'public'),'--out',(Join-Path $runRoot 'app-dist'))
if ($diagnostic) { $buildArguments += '--diagnostic' }
& $node @buildArguments
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic app build failed; no servers started.' }

$environmentNames = @('DATABASE_URL','STORAGE_DIR','HWP_AUTH_TOKENS','HWP_LEGACY_OWNER','HWP_REWRITE_PROVIDER','HWP_CODEX_USER','HWP_CODEX_API_URL','LLM_PROVIDER')
$savedEnvironment = @{}
foreach ($name in $environmentNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$children = [Collections.Generic.List[object]]::new()
$proof = @{ kind='local developer launcher'; diagnostic=$diagnostic; engine_wasm_sha256=$engineInfo.coreBuild.wasm_sha256; started_at=[DateTime]::UtcNow.ToString('o'); web_url="http://127.0.0.1:$Port"; data_directory=$dataRoot; provider='codex'; startup_generates_candidates=$false; services=@(); result='starting' }
function Quote-ReviewArgument([string]$Value) {
  $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
  $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
  return '"' + $escaped + '"'
}
function Start-ReviewService([string]$Name, [string]$Executable, [string[]]$Arguments, [int]$ServicePort, [string]$HealthPath) {
  $argumentLine = ($Arguments | ForEach-Object { Quote-ReviewArgument $_ }) -join ' '
  $child = Start-Process -FilePath $Executable -ArgumentList $argumentLine -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runRoot "$Name.log") -RedirectStandardError (Join-Path $runRoot "$Name.err.log")
  $children.Add($child)
  $proof.services += @{ name=$Name; launcher_pid=$child.Id; port=$ServicePort }
  $deadline = [DateTime]::UtcNow.AddSeconds(30)
  while ([DateTime]::UtcNow -lt $deadline) {
    $child.Refresh()
    if ($child.HasExited) { throw "$Name exited. Logs: $runRoot" }
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$ServicePort$HealthPath" -TimeoutSec 2
      if ($response.StatusCode -eq 200) {
        $owner = (Get-NetTCPConnection -State Listen -LocalPort $ServicePort -ErrorAction Stop | Select-Object -First 1).OwningProcess
        $ancestorId = [int]$owner
        for ($depth = 0; $depth -lt 8 -and $ancestorId -ne $child.Id; $depth++) {
          $ancestor = Get-CimInstance Win32_Process -Filter "ProcessId = $ancestorId"
          if (-not $ancestor) { break }
          $ancestorId = [int]$ancestor.ParentProcessId
        }
        if ($ancestorId -ne $child.Id) { throw 'Port belongs to another process.' }
        $proof.services[-1].listener_pid = [int]$owner
        return
      }
    } catch { }
    Start-Sleep -Milliseconds 100
  }
  throw "$Name startup timed out. Logs: $runRoot"
}
try {
  $env:DATABASE_URL = 'sqlite:///' + (Join-Path $dataRoot 'metadata.db').Replace('\','/')
  $env:STORAGE_DIR = Join-Path $dataRoot 'files'
  $env:HWP_AUTH_TOKENS = ''; $env:HWP_LEGACY_OWNER = ''
  $env:HWP_REWRITE_PROVIDER = 'codex'; $env:HWP_CODEX_USER = 'local'
  $env:HWP_CODEX_API_URL = "http://127.0.0.1:$ApiPort"
  $env:LLM_PROVIDER = ''
  Start-ReviewService 'api' $python @('-m','uvicorn','app.main:app','--app-dir','backend','--host','127.0.0.1','--port',"$ApiPort") $ApiPort '/api/health'
  $serveArguments = @((Join-Path $PSScriptRoot 'serve-built-preview.mjs'),'--dist',(Join-Path $runRoot 'app-dist'),'--port',"$Port",'--api',"http://127.0.0.1:$ApiPort")
  if ($diagnostic) { $serveArguments += '--diagnostic' }
  Start-ReviewService 'web' $node $serveArguments $Port '/'
  & $node (Join-Path $PSScriptRoot 'verify-served-build.mjs') --url "http://127.0.0.1:$Port/" --root (Join-Path $runRoot 'app-dist') --out (Join-Path $runRoot 'served.json') --built
  if ($LASTEXITCODE -ne 0) { throw 'Served bytes do not match the new app/engine bundle.' }
  $proof.result = 'ready'
  $proof | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runRoot 'run.json') -Encoding utf8
  Write-Host "Open http://127.0.0.1:$Port ; Codex is used only when you request candidates."
  Write-Host "Separate persistent review documents: $dataRoot"
  Write-Host 'Keep this terminal open. Ctrl+C stops these servers; saved review documents remain.'
  if ($OpenBrowser) { Start-Process "http://127.0.0.1:$Port/" }
  $finishAt = if ($SmokeSeconds) { [DateTime]::UtcNow.AddSeconds($SmokeSeconds) } else { [DateTime]::MaxValue }
  while ([DateTime]::UtcNow -lt $finishAt) {
    foreach ($child in $children) { $child.Refresh(); if ($child.HasExited) { throw 'A review service exited. Check retained logs.' } }
    Start-Sleep -Milliseconds 500
  }
  $proof.result = 'smoke-passed'
} catch {
  $proof.result = 'failed'; $proof.error = $_.Exception.Message
  throw
} finally {
  foreach ($child in $children) {
    $child.Refresh(); if ($child.HasExited) { continue }
    $all = @(Get-CimInstance Win32_Process); $ids = @([int]$child.Id)
    do {
      $next = @($all | Where-Object { $_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids })
      if ($next.Count -gt 0) { $ids += @($next | ForEach-Object { [int]$_.ProcessId }) }
    } while ($next.Count -gt 0)
    [array]::Reverse($ids)
    foreach ($processId in $ids) { Stop-Process -Id $processId -ErrorAction SilentlyContinue }
  }
  foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
  $proof.finished_at = [DateTime]::UtcNow.ToString('o')
  $proof.remaining_listeners = @($proof.services | Where-Object { Get-NetTCPConnection -State Listen -LocalPort $_.port -ErrorAction SilentlyContinue } | ForEach-Object { $_.name })
  $proof | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runRoot 'run.json') -Encoding utf8
  Write-Host "Run record: $runRoot"
}
