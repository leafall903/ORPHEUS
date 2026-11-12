#requires -Version 7.0
<#
.SYNOPSIS
Read selected target-build STU assets into external research XML files.
.DESCRIPTION
Uses the same read-only CASC setup as export_casc_records.ps1 with an explicit
expected local tool version and a recorded DLL fingerprint. Caches,
network, fallback manifests and registry-language lookup are disabled. No game
content is copied into the source tree. This is inspection, not server behavior.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $GameRoot,
    [Parameter(Mandatory)] [string] $DataToolDir,
    [Parameter(Mandatory)] [string] $ExpectedToolVersion,
    [Parameter(Mandatory)] [string] $OutputDirectory,
    [Parameter(Mandatory)] [ValidatePattern('^0x[0-9a-fA-F]{16}$')] [string[]] $AssetGuid,
    [switch] $Raw
)
$ErrorActionPreference = 'Stop'
$toolDirectory = (Resolve-Path -LiteralPath $DataToolDir).Path
$gameDirectory = (Resolve-Path -LiteralPath $GameRoot).Path
$sourceRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$destination = [IO.Path]::GetFullPath($OutputDirectory)
foreach ($inputRoot in @($sourceRoot, $gameDirectory, $toolDirectory)) {
    if ($destination.Equals($inputRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $destination.StartsWith($inputRoot + [IO.Path]::DirectorySeparatorChar,
                                [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Research output must remain outside source, game and tool inputs.'
    }
}
foreach ($name in 'K4os.Compression.LZ4', 'ZstdNet', 'DragonLib', 'TACTLib', 'TankLib', 'DataTool') {
    Add-Type -Path (Join-Path $toolDirectory "$name.dll")
}
$version = [DataTool.Program].Assembly.GetCustomAttributes(
    [System.Reflection.AssemblyInformationalVersionAttribute], $false
)[0].InformationalVersion
if ($version -cne $ExpectedToolVersion) {
    throw "Unexpected DataTool build: $version"
}
$flags = [DataTool.ToolFlags]::new()
$flags.OverwatchDirectory = $gameDirectory
$flags.Language = $flags.SpeechLanguage = 'zhCN'
$flags.NoLanguageRegistry = $true
$flags.UseCache = $flags.CacheCDNData = $flags.TryManifestFallback = $flags.Online = $false
[DataTool.Program]::Flags = $flags
$consoleOut = [Console]::Out
try {
    [Console]::SetOut([IO.TextWriter]::Null)
    [DataTool.Program]::InitStorage($false)
} finally {
    [Console]::SetOut($consoleOut)
}
if ([DataTool.Program]::BuildVersion -ne 104319) {
    throw "Expected build104319, got $([DataTool.Program]::BuildVersion)"
}
$settings = [DragonLib.XML.DragonMLSettings]::new()
$referenceType = [TankLib.STU.STUInstance].Assembly.GetType('TankLib.STU.teStructuredDataAssetRef`1')
if ($null -eq $referenceType) { throw 'Pinned asset-reference type is absent.' }
$settings.TypeSerializers.Add($referenceType, [DataTool.ToolLogic.Render.teResourceGUIDSerializer]::new())
$settings.Namespaces['tank'] = 'https://yretenai.com/dragonml/v1'
$settings.Namespaces['hml'] = 'https://yretenai.com/dragonml/v1'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$rows = @()
foreach ($text in $AssetGuid) {
    $identifier = [Convert]::ToUInt64($text.Substring(2), 16)
    if ($Raw) {
        $stream = [DataTool.Helper.IO]::OpenFile($identifier)
        if ($null -eq $stream) { throw "Asset $text is absent; no substitute is generated." }
        $file = Join-Path $destination ([TankLib.teResourceGUID]::AsString($identifier))
        try {
            $output = [IO.File]::Create($file)
            try { $stream.CopyTo($output) } finally { $output.Dispose() }
        } finally { $stream.Dispose() }
        $rows += [PSCustomObject]@{
            guid = $text; path = $file; root = 'raw'
            sha256 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
            tool_version = $version
            tool_sha256 = (Get-FileHash -LiteralPath (Join-Path $toolDirectory 'DataTool.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        continue
    }
    $stu = [DataTool.Helper.STUHelper]::OpenSTUSafe($identifier)
    if ($null -eq $stu -or $stu.Instances.Length -eq 0 -or $null -eq $stu.Instances[0]) {
        throw "STU asset $text is absent or unreadable; no substitute is generated."
    }
    try {
        $xml = [DragonLib.XML.DragonML]::Print($stu.Instances[0], $settings)
        $file = Join-Path $destination ([TankLib.teResourceGUID]::AsString($identifier) + '.xml')
        [IO.File]::WriteAllText($file, $xml, [Text.UTF8Encoding]::new($false))
        $rows += [PSCustomObject]@{
            guid = $text; path = $file; root = $stu.Instances[0].GetType().Name
            sha256 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
            tool_version = $version
            tool_sha256 = (Get-FileHash -LiteralPath (Join-Path $toolDirectory 'DataTool.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    } finally {
        $stu.Dispose()
    }
}
$rows | ConvertTo-Json -Depth 5
