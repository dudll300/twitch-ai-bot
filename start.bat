@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import PySide6, websockets" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" start.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Could not start Twitch AI Bot. Press any key to close.
pause >nul
exit /b 1
