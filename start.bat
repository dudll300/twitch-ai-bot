@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 start.py %*
) else (
    python start.py %*
)
echo.
echo Bot stopped. Press any key to close this window.
pause >nul
