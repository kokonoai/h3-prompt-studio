# Restart only this H3 Studio service; refuse while video jobs are active.
$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$studioPython = (Resolve-Path -LiteralPath (Join-Path $studioRoot '.venv\Scripts\python.exe')).Path
$baseUrl = 'http://127.0.0.1:8766'

function Get-ActiveH3Jobs {
    $response = Invoke-RestMethod -Uri "$baseUrl/api/video/runs" -TimeoutSec 8
    if ($null -eq $response -or $null -eq $response.runs) {
        throw 'Unable to verify video-job state; H3 was not stopped.'
    }
    return @($response.runs | Where-Object { $_.status -in @('preparing', 'queued', 'running', 'uncertain') })
}

$listener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -eq '127.0.0.1' } | Select-Object -First 1
if (-not $listener) {
    & (Join-Path $studioRoot 'Launch.ps1') -NoBrowser
    exit 0
}

$health = Invoke-RestMethod -Uri "$baseUrl/api/bootstrap" -TimeoutSec 5
if ($health.version -notmatch '^1\.\d+\.\d+$' -or $health.token -isnot [string] -or $health.token.Length -lt 32) {
    throw 'Port 8766 is not verified as this H3 Studio; nothing was stopped.'
}
$active = @(Get-ActiveH3Jobs)
if ($active.Count -gt 0) {
    Write-Host "$($active.Count) H3 video job(s) are active. Wait until all videos finish, then run this script again. Nothing was stopped." -ForegroundColor Yellow
    exit 2
}

$listenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
$parentProcess = if ($listenerProcess) {
    Get-CimInstance Win32_Process -Filter "ProcessId=$($listenerProcess.ParentProcessId)"
}
if (-not $listenerProcess -or
    $listenerProcess.CommandLine -notmatch 'uvicorn\s+backend\.app:app\s+--host\s+127\.0\.0\.1\s+--port\s+8766' -or
    -not $parentProcess -or $parentProcess.ExecutablePath -ne $studioPython) {
    throw "The listener is not the expected isolated H3 process in $studioRoot; nothing was stopped."
}

# Re-check immediately before stopping: a user may have started another render.
if (@(Get-ActiveH3Jobs).Count -gt 0) {
    Write-Host 'A new H3 video job started. Nothing was stopped.' -ForegroundColor Yellow
    exit 2
}
Stop-Process -Id $listenerProcess.ProcessId -ErrorAction Stop
if (Get-Process -Id $parentProcess.ProcessId -ErrorAction SilentlyContinue) {
    Stop-Process -Id $parentProcess.ProcessId -ErrorAction Stop
}
Start-Sleep -Seconds 2
if (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8766 is still occupied. H3 was not relaunched; inspect the process before retrying.'
}
& (Join-Path $studioRoot 'Launch.ps1') -NoBrowser
Write-Host 'H3 has restarted. Refresh http://127.0.0.1:8766/ with Ctrl+F5.' -ForegroundColor Green
