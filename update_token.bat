@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fomo Timer - token update

if "%~1"=="" (
    echo Drag and drop a .har file ^(or a text file with "Copy as cURL"^)
    echo onto the update_token.bat icon in Explorer.
    echo.
    echo How to capture traffic: see README.md - section "Autotracking", step 1.
    echo The bot must be running ^(start.bat^) - it will pick up the file
    echo and send you a Telegram message.
    pause
    exit /b 0
)

if not exist "token_updates" mkdir "token_updates"
copy /y "%~1" "token_updates\" >nul
if errorlevel 1 (
    echo Failed to copy the file.
    pause
    exit /b 1
)

echo.
echo File copied to token_updates\
echo If the bot is running, it will process it in a few seconds
echo and message you in Telegram. You can close this window.
timeout /t 4 >nul
