@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fomo Timer Bot - publish to GitHub

echo ============================================
echo   Fomo Timer Bot - publish to GitHub
echo ============================================
echo.

rem ---------- 1. git installed? ----------
where git >nul 2>nul
if errorlevel 1 (
    echo [!] Git is not installed.
    echo.
    echo Install it once, any way:
    echo   - Windows 10/11: open PowerShell and run:
    echo       winget install --id Git.Git -e
    echo   - or download the installer: https://git-scm.com/download/win
    echo     ^(click Next-Next-Finish, defaults are fine^)
    echo.
    echo After installing, close this window and run github_push.bat again.
    pause
    exit /b 1
)

rem ---------- 2. local repository exists? ----------
if not exist ".git" (
    echo First publish: creating the local repository...
    git init -b main >nul 2>&1
    if errorlevel 1 (
        git init >nul
        git branch -M main
    )
)
echo.

rem ---------- 3. where to push (remote) ----------
set "URL="
if exist "github_repo.txt" set /p "URL=" < "github_repo.txt"
if defined URL set "URL=%URL:"=%"
git remote get-url origin >nul 2>&1
if not errorlevel 1 goto :remote_ok
if defined URL goto :remote_use_saved
echo Open your new repository page on github.com, press the green
echo "^<^> Code" button and copy the HTTPS link, it looks like:
echo    https://github.com/YOUR_NICK/fomo-timer-bot.git
echo.
set "URL="
set /p "URL=Paste that link here and press Enter: "
if not defined URL (
    echo URL is empty. Run github_push.bat again.
    pause
    exit /b 1
)
set "URL=%URL:"=%"
echo %URL% | findstr /i "github.com" >nul
if errorlevel 1 (
    echo This does not look like a GitHub link. Example:
    echo    https://github.com/YOUR_NICK/fomo-timer-bot.git
    pause
    exit /b 1
)
:remote_use_saved
git remote add origin "%URL%"
if errorlevel 1 goto :fail
> "github_repo.txt" echo %URL%
echo Remote saved: %URL%
goto :remote_done
:remote_ok
for /f "delims=" %%u in ('git remote get-url origin') do set "URL=%%u"
echo Remote already set: %URL%
goto :remote_done
:remote_done
echo.

rem ---------- 4. who commits (one time, just your GitHub nick) ----------
git config user.name >nul 2>&1
if not errorlevel 1 goto :identity_ok
echo Git needs a name for commits. Enter your GitHub nickname
echo ^(letters/numbers only, no spaces^).
set "GHU="
set /p "GHU=GitHub nickname: "
if not defined GHU (
    echo Nickname is empty. Run github_push.bat again.
    pause
    exit /b 1
)
git config user.name "%GHU%"
git config user.email "%GHU%@users.noreply.github.com"
echo Saved: %GHU% ^<%GHU%@users.noreply.github.com^>
:identity_ok
echo.

rem ---------- 5. stage and commit ----------
git add -A
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Update %date% %time%" >nul
    if errorlevel 1 goto :fail
) else (
    echo No new changes to commit. Checking GitHub anyway...
)
echo.

rem ---------- 6. push ----------
echo Publishing to GitHub...
git push -u origin main
if not errorlevel 1 goto :done
echo.
echo Push failed. Most common reasons:
echo  1. The auth window did not appear or was closed - just run this
echo     file again and press Authorize in the browser window.
echo  2. Wrong repository URL - fix it with:
echo       git remote set-url origin https://github.com/NICK/fomo-timer-bot.git
echo  3. You created the repo WITH a README file on the site - the remote
echo     is not empty. You may overwrite it with your local files ONCE.
echo.
set "ANS="
set /p "ANS=Overwrite remote with your local files now? Type Y and Enter: "
if /i not "%ANS%"=="Y" goto :fail
git push -u origin main --force
if not errorlevel 1 goto :done
goto :fail

:done
echo.
echo ============================================
echo   Published! Check it at github.com
echo ============================================
echo Personal files are NOT published ^(protected by .gitignore^):
echo .env, userbot.session, fomo.txt, token_updates/, data/ and logs.
echo.
echo Tip: start.bat now checks GitHub on every launch and installs
echo updates automatically on any PC with this repository.
timeout /t 8 >nul
exit /b 0

:fail
echo.
echo See GITHUB.md (personal guide) - there is a troubleshooting table.
pause
exit /b 1
