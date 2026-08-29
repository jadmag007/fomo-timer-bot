@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fomo Timer Bot

if not exist ".venv\Scripts\python.exe" (
    echo [!] Bot is not installed. Run install.bat first.
    pause
    exit /b 1
)

rem Self-check of dependencies: if something is missing (e.g. tzdata, telethon) - quietly install
".venv\Scripts\python.exe" -c "import aiogram, dotenv, tzdata, telethon" >nul 2>&1
if errorlevel 1 (
    echo Checking / installing dependencies, please wait...
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
)

rem ---------- GitHub update check (silent; skipped if git or repo missing) ----------
where git >nul 2>nul
if errorlevel 1 goto :updated
if not exist ".git" goto :updated
git fetch origin main >nul 2>nul
if errorlevel 1 goto :updated
set "LOCALREV="
set "REMOTEREV="
for /f %%i in ('git rev-parse HEAD') do set "LOCALREV=%%i"
for /f %%i in ('git rev-parse origin/main') do set "REMOTEREV=%%i"
if not defined LOCALREV goto :updated
if not defined REMOTEREV goto :updated
if "%LOCALREV%"=="%REMOTEREV%" goto :updated
echo GitHub: update found, installing...
git reset --hard origin/main >nul 2>nul
echo GitHub: update installed. Personal files (.env, data/) are not touched.
echo.
:updated

:loop
".venv\Scripts\python.exe" bot.py
if errorlevel 2 (
    echo.
    echo Configuration problem ^(exit code 2, e.g. no token in .env^).
    echo NOT restarting - fix the problem shown above and run start.bat again.
    pause
    exit /b 2
)
if errorlevel 1 (
    echo.
    echo Bot crashed. Restarting in 5 seconds...
    echo ^(To stop completely just close this window^)
    timeout /t 5 /nobreak >nul
    goto :loop
)
echo.
echo Bot stopped. You can close this window.
pause
