param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$exitCode = 0
try {
    Set-Location -LiteralPath $ProjectRoot
    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $vite = Join-Path $ProjectRoot 'ui\node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Python environment missing. Run uv sync in your own project terminal first.'
    }
    if (-not (Test-Path -LiteralPath $vite)) {
        throw 'UI dependencies missing. Run npm ci in the ui directory first.'
    }
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $connections = @(& "$env:WINDIR\System32\netstat.exe" -ano -p tcp)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list the processes using the app ports.' }
    $ownerIds = @($connections | ForEach-Object {
        if ($_ -match '^\s*TCP\s+\S+:(2024|5173)\s+\S+\s+LISTENING\s+(\d+)\s*$') {
            [int]$Matches[2]
        }
    })
    foreach ($ownerId in ($ownerIds | Sort-Object -Unique)) {
        if (-not (Get-Process -Id $ownerId -ErrorAction SilentlyContinue)) { continue }
        Write-Host "Stopping process $ownerId to free the app ports..."
        $stopOutput = & "$env:WINDIR\System32\taskkill.exe" /PID $ownerId /T /F 2>&1
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $ownerId -ErrorAction SilentlyContinue)) {
            throw "Cannot stop process ${ownerId}: $stopOutput"
        }
    }
    foreach ($port in 2024, 5173) {
        $portDeadline = (Get-Date).AddSeconds(10)
        while ($true) {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
            try { $listener.Start(); break }
            catch {
                if ((Get-Date) -gt $portDeadline) {
                    throw "Port $port is still in use after stopping its previous listener. Try running the launcher as administrator."
                }
                Start-Sleep -Milliseconds 200
            }
            finally { $listener.Stop() }
        }
    }

    # A Windows job closes the entire server tree even when this window is closed.
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
public static class CalendarServerJob {
    private static IntPtr handle;
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint size);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    public static void Initialize() {
        handle = CreateJobObject(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new Win32Exception();
        int size = IntPtr.Size == 8 ? 144 : 112;
        IntPtr info = Marshal.AllocHGlobal(size);
        try {
            Marshal.Copy(new byte[size], 0, info, size);
            Marshal.WriteInt32(info, 16, 0x2000); // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (!SetInformationJobObject(handle, 9, info, (uint)size)) throw new Win32Exception();
            if (!AssignProcessToJobObject(handle, Process.GetCurrentProcess().Handle))
                throw new Win32Exception();
        } finally { Marshal.FreeHGlobal(info); }
    }
}
'@
    [CalendarServerJob]::Initialize()

    # Previous launcher windows may still hold their log files open.
    $logDir = Join-Path $ProjectRoot ("workspace\launcher\{0}-{1}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID)
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $env:PYTHONUTF8 = '1'
    $env:LANGSMITH_TRACING = 'false'
    $env:VITE_API_URL = 'http://127.0.0.1:2024'
    Write-Host 'Starting Calendar Assistant backend and UI...'
    Write-Host "Logs: $logDir"
    $backend = Start-Process -FilePath $python -ArgumentList @(
        '-m', 'langgraph_cli', 'dev', '--no-browser', '--no-reload',
        '--host', '127.0.0.1', '--port', '2024'
    ) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDir 'backend.log') `
        -RedirectStandardError (Join-Path $logDir 'backend-error.log')
    $frontend = Start-Process -FilePath $node -ArgumentList @(
        ('"{0}"' -f $vite), '--host', '127.0.0.1', '--port', '5173', '--strictPort'
    ) -WorkingDirectory (Join-Path $ProjectRoot 'ui') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logDir 'ui.log') `
        -RedirectStandardError (Join-Path $logDir 'ui-error.log')

    $deadline = (Get-Date).AddSeconds(120)
    $pending = @('http://127.0.0.1:2024/ok', 'http://127.0.0.1:5173/')
    while ($pending.Count -gt 0) {
        if ($backend.HasExited -or $frontend.HasExited) {
            throw "A server stopped during startup. See logs in $logDir"
        }
        $pending = @($pending | Where-Object {
            try {
                $response = Invoke-WebRequest -Uri $_ -UseBasicParsing -TimeoutSec 2
                $response.StatusCode -ne 200
            } catch { $true }
        })
        if ((Get-Date) -gt $deadline) { throw "Startup timed out. See logs in $logDir" }
        if ($pending.Count -gt 0) { Start-Sleep -Milliseconds 500 }
    }
    Write-Host 'Ready: http://127.0.0.1:5173/'
    Write-Host 'Keep this window open. Close it or press Ctrl+C to stop both servers.'
    if (-not $NoBrowser) {
        # Let Explorer open the browser outside the servers' lifetime/job.
        $shell = New-Object -ComObject Shell.Application
        $shell.ShellExecute('http://127.0.0.1:5173/', '', '', 'open', 1)
    }
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Seconds 1
    }
    throw "A server stopped. See logs in $logDir"
} catch {
    $exitCode = 1
    Write-Host "Calendar Assistant: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $NoBrowser) { Read-Host 'Press Enter to close' | Out-Null }
} finally {
    foreach ($server in @($backend, $frontend)) {
        if ($null -ne $server -and -not $server.HasExited) {
            Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
exit $exitCode
