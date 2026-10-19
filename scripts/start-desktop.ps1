# Reuse a verified run record, including auto-selected ports. Never stop an app.
[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$ResolveOnly,
    [string]$EnginePublic = '.cache/developer/public',
    [string]$DataDirectory = '.cache/developer/data',
    [string]$StateDirectory = '.cache/desktop-launcher',
    [ValidateRange(1024,65535)][int]$Port = 15190,
    [ValidateRange(1024,65535)][int]$ApiPort = 18190
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'desktop-launch-lib.ps1')
function Resolve-DesktopPath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) { return Get-HwpFullPath $Value }
    return Get-HwpFullPath (Join-Path $projectRoot $Value)
}
$dataRoot = Resolve-DesktopPath $DataDirectory
$engineRoot = Resolve-DesktopPath $EnginePublic
$stateRoot = Resolve-DesktopPath $StateDirectory
$cacheRoot = (Get-HwpFullPath (Join-Path $projectRoot '.cache')) + '\'
if (-not $stateRoot.StartsWith($cacheRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Desktop state must be a dedicated directory inside project .cache.' }
$engine = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $engineRoot 'rhwp/build-info.json') | ConvertFrom-Json
$context = Get-HwpSourceContext $projectRoot
New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
$activePath = Join-Path $stateRoot 'active-run.json'
$pendingPath = Join-Path $stateRoot 'pending-run.json'
function Write-DesktopState([string]$Path, $Value) {
    $temporary = Join-Path $stateRoot ([guid]::NewGuid().ToString('N') + '.tmp')
    $Value | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Find-DesktopRun {
    $active = $null
    try { $active = Get-Content -Raw -Encoding UTF8 -LiteralPath $activePath -ErrorAction Stop | ConvertFrom-Json } catch { }
    $folders = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot '.cache/developer/runs') -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 128)
    foreach ($folder in $folders) {
        try {
            $recordPath = Join-Path $folder.FullName 'run.json'
            $record = Get-Content -Raw -Encoding UTF8 -LiteralPath $recordPath -ErrorAction Stop | ConvertFrom-Json
            $expected = @()
            if ($active -and (Test-HwpSamePath $active.run_directory $folder.FullName)) { $expected = @($active.identities) }
            $identity = Test-HwpRunIdentity -Run $record -RunDirectory $folder.FullName -ProjectRoot $projectRoot -DataDirectory $dataRoot `
                -SourceContext $context -EngineInfo $engine -ExpectedIdentities $expected
            if ($identity) { return $identity }
        } catch { }
    }
    return $null
}
function Show-DesktopRun($Identity, [string]$Kind) {
    $receipt = [ordered]@{kind=$Kind;verified_at=[DateTime]::UtcNow.ToString('o');project_root=$projectRoot;
        web_url=$Identity.web_url;run_directory=$Identity.run_directory;data_directory=$Identity.data_directory;
        source_fingerprint=$Identity.source_fingerprint;identities=$Identity.identities}
    Write-DesktopState $activePath $receipt
    if (-not $NoBrowser) { Start-Process $Identity.web_url }
    Write-Host "HWP Assistant verified: $($Identity.web_url)"
    $receipt | ConvertTo-Json -Depth 8
}
$mutexName = 'Local\HwpAssistantDesktop-' + (Get-HwpBytesHash ([Text.Encoding]::UTF8.GetBytes(($projectRoot + '|' + $dataRoot).ToLowerInvariant())))
$mutex = [Threading.Mutex]::new($false,$mutexName)
$locked = $false
try {
    try { $locked = $mutex.WaitOne(50000) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another desktop launch is still preparing this document store. Existing services were not stopped.' }
    $identity = Find-DesktopRun
    if ($identity) { Show-DesktopRun $identity 'reused verified local run'; return }
    if ($ResolveOnly) { Write-Host 'No matching verified run. No server was started.'; return }
    $child = $null
    try {
        $pending = Get-Content -Raw -Encoding UTF8 -LiteralPath $pendingPath -ErrorAction Stop | ConvertFrom-Json
        $candidate = Get-HwpProcessSnapshot ([int]$pending.pid)
        if ((Test-HwpExactStart $candidate $pending.started_at) -and (Test-HwpSamePath $pending.data_directory $dataRoot) -and
            $pending.source_fingerprint -eq $context.fingerprint -and
            $candidate.command_line.IndexOf((Join-Path $projectRoot 'scripts/dev.ps1'),[StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $child = Get-Process -Id $candidate.pid -ErrorAction Stop
        }
    } catch { }
    if (-not $child) {
        $launchId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
        $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'dev.ps1'),'-NoInstall','-NoBrowser',
            '-EnginePublic',$engineRoot,'-DataDirectory',$dataRoot,'-Port',"$Port",'-ApiPort',"$ApiPort")
        # All paths originate from this checkout; quote each native Windows arg.
        $line = ($arguments | ForEach-Object { '"' + ([regex]::Replace(([regex]::Replace($_,'(\\*)"','$1$1\"')),'(\\+)$','$1$1')) + '"' }) -join ' '
        $child = Start-Process -FilePath 'powershell.exe' -ArgumentList $line -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $stateRoot ($launchId + '.log')) -RedirectStandardError (Join-Path $stateRoot ($launchId + '.err.log'))
        Write-DesktopState $pendingPath @{pid=$child.Id;started_at=$child.StartTime.ToUniversalTime().ToString('o');data_directory=$dataRoot;
            source_fingerprint=$context.fingerprint;launch_id=$launchId}
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(55)
    while ([DateTime]::UtcNow -lt $deadline) {
        $identity = Find-DesktopRun
        if ($identity) { Show-DesktopRun $identity 'started and verified local run'; return }
        $child.Refresh()
        if ($child.HasExited) { throw "Desktop startup failed. Retained logs: $stateRoot. Existing services were not stopped." }
        Start-Sleep -Milliseconds 500
    }
    throw "Desktop startup is still preparing. Retained logs: $stateRoot. A later click will follow this same launch; existing services were not stopped."
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
