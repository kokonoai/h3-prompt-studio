@echo off
chcp 65001 >nul
setlocal
title H3 Prompt Studio - Close this window to stop
cd /d "%~dp0"
set "H3_STUDIO_DATA=%~dp0data"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe was not found. Run Install-H3.bat first.
    pause
    exit /b 1
)

powershell.exe -NoProfile -Command "$c=Get-NetTCPConnection -State Listen -LocalPort 8766 -ErrorAction SilentlyContinue | Select-Object -First 1; if($c){$p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$c.OwningProcess); if($p.CommandLine -like '*backend.app:app*'){Start-Process 'http://127.0.0.1:8766'; Write-Host 'H3 is already running.' -ForegroundColor Yellow; exit 10}else{Write-Error ('Port 8766 is used by '+$p.Name+' (PID '+$c.OwningProcess+').'); exit 11}}"
if errorlevel 11 (
    pause
    exit /b 1
)
if errorlevel 10 exit /b 0

start "" /b powershell.exe -NoProfile -WindowStyle Hidden -Command "$d=(Get-Date).AddSeconds(30); while((Get-Date)-lt $d){try{$r=Invoke-RestMethod 'http://127.0.0.1:8766/api/bootstrap' -TimeoutSec 1; if($r.version){Start-Process 'http://127.0.0.1:8766'; exit}}catch{}; Start-Sleep -Milliseconds 400}"

echo H3 Prompt Studio is starting at http://127.0.0.1:8766
echo Keep this window open. Close it or press Ctrl+C to stop H3.
echo.
".venv\Scripts\python.exe" -X utf8 -m uvicorn backend.app:app --host 127.0.0.1 --port 8766 --no-access-log

echo.
echo H3 Prompt Studio stopped.
pause
endlocal
