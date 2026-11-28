[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $DataToolDir,
    [Parameter(Mandatory)] [string] $Install,
    [Parameter(Mandatory)] [string] $Heroes,
    [Parameter(Mandatory)] [string] $OutputDir,
    [string] $DotNet = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$assemblies = (Get-Item -LiteralPath $DataToolDir).FullName
$game = (Get-Item -LiteralPath $Install).FullName
$catalog = (Get-Item -LiteralPath $Heroes).FullName
$destination = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDir)
$project = Join-Path $PSScriptRoot 'ModelAnchors.csproj'
& $DotNet run --project $project --configuration Release "-p:DataToolDir=$assemblies" -- $assemblies $game $catalog $destination
exit $LASTEXITCODE
