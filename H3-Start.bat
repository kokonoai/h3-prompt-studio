@echo off
setlocal
cd /d "%~dp0"
set "H3_STUDIO_DATA=%~dp0data"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Please run Install-H3.bat first.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch.ps1"

if errorlevel 1 (
    echo.
    echo [ERROR] Startup failed. Check the logs folder.
    pause
)

endlocal
