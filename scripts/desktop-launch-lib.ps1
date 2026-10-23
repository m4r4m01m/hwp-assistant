# Identity checks and scoped build settings shared by the local launchers.
function Invoke-HwpLocalRootBuild {
    param([Parameter(Mandatory=$true)][scriptblock]$Build)
    # The local preview server and API proxy serve at /. A deployment shell may
    # have a public subpath configured; do not carry it into this local build.
    $previousBase = [Environment]::GetEnvironmentVariable('HWP_PUBLIC_BASE_PATH', 'Process')
    try {
        [Environment]::SetEnvironmentVariable('HWP_PUBLIC_BASE_PATH', '/', 'Process')
        & $Build
    } finally {
        [Environment]::SetEnvironmentVariable('HWP_PUBLIC_BASE_PATH', $previousBase, 'Process')
    }
}
function Get-HwpBytesHash([byte[]]$Bytes) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}
function Get-HwpFileHash([string]$Path) { return Get-HwpBytesHash ([IO.File]::ReadAllBytes($Path)) }
function Get-HwpFullPath([string]$Path) { return [IO.Path]::GetFullPath($Path).TrimEnd('\','/') }
function Test-HwpSamePath([string]$Left, [string]$Right) {
    return [string]::Equals((Get-HwpFullPath $Left),(Get-HwpFullPath $Right),[StringComparison]::OrdinalIgnoreCase)
}
function Get-HwpSourceContext([string]$ProjectRoot) {
    $files = @()
    foreach ($folder in @('frontend/src','backend/app')) {
        $files += @(Get-ChildItem -LiteralPath (Join-Path $ProjectRoot $folder) -File -Recurse | Where-Object {
            $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -in @('.ts','.tsx','.css','.py','.json','.hwpx')
        })
    }
    foreach ($name in @('frontend/index.html','frontend/compare-renderer.html','frontend/package-lock.json',
        'scripts/assistant-bridge.ts.txt','scripts/engine-lock.json','scripts/build-frontend.mjs','scripts/comparison-core.mjs',
        'scripts/start-local.ps1','scripts/desktop-launch-lib.ps1','scripts/public-base.mjs')) {
        $files += Get-Item -LiteralPath (Join-Path $ProjectRoot $name)
    }
    $text = [Text.StringBuilder]::new(); $latest = [DateTime]::MinValue
    foreach ($file in @($files | Sort-Object FullName)) {
        $name = $file.FullName.Substring($ProjectRoot.Length).TrimStart('\','/').Replace('\','/')
        [void]$text.Append($name + ':' + (Get-HwpFileHash $file.FullName) + "`n")
        if ($file.LastWriteTimeUtc -gt $latest) { $latest = $file.LastWriteTimeUtc }
    }
    return [pscustomobject]@{ fingerprint=(Get-HwpBytesHash ([Text.Encoding]::UTF8.GetBytes($text.ToString()))); latest_write_utc=$latest }
}
function Get-HwpProcessSnapshot([int]$TargetId) {
    $process = Get-Process -Id $TargetId -ErrorAction Stop
    $detail = Get-CimInstance Win32_Process -Filter "ProcessId = $TargetId" -ErrorAction Stop
    if (-not $detail) { throw 'Process identity is unavailable.' }
    return [pscustomobject]@{ pid=$TargetId; started_at=$process.StartTime.ToUniversalTime().ToString('o');
        parent_pid=[int]$detail.ParentProcessId; command_line=[string]$detail.CommandLine }
}
function Get-HwpPortOwners([int]$Port) {
    return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
}
function Test-HwpExactStart($Snapshot, $Expected) {
    return ([datetime]$Snapshot.started_at).ToUniversalTime().Ticks -eq ([datetime]$Expected).ToUniversalTime().Ticks
}
function Confirm-HwpHttpAssets([string]$WebUrl, [string]$RunDirectory) {
    $health = Invoke-RestMethod -Uri ($WebUrl.TrimEnd('/') + '/api/health') -TimeoutSec 2 -MaximumRedirection 0
    if ($health.ok -ne $true) { return $false }
    foreach ($name in @('index.html','rhwp/build-info.json')) {
        $response = Invoke-WebRequest -UseBasicParsing -Uri ($WebUrl.TrimEnd('/') + '/' + $name) -TimeoutSec 3 -MaximumRedirection 0
        $actual = Get-HwpBytesHash ($response.RawContentStream.ToArray())
        if ($actual -ne (Get-HwpFileHash (Join-Path $RunDirectory ('app-dist/' + $name)))) { return $false }
    }
    return $true
}
function Test-HwpRunIdentity {
    param($Run, [string]$RunDirectory, [string]$ProjectRoot, [string]$DataDirectory, $SourceContext, $EngineInfo,
        [object[]]$ExpectedIdentities = @(),
        [scriptblock]$ProcessLookup = { param($targetId) Get-HwpProcessSnapshot $targetId },
        [scriptblock]$PortLookup = { param($port) Get-HwpPortOwners $port },
        [scriptblock]$HttpCheck = { param($url,$folder) Confirm-HwpHttpAssets $url $folder })
    try {
        if ($Run.result -ne 'ready' -or -not (Test-HwpSamePath $Run.data_directory $DataDirectory) -or
            $Run.engine_wasm_sha256 -ne $EngineInfo.coreBuild.wasm_sha256) { return $null }
        if ($Run.project_root -and -not (Test-HwpSamePath $Run.project_root $ProjectRoot)) { return $null }
        if ($Run.run_directory -and -not (Test-HwpSamePath $Run.run_directory $RunDirectory)) { return $null }
        if ($Run.source_fingerprint) {
            if ($Run.source_fingerprint -ne $SourceContext.fingerprint) { return $null }
        } elseif ($SourceContext.latest_write_utc -gt ([datetime]$Run.started_at).ToUniversalTime()) { return $null }
        $url = [Uri]$Run.web_url
        if ($url.Scheme -ne 'http' -or $url.Host -ne '127.0.0.1' -or $url.UserInfo -or $url.Query -or $url.Fragment -or
            $url.AbsolutePath -ne '/' -or $url.Port -lt 1024) { return $null }
        $services = @($Run.services)
        if ($services.Count -ne 2 -or @($services | Where-Object name -eq 'web').Count -ne 1 -or
            @($services | Where-Object name -eq 'api').Count -ne 1) { return $null }
        $web = $services | Where-Object name -eq 'web'
        if ($web.port -ne $url.Port) { return $null }
        $info = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RunDirectory 'app-dist/rhwp/build-info.json') | ConvertFrom-Json
        if ($info.coreBuild.wasm_sha256 -ne $EngineInfo.coreBuild.wasm_sha256 -or $info.bridge_sha256 -ne $EngineInfo.bridge_sha256) { return $null }
        $identities = @()
        foreach ($service in $services) {
            if ($service.port -lt 1024 -or $service.port -gt 65535) { return $null }
            $owners = @(& $PortLookup ([int]$service.port))
            if ($owners.Count -ne 1 -or $owners[0] -ne $service.listener_pid) { return $null }
            $listener = & $ProcessLookup ([int]$service.listener_pid)
            $launcher = & $ProcessLookup ([int]$service.launcher_pid)
            if (-not $listener -or -not $launcher) { return $null }
            foreach ($pair in @(@{snapshot=$listener;expected=$service.listener_started_at},@{snapshot=$launcher;expected=$service.launcher_started_at})) {
                if ($pair.expected) {
                    if (-not (Test-HwpExactStart $pair.snapshot $pair.expected)) { return $null }
                } else {
                    # Legacy receipts predate exact start fields. Require the
                    # original launch window plus the specific command/ancestry,
                    # then retain their exact identities in the desktop receipt.
                    $started = ([datetime]$pair.snapshot.started_at).ToUniversalTime()
                    $runStarted = ([datetime]$Run.started_at).ToUniversalTime()
                    if ($started -lt $runStarted.AddSeconds(-1) -or $started -gt $runStarted.AddSeconds(60)) { return $null }
                }
                $previous = @($ExpectedIdentities | Where-Object pid -eq $pair.snapshot.pid)
                if ($previous.Count -and -not (Test-HwpExactStart $pair.snapshot $previous[0].started_at)) { return $null }
                $identities += [pscustomobject]@{pid=$pair.snapshot.pid;started_at=$pair.snapshot.started_at}
            }
            $ancestor = $listener; $matched = $false
            for ($depth=0; $depth -lt 8; $depth++) {
                if ($ancestor.pid -eq $launcher.pid) { $matched=$true; break }
                if (-not $ancestor.parent_pid) { break }
                $ancestor = & $ProcessLookup ([int]$ancestor.parent_pid)
            }
            if (-not $matched) { return $null }
            $command = [string]$listener.command_line
            if ($command -notmatch ('"?--port"?\s+"?' + [regex]::Escape([string]$service.port) + '"?(\s|$)')) { return $null }
            if ($service.name -eq 'web') {
                if ($command.IndexOf((Join-Path $ProjectRoot 'scripts/serve-built-preview.mjs'),[StringComparison]::OrdinalIgnoreCase) -lt 0 -or
                    $command.IndexOf((Join-Path $RunDirectory 'app-dist'),[StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
            } elseif ($command -notmatch 'uvicorn"?\s+"?app\.main:app' -or
                $launcher.command_line.IndexOf((Join-Path $ProjectRoot '.venv'),[StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
        }
        if (-not (& $HttpCheck ($url.AbsoluteUri) $RunDirectory)) { return $null }
        return [pscustomobject]@{web_url=$url.AbsoluteUri;run_directory=$RunDirectory;data_directory=$DataDirectory;
            source_fingerprint=$SourceContext.fingerprint;identities=@($identities | Sort-Object pid -Unique)}
    } catch { return $null }
}
