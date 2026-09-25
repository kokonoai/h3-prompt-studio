@echo off
chcp 65001 >nul
setlocal EnableExtensions
title Install H3 Prompt Studio
cd /d "%~dp0"

set "UV_CACHE_DIR=%~dp0.cache\uv"
set "UV_PYTHON_INSTALL_DIR=%~dp0.uv-python"
set "NPM_CONFIG_CACHE=%~dp0.cache\npm"

echo ============================================================
echo H3 Prompt Studio installer
echo App directory: %CD%
echo.
echo Prerequisites: uv, Node.js with npm, FFmpeg and FFprobe.
echo The installer creates a private .venv and keeps caches here.
echo Existing data, projects, cards and videos are retained.
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup.ps1"
if errorlevel 1 (
    echo.
    echo [ERROR] Installation did not complete.
    echo Read the message above, install the missing prerequisite, then run this file again.
    pause
    exit /b 1
)

echo.
echo [OK] H3 Prompt Studio installation completed.
echo Start it with H3-Start.bat.
echo ComfyUI and Ollama or LM Studio remain separate applications.
echo.
pause
endlocal
