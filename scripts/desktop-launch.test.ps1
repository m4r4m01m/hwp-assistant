# Pure identity fixtures. No real services, documents, model, or browser.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'desktop-launch-lib.ps1')
$runDirectory = Join-Path $projectRoot ('.cache/desktop-identity-tests/' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $runDirectory 'app-dist/rhwp') -Force | Out-Null
$dataDirectory = Join-Path $runDirectory 'data'
$engine = [pscustomobject]@{coreBuild=@{wasm_sha256=('a'*64)};bridge_sha256=('b'*64)}
$engine | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runDirectory 'app-dist/rhwp/build-info.json') -Encoding UTF8
$context = [pscustomobject]@{fingerprint=('c'*64);latest_write_utc=[DateTime]::MinValue}
$start = '2026-10-19T13:00:02.0000001Z'
$run = [pscustomobject]@{result='ready';project_root=$projectRoot;run_directory=$runDirectory;data_directory=$dataDirectory;
    source_fingerprint=$context.fingerprint;engine_wasm_sha256=$engine.coreBuild.wasm_sha256;started_at='2026-10-19T13:00:00Z';
    web_url='http://127.0.0.1:15192';services=@(
        [pscustomobject]@{name='api';port=18192;launcher_pid=100;listener_pid=101;launcher_started_at=$start;listener_started_at=$start},
        [pscustomobject]@{name='web';port=15192;launcher_pid=200;listener_pid=200;launcher_started_at=$start;listener_started_at=$start})}
$states = @{
    100=[pscustomobject]@{pid=100;started_at=$start;parent_pid=50;command_line=('"' + (Join-Path $projectRoot '.venv/Scripts/python.exe') + '" "-m" "uvicorn" "app.main:app" "--port" "18192"')}
    101=[pscustomobject]@{pid=101;started_at=$start;parent_pid=100;command_line='"python.exe" "-m" "uvicorn" "app.main:app" "--port" "18192"'}
    200=[pscustomobject]@{pid=200;started_at=$start;parent_pid=50;command_line=('"node.exe" "' + (Join-Path $projectRoot 'scripts/serve-built-preview.mjs') + '" "--dist" "' + (Join-Path $runDirectory 'app-dist') + '" "--port" "15192"')}
}
$ports = @{18192=101;15192=200}
$lookup = {param($targetId) $states[$targetId]}.GetNewClosure()
$portLookup = {param($port) $ports[$port]}.GetNewClosure()
$http = {$true}
$checks = 0
function Assert-Identity($Value, [bool]$Expected, [string]$Name) {
    if ([bool]$Value -ne $Expected) { throw "FAIL: $Name" }
    $script:checks++; Write-Host "PASS: $Name"
}
function Inspect-Run($Record = $run, [object[]]$Previous = @(), [scriptblock]$Http = $http) {
    return Test-HwpRunIdentity -Run $Record -RunDirectory $runDirectory -ProjectRoot $projectRoot -DataDirectory $dataDirectory `
        -SourceContext $context -EngineInfo $engine -ExpectedIdentities $Previous -ProcessLookup $lookup -PortLookup $portLookup -HttpCheck $Http
}
$identity = Inspect-Run
Assert-Identity ($identity -and $identity.web_url -eq 'http://127.0.0.1:15192/') $true 'actual run URL is reused even when preferred port differs'
$old = $run.data_directory; $run.data_directory = Join-Path $runDirectory 'other-data'
Assert-Identity (Inspect-Run) $false 'different document store is rejected'; $run.data_directory=$old
$states[200].started_at='2026-10-19T13:00:02.0000002Z'
Assert-Identity (Inspect-Run) $false 'reused PID with different exact start tick is rejected'; $states[200].started_at=$start
$ports[15192]=201
Assert-Identity (Inspect-Run) $false 'port owned by another process is rejected'; $ports[15192]=200
$old=$states[200].command_line; $states[200].command_line='HWPX "--port" "15192"'
Assert-Identity (Inspect-Run) $false 'HWPX page or generic process text cannot establish app identity'; $states[200].command_line=$old
$old=$run.source_fingerprint; $run.source_fingerprint='stale'
Assert-Identity (Inspect-Run) $false 'stale application source is rejected'; $run.source_fingerprint=$old
Assert-Identity (Inspect-Run -Http {$false}) $false 'HTTP bytes and health must also match'
$legacy = $run | ConvertTo-Json -Depth 8 | ConvertFrom-Json
$legacy.PSObject.Properties.Remove('source_fingerprint')
foreach ($service in $legacy.services) { $service.PSObject.Properties.Remove('launcher_started_at'); $service.PSObject.Properties.Remove('listener_started_at') }
Assert-Identity (Inspect-Run -Record $legacy) $true 'legacy receipt requires launch window plus exact commands and ancestry'
$states[200].started_at='2026-10-19T13:00:03.0000001Z'
Assert-Identity (Inspect-Run -Record $legacy -Previous $identity.identities) $false 'captured legacy identity rejects later PID reuse'; $states[200].started_at=$start
$states[200].started_at='2026-10-19T14:00:02.0000001Z'
Assert-Identity (Inspect-Run -Record $legacy) $false 'legacy process outside original startup window is rejected'
$fingerprint = Get-HwpSourceContext $projectRoot
Assert-Identity ($fingerprint.fingerprint -match '^[a-f0-9]{64}$') $true 'current source fingerprint is deterministic SHA-256'
$tokens=$null; $parseErrors=$null
[void][Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'start-desktop.ps1'),[ref]$tokens,[ref]$parseErrors)
Assert-Identity (@($parseErrors).Count -eq 0) $true 'desktop entry parses under Windows PowerShell 5.1'
Assert-Identity (Test-HwpExactStart @{started_at=$start} ([datetime]$start)) $true 'DateTime JSON values retain exact fractional start ticks'
Write-Host "$checks desktop identity fixture checks passed; no service was started or stopped."
