@echo off
chcp 65001 >nul
setlocal
title Stop H3 Prompt Studio

powershell.exe -NoProfile -Command "$c=Get-NetTCPConnection -State Listen -LocalPort 8766 -ErrorAction SilentlyContinue | Select-Object -First 1; if(-not $c){Write-Host 'H3 is not running.' -ForegroundColor Yellow; exit 0}; $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$c.OwningProcess); if($p.CommandLine -notlike '*backend.app:app*' -or $p.CommandLine -notlike '*uvicorn*'){Write-Error ('Safety check stopped: port 8766 belongs to '+$p.Name+' (PID '+$c.OwningProcess+'), not H3.'); exit 1}; Stop-Process -Id $c.OwningProcess -ErrorAction Stop; Write-Host ('H3 stopped safely. PID '+$c.OwningProcess) -ForegroundColor Green"

if errorlevel 1 (
    echo.
    echo [ERROR] H3 was not stopped.
    pause
    exit /b 1
)

timeout /t 2 >nul
endlocal
