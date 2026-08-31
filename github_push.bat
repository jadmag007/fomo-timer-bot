@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fomo Timer Bot - update from zip + publish to GitHub

echo ============================================
echo   Fomo Timer Bot - update zip + GitHub push
echo ============================================
echo.

rem ---------- 0. self-update: the zip may bring a newer copy of this file ----------
if not exist "github_push.bat.new" goto :no_selfupdate
copy /y "github_push.bat.new" "github_push.bat" >nul
del "github_push.bat.new" >nul 2>nul
echo github_push.bat was updated by the zip. Please run it once more.
pause
(goto) 2>nul & exit /b 0
:no_selfupdate

rem ---------- 1. unpack fomo-timer-bot.zip over this folder (if present) ----------
if not exist "fomo-timer-bot.zip" goto :nozip
echo Found fomo-timer-bot.zip - unpacking over this folder...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $tmp=Join-Path $env:TEMP ('fomo_unzip_'+[guid]::NewGuid().ToString('N')); Expand-Archive -LiteralPath (Join-Path (Get-Location) 'fomo-timer-bot.zip') -DestinationPath $tmp -Force; $src=Join-Path $tmp 'fomo-timer-bot'; if (-not (Test-Path $src)) { $src=$tmp }; robocopy $src (Get-Location).Path /E /NFL /NDL /NJH /NJS /NP /XF .env github_repo.txt github_push.bat fomo.txt userbot.session cloudflared.exe /XD .git .venv venv data token_updates __pycache__ | Out-Null; $rc=$LASTEXITCODE; try { Remove-Item -Recurse -Force $tmp } catch {}; if ($rc -ge 8) { exit $rc } else { exit 0 }"
if errorlevel 1 (
    echo [!] Unpack failed - the zip may be broken or files are locked.
    echo     Close the bot window ^(start.bat^) and run this file again.
    pause
    exit /b 1
)
echo Unpacked. Kept untouched: .env, data/, sessions, this .bat file.
echo.
:nozip

rem ---------- 2. git installed? ----------
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

rem ---------- 3. local repository exists? ----------
if not exist ".git" (
    echo First publish: creating the local repository...
    git init -b main >nul 2>&1
    if errorlevel 1 (
        git init >nul
        git branch -M main
    )
)
echo.

rem ---------- 4. where to push (remote) ----------
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

rem ---------- 5. who commits (one time, just your GitHub nick) ----------
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

rem ---------- 6. bot version for the commit message ----------
set "VER="
if exist "config.py" for /f "tokens=2 delims== " %%v in ('findstr /b /c:"APP_VERSION" config.py') do set "VER=%%v"
if defined VER set "VER=%VER:"=%"

rem ---------- 7. stage and commit ----------
rem install keeps previous timestamps; a same-length change (version
rem bump) can keep the same (mtime, size) the index cached -- git's
rem stat cache would call the file 'unchanged' and silently skip the
rem release. Re-add by CONTENT, not stat.
git rm -r --cached --quiet . >nul 2>nul
git add -A
if errorlevel 1 goto :fail
git diff --cached --quiet
if not errorlevel 1 (
    echo No new changes to commit. Checking GitHub anyway...
    goto :push
)
if defined VER (
    git commit -m "Update to %VER% (%date% %time%)" >nul
) else (
    git commit -m "Update %date% %time%" >nul
)
if errorlevel 1 goto :fail
:push
echo.

rem ---------- 8. push ----------
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
echo .env, userbot.session, fomo.txt, token_updates/, data/, logs,
echo fomo-timer-bot.zip and github_push.bat itself.
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
