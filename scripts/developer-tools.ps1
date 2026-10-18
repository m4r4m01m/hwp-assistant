# Shared helpers. No shell command construction, global install, login, or AI call.
function Invoke-DeveloperStep([string]$Executable, [string[]]$Arguments, [string]$Label) {
    Write-Host "[HWP] $Label"
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE). Existing documents and services were not stopped." }
}
function Resolve-DeveloperPath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) { return [IO.Path]::GetFullPath($Value) }
    return [IO.Path]::GetFullPath((Join-Path $projectRoot $Value))
}
function Assert-DeveloperInstallSafe {
    # Installing into a checkout whose Python/Vite runtime is in use can remove
    # loaded packages on Windows. Refuse before pip/npm touch that environment.
    $active = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in @('node.exe','python.exe','pythonw.exe') -and $_.CommandLine -and
        $_.CommandLine.IndexOf($projectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    if ($active.Count) {
        throw 'This checkout has active Python/Node services. Dependency installation was not started. Use a fresh clone, close those services, or use -NoInstall when its locked dependencies are already prepared. Existing processes were not stopped.'
    }
}
function Get-DeveloperPrerequisites {
    $checks = @()
    foreach ($tool in @('git','node','python')) {
        $command = Get-Command $tool -ErrorAction SilentlyContinue
        $checks += [pscustomobject]@{ name=$tool; ok=[bool]$command; path=if ($command) { $command.Source } else { $null } }
    }
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCommand) {
        $version = (& $nodeCommand.Source -p 'process.versions.node').Trim()
        $checks += [pscustomobject]@{ name='Node >= 22.12'; ok=([version]$version -ge [version]'22.12.0'); version=$version }
    }
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $version = (& $pythonCommand.Source -c 'import sys; print(sys.version.split()[0])').Trim()
        $checks += [pscustomobject]@{ name='Python >= 3.11'; ok=($LASTEXITCODE -eq 0 -and [version]$version -ge [version]'3.11.0'); version=$version }
    }
    return $checks
}
function Get-DeveloperFreePort([int]$Preferred, [int[]]$Excluded = @()) {
    for ($candidate = $Preferred; $candidate -le [Math]::Min(65535, $Preferred + 99); $candidate++) {
        if ($candidate -in $Excluded) { continue }
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $candidate)
        try { $listener.Start(); return $candidate } catch [Net.Sockets.SocketException] { } finally { $listener.Stop() }
    }
    throw "No free loopback port in $Preferred..$($Preferred + 99). Existing services were not stopped."
}
