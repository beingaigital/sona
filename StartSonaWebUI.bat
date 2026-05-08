@echo off
setlocal

REM Always run from this script's directory.
cd /d "%~dp0"

REM Optional first argument: port (default 8501)
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8501"

if not exist ".\.venv\Scripts\python.exe" (
    echo [ERROR] Python virtual environment not found: .\.venv
    echo Please create it first, then install dependencies.
    echo.
    pause
    exit /b 1
)

echo Starting Sona Web UI on http://localhost:%PORT%
echo Press Ctrl+C in this window to stop.
echo.

set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
set "STREAMLIT_SERVER_HEADLESS=true"

REM Feed an empty line once to bypass first-run email prompt.
echo.| ".\.venv\Scripts\python.exe" -m streamlit run "streamlit_app.py" --server.port %PORT%

endlocal
