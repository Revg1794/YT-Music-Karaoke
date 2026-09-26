@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on your PATH.
    echo Install it from https://python.org - check "Add python.exe to PATH" during install - then run this again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
)

echo Installing/updating dependencies...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt

echo Checking your YouTube Music connection...
".venv\Scripts\python.exe" backend\ytauth.py --check
if errorlevel 2 goto startserver
if errorlevel 1 (
    echo.
    echo Let's connect your YouTube Music account.
    ".venv\Scripts\python.exe" setup_auth.py
    if errorlevel 1 (
        echo Account setup failed or was cancelled.
        pause
        exit /b 1
    )
)
:startserver

echo.
echo Starting server...
echo (bound to your LAN too -- see the "Guests" link in the app to invite phones on the same WiFi)
start "YT Music Karaoke" ".venv\Scripts\python.exe" -m uvicorn main:app --app-dir backend --host 0.0.0.0 --port 8000
timeout /t 2 /nobreak > nul
start "" http://localhost:8000
