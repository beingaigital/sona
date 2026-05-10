@echo off
chcp 65001 >nul 2>&1
title Sona

set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%venv312_new\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] Python 3.12 venv not found
    pause
    exit /b 1
)

echo Starting Sona...
cd /d "%PROJECT_DIR%"
"%PYTHON%" -m cli.main interactive
pause
