<#
Windows browser regression with a verified, copied engine and synthetic HTTP model.
Never starts Codex or an actual model, uses existing data, or replaces a public engine.
All processes created here are stopped; databases and evidence are retained in a NEW output directory.
#>
param(
  [Parameter(Mandatory = $true)][string]$EnginePublic,
  [string]$OutDir = '',
  [switch]$BuiltApp,
  [string]$TestPattern = '',
  [ValidateRange(0,7200)][int]$SoakSeconds = 0,
  [switch]$ContextSoak,
  [switch]$ReportMock,
  [string]$RecordedReports = ''
)
$ErrorActionPreference = 'Stop'
if ($RecordedReports -and -not $ReportMock) { throw 'Recorded reports require the isolated test-only -ReportMock server.' }
if ($SoakSeconds -gt 0 -and $SoakSeconds -lt 180) { throw 'Real-clock soak must run for at least 180 seconds, or use 0 to skip it.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
$node = (Get-Command node -ErrorAction Stop).Source
$engineSource = if ([IO.Path]::IsPathRooted($EnginePublic)) { [IO.Path]::GetFullPath($EnginePublic) } else { [IO.Path]::GetFullPath((Join-Path $projectRoot $EnginePublic)) }
if (-not $OutDir) { $OutDir = '.cache/isolated-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8) }
$runRoot = if ([IO.Path]::IsPathRooted($OutDir)) { [IO.Path]::GetFullPath($OutDir) } else { [IO.Path]::GetFullPath((Join-Path $projectRoot $OutDir)) }
if (Test-Path -LiteralPath $runRoot) { throw 'Output directory already exists. Choose a new directory; earlier evidence is preserved.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Install backend requirements in .venv first.' }
& $node (Join-Path $PSScriptRoot 'check-engine-bundle.mjs') (Join-Path $engineSource 'rhwp') --diagnostic
if ($LASTEXITCODE -ne 0) { throw 'Engine verification failed; no services were started.' }

$children = [Collections.Generic.List[object]]::new()
$ports = [Collections.Generic.HashSet[int]]::new()
$environmentNames = @('DATABASE_URL','STORAGE_DIR','HWP_AUTH_TOKENS','HWP_LEGACY_OWNER','HWP_CODEX_USER','HWP_LOCAL_AI_SETUP',
  'HWP_REWRITE_PROVIDER','HWP_CHAT_PROVIDER','HWP_REVIEW_PROVIDER','HWP_REWRITE_MAX_ACTIVE','LLM_PROVIDER','LLM_BASE_URL','LLM_MODEL','LLM_API_KEY','LLM_TIMEOUT_SECONDS',
  'HWP_E2E_URL','HWP_E2E_AUTH_URL','HWP_E2E_LOCAL_URL','HWP_E2E_MOCK_URL','HWP_E2E_SOAK_SECONDS','HWP_E2E_CONTEXT_SOAK','HWP_E2E_REPORT_MOCK','HWP_REPORT_FIXTURES','PYTHONPATH','NO_COLOR','FORCE_COLOR')
$savedEnvironment = @{}
foreach ($name in $environmentNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$proof = @{ kind='isolated Windows regression'; started_at=[DateTime]::UtcNow.ToString('o'); actual_model_calls=0; mock=$true; result='running'; services=@(); commands=@() }

function New-TestPort {
  do {
    $socket = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $socket.Start()
    $chosen = $socket.LocalEndpoint.Port
    $socket.Stop()
  } while (-not $ports.Add($chosen))
  return $chosen
}

function Quote-Argument([string]$Value) {
  # Windows CommandLineToArgvW quoting, including paths with spaces and trailing backslashes.
  $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
  $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
  return '"' + $escaped + '"'
}

function Start-TestService([string]$Name, [string]$Executable, [string[]]$Arguments, [int]$Port, [string]$HealthPath) {
  $argumentLine = ($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
  $child = Start-Process -FilePath $Executable -ArgumentList $argumentLine -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runRoot "$Name.log") -RedirectStandardError (Join-Path $runRoot "$Name.err.log")
  $children.Add($child)
  $proof.services += @{ name=$Name; launcher_pid=$child.Id; port=$Port }
  $proof | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $runRoot 'run.json') -Encoding utf8
  $deadline = [DateTime]::UtcNow.AddSeconds(30)
  while ([DateTime]::UtcNow -lt $deadline) {
    $child.Refresh()
    if ($child.HasExited) { throw "$Name exited during startup. See its log in $runRoot" }
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port$HealthPath" -TimeoutSec 2
      if ($response.StatusCode -eq 200) {
        $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -First 1
        $ownerId = [int]$listener.OwningProcess
        $ancestorId = $ownerId
        for ($depth = 0; $depth -lt 8 -and $ancestorId -ne $child.Id; $depth++) {
          $ancestor = Get-CimInstance Win32_Process -Filter "ProcessId = $ancestorId"
          if (-not $ancestor) { break }
          $ancestorId = [int]$ancestor.ParentProcessId
        }
        if ($ancestorId -ne $child.Id) { throw 'Port was acquired by another process; preserving it.' }
        $proof.services[-1].listener_pid = $ownerId
        return
      }
    } catch { }
    Start-Sleep -Milliseconds 100
  }
  throw "$Name startup timed out"
}

function Stop-TestChildren {
  # Use the exact process objects we created. A dead/reused PID is never a cleanup target.
  foreach ($child in ($children | Select-Object -Last $children.Count)) {
    $child.Refresh()
    if ($child.HasExited) { continue }
    $all = @(Get-CimInstance Win32_Process)
    $ids = @([int]$child.Id)
    do {
      $next = @($all | Where-Object { $_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids })
      if ($next.Count -gt 0) { $ids += @($next | ForEach-Object { [int]$_.ProcessId }) }
    } while ($next.Count -gt 0)
    [array]::Reverse($ids)
    foreach ($processId in $ids) { Stop-Process -Id $processId -ErrorAction SilentlyContinue }
  }
}

try {
  New-Item -ItemType Directory -Path $runRoot | Out-Null
  Copy-Item -LiteralPath $engineSource -Destination (Join-Path $runRoot 'public') -Recurse
  $proof.engine = Get-Content -LiteralPath (Join-Path $runRoot 'public/rhwp/build-info.json') -Raw | ConvertFrom-Json
  $env:HWP_LEGACY_OWNER = ''
  $env:HWP_REPORT_FIXTURES = if ($RecordedReports) { [IO.Path]::GetFullPath((Join-Path $projectRoot $RecordedReports)) } else { '' }
  $proof.recorded_model_results = [bool]$RecordedReports
  $env:HWP_REWRITE_MAX_ACTIVE = '2'
  [Environment]::SetEnvironmentVariable('NO_COLOR', $null, 'Process')
  [Environment]::SetEnvironmentVariable('FORCE_COLOR', $null, 'Process')
  $mockPort = New-TestPort
  Start-TestService 'mock' $python @('-m','uvicorn','mock_local_provider:app','--app-dir','backend/tests','--host','127.0.0.1','--port',"$mockPort") $mockPort '/v1/models'
  if ($BuiltApp) {
    & $node (Join-Path $projectRoot 'frontend/node_modules/typescript/bin/tsc') --noEmit --project (Join-Path $projectRoot 'frontend/tsconfig.json')
    if ($LASTEXITCODE -ne 0) { throw 'TypeScript failed' }
    & $node (Join-Path $PSScriptRoot 'build-frontend.mjs') --diagnostic --public (Join-Path $runRoot 'public') --out (Join-Path $runRoot 'app-dist')
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic app build failed' }
  }
  foreach ($kind in 'general','auth','local') {
    $apiPort, $webPort = (New-TestPort), (New-TestPort)
    $env:DATABASE_URL = 'sqlite:///' + (Join-Path $runRoot "$kind.db").Replace('\','/')
    $env:STORAGE_DIR = Join-Path $runRoot "$kind-files"
    $env:HWP_AUTH_TOKENS = if ($kind -eq 'auth') { '{"alice":"alice-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bob":"bob-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","eve":"eve-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}' } else { '' }
    $env:HWP_CODEX_USER = if ($kind -eq 'auth') { 'alice' } elseif ($kind -eq 'local') { 'not-local' } else { 'local' }
    $env:HWP_REWRITE_PROVIDER = if ($kind -eq 'local') { 'llm' } else { 'codex' }
    $env:HWP_LOCAL_AI_SETUP = '0'
    # The existing general suite explicitly verifies that editing works without AI.
    $env:LLM_PROVIDER = if ($kind -eq 'local') { 'openai-compatible' } else { '' }
    $env:LLM_BASE_URL = if ($kind -eq 'local') { "http://127.0.0.1:$mockPort/v1" } else { '' }
    $env:LLM_MODEL = if ($kind -eq 'local') { 'hwp-synthetic-fixture' } else { '' }
    $env:LLM_API_KEY = if ($kind -eq 'local') { 'unused-synthetic-test-key' } else { '' }
    $env:LLM_TIMEOUT_SECONDS = '5'
    $apiApp = if ($ReportMock) { 'tests.report_mock_app:app' } else { 'app.main:app' }
    $env:PYTHONPATH = Join-Path $projectRoot 'backend'
    Start-TestService "$kind-api" $python @('-m','uvicorn',$apiApp,'--app-dir','backend','--host','127.0.0.1','--port',"$apiPort") $apiPort '/api/health'
    if ($BuiltApp) {
      Start-TestService "$kind-web" $node @((Join-Path $PSScriptRoot 'serve-built-preview.mjs'),'--diagnostic','--dist',(Join-Path $runRoot 'app-dist'),'--port',"$webPort",'--api',"http://127.0.0.1:$apiPort") $webPort '/'
    } else {
      Start-TestService "$kind-web" $node @((Join-Path $PSScriptRoot 'serve-engine-preview.mjs'),'--public',(Join-Path $runRoot 'public'),'--port',"$webPort",'--api',"http://127.0.0.1:$apiPort") $webPort '/'
    }
    $servedRoot = if ($BuiltApp) { Join-Path $runRoot 'app-dist' } else { Join-Path $runRoot 'public' }
    $verifyArguments = @((Join-Path $PSScriptRoot 'verify-served-build.mjs'),'--url',"http://127.0.0.1:$webPort/",'--root',$servedRoot,'--out',(Join-Path $runRoot "$kind-served.json"))
    if ($BuiltApp) { $verifyArguments += '--built' }
    & $node @verifyArguments
    if ($LASTEXITCODE -ne 0) { throw 'Served application/engine bytes do not match this run.' }
    $variable = switch ($kind) { 'general' {'HWP_E2E_URL'} 'auth' {'HWP_E2E_AUTH_URL'} 'local' {'HWP_E2E_LOCAL_URL'} }
    [Environment]::SetEnvironmentVariable($variable, "http://127.0.0.1:$webPort", 'Process')
  }
  $env:HWP_E2E_MOCK_URL = "http://127.0.0.1:$mockPort"
  $env:HWP_E2E_SOAK_SECONDS = "$SoakSeconds"
  $env:HWP_E2E_CONTEXT_SOAK = if ($ContextSoak) { '1' } else { '0' }
  $env:HWP_E2E_REPORT_MOCK = if ($ReportMock) { '1' } else { '0' }
  $testArguments = @((Join-Path $projectRoot 'frontend/node_modules/@playwright/test/cli.js'),'test','--config',(Join-Path $projectRoot 'frontend/playwright.config.ts'),'--output',(Join-Path $runRoot 'results'))
  if ($TestPattern) { $testArguments += @('--grep',$TestPattern) }
  if ($BuiltApp -and -not $TestPattern) { $testArguments += @('--grep','MOCK report|MOCK HTTP local provider|MOCK provider|MOCK delayed provider') }
  Push-Location $runRoot
  try {
    # Native stderr warnings are not PowerShell failures; judge the CLI exit code.
    $ErrorActionPreference = 'Continue'
    & $node @testArguments 2>&1 | Tee-Object -FilePath (Join-Path $runRoot 'e2e.log')
    $testExit = $LASTEXITCODE
  } finally { $ErrorActionPreference = 'Stop'; Pop-Location }
  $proof.commands += @{ command='Playwright E2E'; exit_code=$testExit; built_app=[bool]$BuiltApp; pattern=$TestPattern }
  if ($testExit -ne 0) { throw 'E2E failed. Logs and artifacts are preserved.' }
  $proof.result = 'passed'
} catch {
  $proof.result = 'failed'
  $proof.error = $_.Exception.Message
  throw
} finally {
  Stop-TestChildren
  foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
  if (Test-Path -LiteralPath $runRoot) {
    $proof.finished_at = [DateTime]::UtcNow.ToString('o')
    $proof.remaining_listeners = @($proof.services | Where-Object {
      Get-NetTCPConnection -State Listen -LocalPort $_.port -ErrorAction SilentlyContinue
    } | ForEach-Object { $_.name })
    $proof | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $runRoot 'run.json') -Encoding utf8
    Write-Host "Evidence retained: $runRoot"
  }
}
