<#
.SYNOPSIS
    One-click legal setup, runtime pack builder/preflight, server + Overwatch 1.74 client launcher.
.DESCRIPTION
    Unifies all required runtime checks, server startup, and Overwatch 1.74.0.0.104319 client launch
    (with TLS relay injection) into one legal script without embedding any Blizzard assets or keys.
    - Packs a single unified 104319 runtime directory via `prepare_server_runtime` when -SourceDir is given.
    - Builds `orpheus_server.exe` in release mode (`inspector,lua-mods`) if missing.
    - Runs `--check-runtime` preflight and reports enriched retail data availability.
    - Persists player saves (`%LOCALAPPDATA%\ORPHEUS\state`) and remembers `-GameExe` / `-RelayDll`.
    - Launches `orpheus_server.exe`, starts `Overwatch.exe --BNetServer=127.0.0.1:<port>`, and injects `owwfd_relay.dll`.
#>
[CmdletBinding()]
param(
    [string]$RuntimeDir = "",
    [string]$SourceDir = "",
    [string]$DataDir = "",
    [string]$GameExe = "",
    [string]$RelayDll = "",
    [string]$Locale = "auto",
    [string]$Account = "",
    [int]$DebugPort = 3726,
    [int]$StallPort = 1119,
    [string]$ModScript = "",
    [string]$AiScript = "",
    [string[]]$Maps = @("0688", "06C9"),
    [switch]$Repack,
    [switch]$NoBuild,
    [switch]$ServerOnly,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LocalAppData = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Join-Path $env:USERPROFILE "AppData\Local"
} else {
    $env:LOCALAPPDATA
}
$OrpheusAppDir = Join-Path $LocalAppData "ORPHEUS"
$GamePathFile = Join-Path $OrpheusAppDir "game_path.txt"
$RelayPathFile = Join-Path $OrpheusAppDir "relay_path.txt"

if ([string]::IsNullOrWhiteSpace($RuntimeDir)) {
    if (-not [string]::IsNullOrWhiteSpace($env:ORPHEUS_RUNTIME_DIR)) {
        $RuntimeDir = $env:ORPHEUS_RUNTIME_DIR
    } elseif (Test-Path -LiteralPath (Join-Path $OrpheusAppDir "runtime-104319\manifest.json") -PathType Leaf) {
        $RuntimeDir = Join-Path $OrpheusAppDir "runtime-104319"
    } else {
        $RuntimeDir = Join-Path (Split-Path $RepoRoot -Parent) "orpheus-runtime-104319"
    }
}

if ([string]::IsNullOrWhiteSpace($DataDir)) {
    $DataDir = Join-Path $OrpheusAppDir "state"
}

function Resolve-GameExecutable {
    param([string]$Candidate)
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $null }
    $Trimmed = $Candidate.Trim().Trim('"')
    if (Test-Path -LiteralPath $Trimmed -PathType Leaf) {
        if ((Split-Path $Trimmed -Leaf) -ieq "Overwatch.exe") {
            return (Resolve-Path -LiteralPath $Trimmed).Path
        }
        $Parent = Split-Path $Trimmed -Parent
        foreach ($Sub in @("_retail_\Overwatch.exe", "Overwatch.exe")) {
            $P = Join-Path $Parent $Sub
            if (Test-Path -LiteralPath $P -PathType Leaf) { return (Resolve-Path -LiteralPath $P).Path }
        }
        return $null
    }
    if (Test-Path -LiteralPath $Trimmed -PathType Container) {
        foreach ($Sub in @("_retail_\Overwatch.exe", "Overwatch.exe")) {
            $P = Join-Path $Trimmed $Sub
            if (Test-Path -LiteralPath $P -PathType Leaf) { return (Resolve-Path -LiteralPath $P).Path }
        }
    }
    return $null
}

function Pick-GameExecutableDialog {
    Add-Type -AssemblyName System.Windows.Forms
    $Dialog = New-Object System.Windows.Forms.OpenFileDialog
    $Dialog.Title = "Select Overwatch 1.74 (_retail_\Overwatch.exe)"
    $Dialog.Filter = "Overwatch (Overwatch.exe)|Overwatch.exe|All files (*.*)|*.*"
    if ($Dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        throw "No Overwatch.exe selected. Pass -GameExe <path-to-_retail_\Overwatch.exe> or -ServerOnly."
    }
    $Resolved = Resolve-GameExecutable $Dialog.FileName
    if (-not $Resolved) {
        throw "Selected file is not Overwatch.exe: $($Dialog.FileName)"
    }
    return $Resolved
}

function Resolve-OrBuildRelayDll {
    param([string]$ExplicitRelay)
    if (-not [string]::IsNullOrWhiteSpace($ExplicitRelay)) {
        if (-not (Test-Path -LiteralPath $ExplicitRelay -PathType Leaf)) {
            throw "Relay DLL not found: $ExplicitRelay"
        }
        $Resolved = (Resolve-Path -LiteralPath $ExplicitRelay).Path
        New-Item -ItemType Directory -Force -Path $OrpheusAppDir | Out-Null
        Set-Content -LiteralPath $RelayPathFile -Value $Resolved -Encoding UTF8
        return $Resolved
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ORPHEUS_RELAY_DLL) -and (Test-Path -LiteralPath $env:ORPHEUS_RELAY_DLL -PathType Leaf)) {
        return (Resolve-Path -LiteralPath $env:ORPHEUS_RELAY_DLL).Path
    }
    $RepoRelay = Join-Path $RepoRoot "relay\owwfd_relay.dll"
    if (Test-Path -LiteralPath $RepoRelay -PathType Leaf) {
        return (Resolve-Path -LiteralPath $RepoRelay).Path
    }
    if (Test-Path -LiteralPath $RelayPathFile -PathType Leaf) {
        $Saved = (Get-Content -LiteralPath $RelayPathFile -Raw).Trim()
        if (-not [string]::IsNullOrWhiteSpace($Saved) -and (Test-Path -LiteralPath $Saved -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $Saved).Path
        }
    }
    $LocalRelay = Join-Path $OrpheusAppDir "owwfd_relay.dll"
    if (Test-Path -LiteralPath $LocalRelay -PathType Leaf) {
        return (Resolve-Path -LiteralPath $LocalRelay).Path
    }

    Write-Host "[relay] owwfd_relay.dll not found; compiling from relay\owwfd_relay.cpp..." -ForegroundColor Yellow
    $RelayCpp = Join-Path $RepoRoot "relay\owwfd_relay.cpp"
    if (-not (Test-Path -LiteralPath $RelayCpp -PathType Leaf)) {
        throw "Cannot find relay source at $RelayCpp"
    }
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $VsWhere -PathType Leaf)) {
        throw "vswhere.exe not found and no prebuilt owwfd_relay.dll was provided. Pass -RelayDll <path\to\owwfd_relay.dll>."
    }
    $VsPath = (& $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($VsPath)) {
        throw "Visual Studio x64 C++ Build Tools not found. Pass -RelayDll <path\to\owwfd_relay.dll>."
    }
    $VcVars = Join-Path $VsPath "VC\Auxiliary\Build\vcvars64.bat"
    if (-not (Test-Path -LiteralPath $VcVars -PathType Leaf)) {
        throw "vcvars64.bat not found at $VcVars. Pass -RelayDll <path\to\owwfd_relay.dll>."
    }
    New-Item -ItemType Directory -Force -Path $OrpheusAppDir | Out-Null
    $Cmd = "`"$VcVars`" >nul && cl /nologo /LD /O2 /W3 /EHsc /D_CRT_SECURE_NO_WARNINGS `"$RelayCpp`" /link ws2_32.lib /MACHINE:X64 /OUT:`"$LocalRelay`""
    & cmd.exe /c $Cmd
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $LocalRelay -PathType Leaf)) {
        throw "Failed to compile owwfd_relay.dll (exit code $LASTEXITCODE). Pass -RelayDll <path\to\owwfd_relay.dll>."
    }
    Set-Content -LiteralPath $RelayPathFile -Value $LocalRelay -Encoding UTF8
    return $LocalRelay
}

function Detect-TankLocale {
    param([string]$GameExePath, [string]$Requested)
    if ($Requested -eq "none") { return $null }
    if ($Requested -ne "auto" -and -not [string]::IsNullOrWhiteSpace($Requested)) { return $Requested }

    $RetailDir = Split-Path $GameExePath -Parent
    $RootDir = Split-Path $RetailDir -Parent
    $Installed = New-Object System.Collections.Generic.HashSet[string]
    foreach ($Dir in @($RootDir, $RetailDir)) {
        $BuildInfo = Join-Path $Dir ".build.info"
        if (-not (Test-Path -LiteralPath $BuildInfo -PathType Leaf)) { continue }
        $Lines = Get-Content -LiteralPath $BuildInfo -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($Lines.Count -lt 2) { continue }
        $Header = @()
        foreach ($Col in ($Lines[0] -split "\|")) {
            $Header += ($Col -split "!")[0]
        }
        $TagsIdx = [Array]::IndexOf($Header, "Tags")
        if ($TagsIdx -ge 0) {
            $Row = $Lines[1] -split "\|"
            if ($TagsIdx -lt $Row.Count) {
                foreach ($M in [regex]::Matches($Row[$TagsIdx], "\b[a-z]{2}[A-Z]{2}\b")) {
                    [void]$Installed.Add($M.Value)
                }
            }
        }
        break
    }
    if ($Installed.Count -eq 0) { return $null }
    $Sys = ([System.Globalization.CultureInfo]::CurrentUICulture.Name -replace "-", "")
    if (-not [string]::IsNullOrWhiteSpace($Sys) -and $Installed.Contains($Sys)) { return $Sys }
    if (-not [string]::IsNullOrWhiteSpace($Sys) -and $Sys.Length -ge 2) {
        $Prefix = $Sys.Substring(0, 2)
        $Match = $Installed | Where-Object { $_.StartsWith($Prefix) } | Sort-Object | Select-Object -First 1
        if ($Match) { return $Match }
    }
    if ($Installed.Contains("enUS")) { return "enUS" }
    return $null
}

function Test-TcpPortListening {
    param([int]$Port)
    $Client = $null
    try {
        $Client = New-Object System.Net.Sockets.TcpClient
        $Async = $Client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if ($Async.AsyncWaitHandle.WaitOne(150, $false)) {
            $Client.EndConnect($Async)
            return $true
        }
        return $false
    } catch {
        return $false
    } finally {
        if ($Client) { $Client.Dispose() }
    }
}

$RelayInjectorCode = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class OrpheusRelayInjector
{
    private const uint PROCESS_CREATE_THREAD = 0x0002;
    private const uint PROCESS_VM_OPERATION = 0x0008;
    private const uint PROCESS_VM_READ = 0x0010;
    private const uint PROCESS_VM_WRITE = 0x0020;
    private const uint PROCESS_QUERY_INFORMATION = 0x0400;
    private const uint MEM_COMMIT_RESERVE = 0x3000;
    private const uint MEM_RELEASE = 0x8000;
    private const uint PAGE_READWRITE = 0x04;
    private const uint WAIT_OBJECT_0 = 0x00000000;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint dwDesiredAccess, bool bInheritHandle, int dwProcessId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr hObject);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr VirtualAllocEx(IntPtr hProcess, IntPtr lpAddress, UIntPtr dwSize, uint flAllocationType, uint flProtect);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool VirtualFreeEx(IntPtr hProcess, IntPtr lpAddress, UIntPtr dwSize, uint dwFreeType);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool WriteProcessMemory(IntPtr hProcess, IntPtr lpBaseAddress, byte[] lpBuffer, UIntPtr nSize, out UIntPtr lpNumberOfBytesWritten);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr GetModuleHandleW(string lpModuleName);

    [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    private static extern IntPtr GetProcAddress(IntPtr hModule, string procName);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateRemoteThread(IntPtr hProcess, IntPtr lpThreadAttributes, UIntPtr dwStackSize, IntPtr lpStartAddress, IntPtr lpParameter, uint dwCreationFlags, out uint lpThreadId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint WaitForSingleObject(IntPtr hHandle, uint dwMilliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetExitCodeThread(IntPtr hThread, out uint lpExitCode);

    public static void WaitAndInject(int pid, string dllPath, int timeoutMs)
    {
        string fullDll = Path.GetFullPath(dllPath);
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
        Process proc = Process.GetProcessById(pid);

        while (true)
        {
            if (proc.HasExited)
                throw new InvalidOperationException("Overwatch exited with code " + proc.ExitCode + " before relay injection.");
            try
            {
                proc.Refresh();
                bool ready = false;
                foreach (ProcessModule mod in proc.Modules)
                {
                    if (string.Equals(mod.ModuleName, "kernel32.dll", StringComparison.OrdinalIgnoreCase))
                    {
                        ready = true;
                        break;
                    }
                }
                if (ready) break;
            }
            catch
            {
                // Process loader is still initializing modules.
            }
            if (DateTime.UtcNow >= deadline)
                throw new TimeoutException("Timed out waiting for Overwatch.exe loader initialization.");
            Thread.Sleep(50);
        }

        IntPtr hKernel32 = GetModuleHandleW("kernel32.dll");
        IntPtr pLoadLibraryW = GetProcAddress(hKernel32, "LoadLibraryW");
        if (pLoadLibraryW == IntPtr.Zero)
            throw new InvalidOperationException("Failed to resolve LoadLibraryW (win32=" + Marshal.GetLastWin32Error() + ").");

        uint access = PROCESS_CREATE_THREAD | PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_QUERY_INFORMATION;
        IntPtr hProcess = OpenProcess(access, false, pid);
        if (hProcess == IntPtr.Zero)
            throw new InvalidOperationException("OpenProcess failed (win32=" + Marshal.GetLastWin32Error() + ").");

        IntPtr remoteMem = IntPtr.Zero;
        IntPtr hThread = IntPtr.Zero;
        try
        {
            byte[] pathBytes = Encoding.Unicode.GetBytes(fullDll + "\0");
            remoteMem = VirtualAllocEx(hProcess, IntPtr.Zero, (UIntPtr)pathBytes.Length, MEM_COMMIT_RESERVE, PAGE_READWRITE);
            if (remoteMem == IntPtr.Zero)
                throw new InvalidOperationException("VirtualAllocEx failed (win32=" + Marshal.GetLastWin32Error() + ").");

            UIntPtr written;
            if (!WriteProcessMemory(hProcess, remoteMem, pathBytes, (UIntPtr)pathBytes.Length, out written))
                throw new InvalidOperationException("WriteProcessMemory failed (win32=" + Marshal.GetLastWin32Error() + ").");

            uint threadId;
            hThread = CreateRemoteThread(hProcess, IntPtr.Zero, UIntPtr.Zero, pLoadLibraryW, remoteMem, 0, out threadId);
            if (hThread == IntPtr.Zero)
                throw new InvalidOperationException("CreateRemoteThread failed (win32=" + Marshal.GetLastWin32Error() + ").");

            uint wait = WaitForSingleObject(hThread, (uint)timeoutMs);
            if (wait != WAIT_OBJECT_0)
                throw new TimeoutException("Remote LoadLibraryW timed out (wait=" + wait + ").");

            uint exitCode;
            if (!GetExitCodeThread(hThread, out exitCode) || exitCode == 0)
                throw new InvalidOperationException("LoadLibraryW returned NULL inside Overwatch.exe for " + fullDll);
        }
        finally
        {
            if (hThread != IntPtr.Zero) CloseHandle(hThread);
            if (remoteMem != IntPtr.Zero) VirtualFreeEx(hProcess, remoteMem, UIntPtr.Zero, MEM_RELEASE);
            CloseHandle(hProcess);
        }
    }
}
'@

Write-Host "=== ORPHEUS 1.74 Unified Launcher ===" -ForegroundColor Cyan
Write-Host "Repo Root   : $RepoRoot"
Write-Host "Runtime Dir : $RuntimeDir"
Write-Host "Save Dir    : $DataDir"

Push-Location $RepoRoot
try {
    $NeedsPack = $Repack -or (-not (Test-Path -LiteralPath (Join-Path $RuntimeDir "data\schemas_174.json")))
    if ($NeedsPack -and (-not [string]::IsNullOrWhiteSpace($SourceDir))) {
        Write-Host "[1/5] Packing unified 1.74 runtime from local source: $SourceDir" -ForegroundColor Yellow
        $PackArgs = @(
            "run", "--locked", "-p", "orpheus-runtime", "--bin", "prepare_server_runtime", "--",
            "--source", $SourceDir,
            "--destination", $RuntimeDir
        )
        foreach ($MapId in $Maps) {
            if (-not [string]::IsNullOrWhiteSpace($MapId)) {
                $PackArgs += @("--map", $MapId)
            }
        }
        & cargo @PackArgs
        if ($LASTEXITCODE -ne 0) {
            throw "prepare_server_runtime failed with exit code $LASTEXITCODE"
        }
    } elseif (-not (Test-Path -LiteralPath $RuntimeDir)) {
        throw "Runtime directory not found: $RuntimeDir`nFrom the repository root, run: python scripts/runtime.py build <OW1-install-directory>. For an existing pack, pass -RuntimeDir <prepared-pack>."
    }

    $ServerExe = Join-Path $RepoRoot "target\release\orpheus_server.exe"
    if ((-not $NoBuild) -and (-not (Test-Path -LiteralPath $ServerExe))) {
        Write-Host "[2/5] Building release orpheus_server (inspector + lua-mods)..." -ForegroundColor Yellow
        & cargo build --locked --release --features "inspector,lua-mods" --bin orpheus_server
        if ($LASTEXITCODE -ne 0) {
            throw "cargo build failed with exit code $LASTEXITCODE"
        }
    } elseif (-not (Test-Path -LiteralPath $ServerExe)) {
        throw "Release binary not found at $ServerExe (re-run without -NoBuild to compile)."
    } else {
        Write-Host "[2/5] Using release binary: $ServerExe" -ForegroundColor Green
    }

    Write-Host "[3/5] Running unified runtime preflight (--check-runtime)..." -ForegroundColor Yellow
    & $ServerExe --check-runtime --runtime $RuntimeDir
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime preflight failed with exit code $LASTEXITCODE"
    }

    $EnrichedFiles = @(
        "collision\0688.rays.col",
        "data\combat_hurt_volumes.json",
        "data\emote_aliases_174.json",
        "data\emote_blend_trees_174.json",
        "data\voice_effects_174.json",
        "data\player_voice_174.json",
        "data\combat_voice_174.json",
        "data\map_environment_174.json",
        "data\junkenstein\junkenstein-npcs.json"
    )
    Write-Host "Enriched runtime data status:" -ForegroundColor DarkCyan
    foreach ($Rel in $EnrichedFiles) {
        $Full = Join-Path $RuntimeDir $Rel
        if (Test-Path -LiteralPath $Full) {
            Write-Host "  [OK]      $Rel" -ForegroundColor Green
        } else {
            Write-Host "  [MISSING] $Rel" -ForegroundColor DarkYellow
        }
    }

    if ($PreflightOnly) {
        Write-Host "[4/5] PreflightOnly complete. Exiting cleanly." -ForegroundColor Green
        exit 0
    }

    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

    if ($StallPort -eq 1119 -and (Test-TcpPortListening 1119)) {
        Write-Host "[net] Port 1119 already in use; switching Battle.net stall port to 1118." -ForegroundColor DarkYellow
        $StallPort = 1118
    }

    $ServerArgs = @(
        "--runtime", $RuntimeDir,
        "--data-dir", $DataDir,
        "--stall-port", $StallPort.ToString()
    )
    if (-not [string]::IsNullOrWhiteSpace($Account)) {
        $ServerArgs += @("--account", $Account)
    }
    if ($DebugPort -gt 0) {
        $ServerArgs += @("--debug-port", $DebugPort.ToString())
    }
    if (-not [string]::IsNullOrWhiteSpace($ModScript)) {
        $ResolvedMod = (Resolve-Path -LiteralPath $ModScript).Path
        $ServerArgs += @("--mod-script", $ResolvedMod)
    }
    if (-not [string]::IsNullOrWhiteSpace($AiScript)) {
        $ResolvedAi = (Resolve-Path -LiteralPath $AiScript).Path
        $ServerArgs += @("--ai-script", $ResolvedAi)
    }

    if ($ServerOnly) {
        Write-Host "[4/5] Starting ORPHEUS native server only (-ServerOnly)..." -ForegroundColor Green
        if ($DebugPort -gt 0) {
            Write-Host "      Web Scene Inspector : http://127.0.0.1:$DebugPort/" -ForegroundColor Cyan
        }
        & $ServerExe @ServerArgs
        exit $LASTEXITCODE
    }

    # Resolve local Overwatch.exe and owwfd_relay.dll
    $ResolvedGame = $null
    if (-not [string]::IsNullOrWhiteSpace($GameExe)) {
        $ResolvedGame = Resolve-GameExecutable $GameExe
        if (-not $ResolvedGame) { throw "Overwatch.exe not found at: $GameExe" }
    } elseif (-not [string]::IsNullOrWhiteSpace($env:ORPHEUS_GAME_EXE)) {
        $ResolvedGame = Resolve-GameExecutable $env:ORPHEUS_GAME_EXE
    } elseif (Test-Path -LiteralPath $GamePathFile -PathType Leaf) {
        $SavedGame = (Get-Content -LiteralPath $GamePathFile -Raw).Trim()
        $ResolvedGame = Resolve-GameExecutable $SavedGame
    }
    if (-not $ResolvedGame) {
        $ResolvedGame = Pick-GameExecutableDialog
    }
    New-Item -ItemType Directory -Force -Path $OrpheusAppDir | Out-Null
    Set-Content -LiteralPath $GamePathFile -Value $ResolvedGame -Encoding UTF8

    $ResolvedRelay = Resolve-OrBuildRelayDll -ExplicitRelay $RelayDll
    $DetectedLocale = Detect-TankLocale -GameExePath $ResolvedGame -Requested $Locale

    Write-Host "Game Exe    : $ResolvedGame"
    Write-Host "Relay DLL   : $ResolvedRelay"
    if ($DetectedLocale) {
        Write-Host "Locale      : $DetectedLocale"
    }

    Add-Type -TypeDefinition $RelayInjectorCode -Language CSharp

    $ServerLog = Join-Path $DataDir "orpheus_server.log"
    if (Test-Path $ServerLog) {
        $PrevLog = Join-Path $DataDir "orpheus_server.prev.log"
        try {
            Copy-Item -Path $ServerLog -Destination $PrevLog -Force -ErrorAction SilentlyContinue
        } catch {}
    }
    $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $ServerExe
    $StartInfo.WorkingDirectory = $RepoRoot
    $EscapedArgs = foreach ($Arg in $ServerArgs) {
        if ($Arg -match '[\s"]') { '"' + ($Arg -replace '"', '\"') + '"' } else { $Arg }
    }
    $StartInfo.Arguments = $EscapedArgs -join " "
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.CreateNoWindow = $true

    Write-Host "[4/5] Starting ORPHEUS native server on loopback..." -ForegroundColor Green
    $ServerProc = New-Object System.Diagnostics.Process
    $ServerProc.StartInfo = $StartInfo
    [void]$ServerProc.Start()

    $LogWriter = New-Object System.IO.StreamWriter($ServerLog, $true, [System.Text.Encoding]::UTF8)
    $LogWriter.AutoFlush = $true
    $LogWriter.WriteLine("")
    $LogWriter.WriteLine("=== ORPHEUS session started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===")
    $OutJob = Register-ObjectEvent -InputObject $ServerProc -EventName OutputDataReceived -Action {
        if ($EventArgs.Data -ne $null) {
            $Event.MessageData.WriteLine($EventArgs.Data)
            Write-Host $EventArgs.Data
        }
    } -MessageData $LogWriter
    $ErrJob = Register-ObjectEvent -InputObject $ServerProc -EventName ErrorDataReceived -Action {
        if ($EventArgs.Data -ne $null) {
            $Event.MessageData.WriteLine($EventArgs.Data)
            Write-Host $EventArgs.Data -ForegroundColor DarkGray
        }
    } -MessageData $LogWriter
    $ServerProc.BeginOutputReadLine()
    $ServerProc.BeginErrorReadLine()

    $GameProc = $null
    try {
        $Deadline = (Get-Date).AddSeconds(30)
        $ReadyPorts = @($StallPort, 3724, 21119)
        while ($true) {
            if ($ServerProc.HasExited) {
                throw "orpheus_server exited early with code $($ServerProc.ExitCode). Check log: $ServerLog"
            }
            $AllReady = $true
            foreach ($Port in $ReadyPorts) {
                if (-not (Test-TcpPortListening $Port)) {
                    $AllReady = $false
                    break
                }
            }
            if ($AllReady) { break }
            if ((Get-Date) -ge $Deadline) {
                throw "Timed out waiting for orpheus_server ports ($($ReadyPorts -join ', ')). Check log: $ServerLog"
            }
            Start-Sleep -Milliseconds 150
        }

        if ($DebugPort -gt 0) {
            Write-Host "      Web Scene Inspector : http://127.0.0.1:$DebugPort/" -ForegroundColor Cyan
        }
        if (-not [string]::IsNullOrWhiteSpace($ModScript)) {
            Write-Host "      Lua 5.4 Mod         : $ModScript" -ForegroundColor Cyan
        }

        Write-Host "[5/5] Launching Overwatch.exe and injecting TLS relay..." -ForegroundColor Green
        $GameArgs = @("--BNetServer=127.0.0.1:$StallPort")
        if ($DetectedLocale) {
            $GameArgs += "--tank_Locale=$DetectedLocale"
        }
        $GameProc = Start-Process -FilePath $ResolvedGame -ArgumentList $GameArgs -WorkingDirectory (Split-Path $ResolvedGame -Parent) -PassThru
        [OrpheusRelayInjector]::WaitAndInject($GameProc.Id, $ResolvedRelay, 30000)
        Write-Host "[+] Overwatch.exe (PID $($GameProc.Id)) connected with relay! Close the game when finished." -ForegroundColor Green
        if (-not [string]::IsNullOrWhiteSpace($ModScript) -or -not [string]::IsNullOrWhiteSpace($AiScript)) {
            Write-Host "    Tip: Type 'reload' + Enter here to hot-reload Lua scripts while playing." -ForegroundColor DarkCyan
        }

        while (-not $GameProc.HasExited) {
            if ($ServerProc.HasExited) {
                Write-Warning "orpheus_server exited unexpectedly with code $($ServerProc.ExitCode)."
                break
            }
            if ([Console]::KeyAvailable) {
                $Line = [Console]::ReadLine()
                if ($null -ne $Line -and $Line.Trim() -eq "reload") {
                    $ServerProc.StandardInput.WriteLine("reload")
                }
            }
            Start-Sleep -Milliseconds 250
        }
    } finally {
        if ($GameProc -and (-not $GameProc.HasExited)) {
            try {
                if ($ServerProc.HasExited) { $GameProc.Kill() }
            } catch {}
        }
        if ($ServerProc -and (-not $ServerProc.HasExited)) {
            try {
                $ServerProc.StandardInput.WriteLine("")
                if (-not $ServerProc.WaitForExit(5000)) {
                    $ServerProc.Kill()
                }
            } catch {
                try { $ServerProc.Kill() } catch {}
            }
        }
        if ($OutJob) { Unregister-Event -SubscriptionId $OutJob.Id -ErrorAction SilentlyContinue }
        if ($ErrJob) { Unregister-Event -SubscriptionId $ErrJob.Id -ErrorAction SilentlyContinue }
        if ($LogWriter) { $LogWriter.Dispose() }
    }
} finally {
    Pop-Location
}
