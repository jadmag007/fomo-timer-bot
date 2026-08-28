@echo off
setlocal
cd /d "%~dp0"
title Fomo Timer Bot - one-time userbot login

if not exist ".venv\Scripts\python.exe" (
    echo [!] Bot is not installed yet. Run install.bat first,
    echo     then run this file again.
    pause
    exit /b 1
)

rem Self-heal: telethon was added later than the user's first install
".venv\Scripts\python.exe" -c "import telethon" >nul 2>&1
if errorlevel 1 (
    echo Installing missing dependencies, please wait...
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
)

".venv\Scripts\python.exe" login_userbot.py

echo.
echo Finished. You can close this window.
pause
