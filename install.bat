@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fomo Timer Bot - setup

echo ============================================
echo   Fomo Timer Bot - setup (Windows)
echo ============================================
echo.

rem ---------- 1. Find Python ----------
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
if not defined PY if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"
if not defined PY python --version >nul 2>&1 && set "PY=python"

if defined PY goto :py_found

echo Python not found. Installing automatically (1-2 minutes)...
echo.
winget --version >nul 2>&1
if not errorlevel 1 (
    echo [1/2] Installing Python via winget...
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
) else (
    echo [1/2] winget not found - downloading installer from python.org...
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile 'python-installer.exe'"
    if not exist python-installer.exe (
        echo Failed to download the Python installer. Download it from python.org
        echo and install manually ^(check "Add Python to PATH"^), then run install.bat again.
        goto :fail
    )
    echo [2/2] Installing Python...
    python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
    del python-installer.exe >nul 2>&1
)

echo Checking Python installation...
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"

if not defined PY (
    echo Python was not detected after installation. Reboot the PC or install
    echo it manually from python.org ^(check "Add Python to PATH"^),
    echo then run install.bat again.
    goto :fail
)

:py_found
echo Python found: %PY%
%PY% --version
echo.

rem ---------- 2. venv + dependencies ----------
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
) else (
    echo Virtual environment already exists.
)

echo Installing dependencies (1-2 minutes, downloading aiogram)...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 goto :fail
echo Dependencies installed.
echo.

rem ---------- 3. Bot token ----------
if exist ".env" (
    findstr /b /r /c:"BOT_TOKEN=." .env >nul 2>&1 && goto :token_ok
)
echo A bot token is required:
echo   1. Open @BotFather in Telegram
echo   2. Send the /newbot command, choose a name and username
echo   3. Copy the token like 7712345678:AAF3xQ...
echo.
set "TOKEN="
set /p "TOKEN=Paste the token here and press Enter: "
if not defined TOKEN (
    echo Token is empty. Run install.bat again.
    goto :fail
)
> .env echo BOT_TOKEN=%TOKEN%
>> .env echo DEFAULT_TZ=Europe/Moscow
>> .env echo WARN_ENABLED=false

:token_ok
echo Token found: OK
echo Tip: after the first start run login_bot.bat ONCE -
echo then the bot refreshes the game keys itself, no HAR files needed.
echo.

rem ---------- 4. Start ----------
echo Done! Starting the bot...
echo Later you can start it by double-clicking start.bat
echo.
timeout /t 3 /nobreak >nul
call start.bat
exit /b 0

:fail
echo.
echo === Setup failed. Make a screenshot of this window and send it. ===
pause
exit /b 1
