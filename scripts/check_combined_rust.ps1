param(
    [Parameter(Mandatory = $true)]
    [string]$Runtime,
    [string]$Toolchain = 'stable',
    [ValidateRange(1, 60)]
    [int]$TimingSeconds = 10
)

$ErrorActionPreference = 'Stop'
$runtimePath = (Resolve-Path -LiteralPath $Runtime).Path
$environment = @{
    ORPHEUS_RUNTIME_DIR = $runtimePath
    ORPHEUS_BNET_DESCRIPTORS = Join-Path $runtimePath 'bnet/descriptors.pb'
    ORPHEUS_CATALOG_DATA = Join-Path $runtimePath 'data'
    ORPHEUS_TIMING_BENCH_RUNTIME = $runtimePath
    ORPHEUS_TIMING_BENCH_SECONDS = [string]$TimingSeconds
}
$previous = @{}
foreach ($name in $environment.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
}

function Invoke-CargoCheck {
    param([string[]]$Arguments)
    & cargo "+$Toolchain" @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "cargo $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    Invoke-CargoCheck -Arguments @('fmt', '--all', '--', '--check')
    foreach ($feature in @('default', 'inspector')) {
        Invoke-CargoCheck -Arguments @('clippy', '--locked', '--workspace', '--all-targets', '--features', $feature, '--', '-D', 'warnings')
        Invoke-CargoCheck -Arguments @('test', '--locked', '--workspace', '--all-targets', '--features', $feature, '--quiet')
        Invoke-CargoCheck -Arguments @('test', '--locked', '--workspace', '--lib', '--features', $feature, '--', '--ignored', '--test-threads=4', '--quiet')
    }
    Invoke-CargoCheck -Arguments @('run', '--locked', '--bin', 'orpheus_server', '--', '--check-runtime', '--runtime', $runtimePath)
    Write-Host 'Combined Rust source, runtime preflight and prepared checks passed.'
}
finally {
    Pop-Location
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
