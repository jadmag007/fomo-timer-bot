#!/data/data/com.termux/files/usr/bin/sh
# github_push.sh -- release from phone (Termux). Windows github_push.bat does
# the same on PC; this script is its Termux twin.
#
# Flow: download fomo-timer-bot.zip to Downloads (browser) -> run
#   bash github_push.sh
# The script finds the NEWEST fomo-timer-bot*.zip in Downloads (repeated
# downloads are saved as "fomo-timer-bot (1).zip", "(2)"... -- a plain-name
# search could pick an ancient copy), unpacks it into a TEMP folder and
# copies the files into the bot folder ROOT. The zip's own
# "fomo-timer-bot/" wrapper folder is detected and flattened (an older
# script version unpacked it as a nested fomo-timer-bot/fomo-timer-bot/
# copy -- that is fixed and such stale copies are removed automatically).
# Personal files (.env, data/, sessions, fomo.txt) are NOT in the zip and
# survive untouched.
#
# After a successful install ALL downloaded fomo-timer-bot*.zip copies are
# removed from Downloads (no duplicate piles). Running the script again
# with no zip around simply pushes whatever is already committed.
#
# Publishing is safe to re-run: it commits only real changes and pushes
# with retries (mobile networks often reset TLS connections to GitHub).
#
# ONE-TIME bootstrap (fresh folder, or full recovery from any mess):
#   mkdir -p ~/fomo-timer-bot
#   cp ~/storage/downloads/fomo-timer-bot.zip ~/fomo-timer-bot/
#   cd ~/fomo-timer-bot
#   python -m zipfile -e fomo-timer-bot.zip .
#   bash fomo-timer-bot/github_push.sh
# The rest is automatic: git repo is created if needed (adopting the
# existing GitHub history), git identity is derived from the repo URL,
# the release is committed and pushed.

set -u

ZIP_NAME=fomo-timer-bot.zip

# --- 0) which folder do we serve? -------------------------------------------
SELF_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
BOT_DIR="$SELF_DIR"
# Bootstrap case: the script itself lives inside the freshly extracted zip
# wrapper (a nested fomo-timer-bot/ folder with no .git of its own) and the
# parent folder is the real bot root (has the zip / saved repo URL, but no
# bot.py yet) -- serve the parent. A real bot root is never mistaken for
# the wrapper: it has its own .git.
case "$BOT_DIR" in
  */fomo-timer-bot)
    if [ ! -e "$BOT_DIR/.git" ]; then
      PARENT="$(dirname "$BOT_DIR")"
      if [ ! -f "$PARENT/bot.py" ] && [ -f "$BOT_DIR/bot.py" ] \
         && { [ -f "$PARENT/$ZIP_NAME" ] || [ -f "$PARENT/github_repo.txt" ]; }; then
        echo "[push] running from a nested fomo-timer-bot/ folder -- using $PARENT as the bot root"
        BOT_DIR="$PARENT"
      fi
    fi ;;
esac
cd "$BOT_DIR" || exit 1

# --- 1) locate the NEWEST release zip ----------------------------------------
# Browsers rename repeated downloads to "fomo-timer-bot (1).zip", "(2)"...
# so a plain-name search would install an OLD archive. Compare every
# fomo-timer-bot*.zip by modification time and take the newest one.
SRC=""
NEWEST_T=0
for D in "$HOME/storage/downloads" "/sdcard/Download" "/sdcard/Downloads" "$BOT_DIR"; do
  [ -d "$D" ] || continue
  for Z in "$D"/fomo-timer-bot*.zip; do
    [ -f "$Z" ] || continue
    M="$(stat -c %Y "$Z" 2>/dev/null || echo 0)"
    if [ "$M" -gt "$NEWEST_T" ]; then NEWEST_T="$M"; SRC="$Z"; fi
  done
done

if [ -n "$SRC" ]; then
  echo "[push] found: $SRC (newest downloaded copy)"

# --- 2) unpack into a TEMP folder (never straight into the bot folder) ------
TMP=""
for D in "${TMPDIR:-}" "$PREFIX/tmp" "/tmp" "$BOT_DIR"; do
  [ -n "$D" ] && [ -d "$D" ] && [ -w "$D" ] && { TMP="$D/fomo_unzip_$$"; break; }
done
[ -n "$TMP" ] || TMP="$BOT_DIR/.fomo_unzip_$$"
rm -rf "$TMP"
mkdir -p "$TMP" || { echo "[push] cannot create temp folder"; exit 1; }
if command -v unzip >/dev/null 2>&1; then
  unzip -oq "$SRC" -d "$TMP" || { echo "[push] unzip failed"; rm -rf "$TMP"; exit 1; }
else
  echo "[push] unzip not found -- using python zipfile"
  python -m zipfile -e "$SRC" "$TMP" || { echo "[push] zip extract failed"; rm -rf "$TMP"; exit 1; }
fi

# --- 3) detect the payload (strip the zip's wrapper folder) -----------------
PAYLOAD="$TMP"
N_TOP="$(find "$TMP" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
if [ "$N_TOP" = "1" ] && [ -d "$TMP/fomo-timer-bot" ]; then
  PAYLOAD="$TMP/fomo-timer-bot"
fi
if [ ! -f "$PAYLOAD/bot.py" ] || [ ! -f "$PAYLOAD/config.py" ]; then
  echo "[push] this zip does not look like a bot release (no bot.py/config.py)"
  rm -rf "$TMP"
  exit 1
fi
VERSION="$(grep -E '^APP_VERSION' "$PAYLOAD/config.py" 2>/dev/null | head -n 1 | cut -d= -f2- | tr -d ' "' | tr -d "'")"
NFILES="$(find "$PAYLOAD" -type f | wc -l | tr -d ' ')"
echo "[push] release version: ${VERSION:-unknown} ($NFILES files)"

# --- 4) install payload into the bot folder root ----------------------------
# github_push.sh itself is installed via .new + rename: overwriting the
# running script file in place could corrupt execution.
find "$PAYLOAD" -mindepth 1 -maxdepth 1 ! -name 'github_push.sh' \
  -exec cp -a {} "$BOT_DIR/" \; || { echo "[push] copy failed"; rm -rf "$TMP"; exit 1; }
if [ -f "$PAYLOAD/github_push.sh" ]; then
  cp -a "$PAYLOAD/github_push.sh" "$BOT_DIR/github_push.sh.new"
  if cmp -s "$BOT_DIR/github_push.sh.new" "$BOT_DIR/github_push.sh" 2>/dev/null; then
    rm -f "$BOT_DIR/github_push.sh.new"
  else
    mv -f "$BOT_DIR/github_push.sh.new" "$BOT_DIR/github_push.sh"
    echo "[push] github_push.sh updated itself from the zip (new logic active)."
  fi
fi
rm -rf "$TMP"
echo "[push] unpacked into $BOT_DIR"

# --- 5) remove stale nested copies left by the older script version --------
if [ "$BOT_DIR" != "$HOME" ] && [ "$BOT_DIR" != "/" ] \
   && [ -f "$BOT_DIR/fomo-timer-bot/bot.py" ]; then
  echo "[push] removing stale nested copy fomo-timer-bot/ (old bug; files live in the root now)"
  rm -rf "$BOT_DIR/fomo-timer-bot"
fi

# --- 5b) installed -- remove ALL downloaded zip copies (no duplicate piles) --
CLEANED=0
for D in "$HOME/storage/downloads" "/sdcard/Download" "/sdcard/Downloads" "$BOT_DIR"; do
  [ -d "$D" ] || continue
  for Z in "$D"/fomo-timer-bot*.zip; do
    [ -f "$Z" ] || continue
    if rm -f "$Z" 2>/dev/null; then CLEANED=$((CLEANED + 1)); fi
  done
done
if [ "$CLEANED" -gt 0 ]; then
  echo "[push] removed $CLEANED downloaded zip copy(ies) -- Downloads stays clean."
fi

else

# --- no zip: just push what is already committed (retry after a network
# --- failure), unless there is nothing installed here at all ---------------
if [ -f bot.py ] && [ -e .git ]; then
  echo "[push] no fomo-timer-bot*.zip found -- pushing what is already committed."
  VERSION="$(grep -E '^APP_VERSION' config.py 2>/dev/null | head -n 1 | cut -d= -f2- | tr -d ' "' | tr -d "'")"
else
  echo "[push] zip not found and this folder has no installed bot yet."
  echo "[push] Download fomo-timer-bot.zip to Downloads first (or copy it here)."
  echo "[push] If Downloads is not visible: run 'termux-setup-storage' once."
  exit 1
fi

fi

# --- 6) git repo: create if missing, adopt GitHub history on first publish --
# NOTE: the repo must live IN THIS FOLDER (.git right here) -- not merely
# somewhere above it, otherwise git commands would act on a foreign repo.
FRESH=0
if [ -e .git ]; then
  :
else
  echo "[push] no git repo here yet -- creating one..."
  git init >/dev/null 2>&1 || { echo "[push] git init failed"; exit 1; }
  git symbolic-ref HEAD refs/heads/main >/dev/null 2>&1
  FRESH=1
fi
git config core.fileMode false 2>/dev/null
# symbolic-ref (not rev-parse): it works on a fresh repo with no commits yet
BR="$(git symbolic-ref --short HEAD 2>/dev/null || echo main)"
[ "$BR" = "HEAD" ] && BR=main

# --- 7) remote URL -----------------------------------------------------------
# literal stored URL (git config), NOT "remote get-url": get-url applies
# insteadOf rewrites, which would both fail validation and overwrite the
# user's URL indirection.
URL="$(git config --get remote.origin.url 2>/dev/null || echo '')"
if [ -z "$URL" ] && [ -f github_repo.txt ]; then
  URL="$(head -n 1 github_repo.txt | tr -d '\r' | tr -d '"')"
fi
if [ -z "$URL" ]; then
  echo "[push] where to publish? Open your repository page on github.com,"
  echo "[push] press the green '<> Code' button and copy the HTTPS link:"
  echo "[push]   https://github.com/YOUR_NICK/fomo-timer-bot.git"
  if [ -t 0 ]; then
    printf "[push] Paste that link and press Enter: "
    read -r URL || URL=""
  fi
fi
case "$URL" in
  *github.com*) : ;;
  *) echo "[push] no GitHub URL known -- run the script again in a terminal"
     echo "[push] or set it once: git remote add origin https://github.com/NICK/fomo-timer-bot.git"
     exit 1 ;;
esac
if git config --get remote.origin.url >/dev/null 2>&1; then
  : # remote already configured -- keep it untouched
else
  git remote add origin "$URL" || { echo "[push] cannot set the remote"; exit 1; }
fi
printf '%s\n' "$URL" > github_repo.txt

# First publish on this phone: adopt the existing GitHub history so the
# release lands as ONE clean commit on top (no force, no history rewrite).
if [ "$FRESH" -eq 1 ]; then
  echo "[push] first publish: adopting existing GitHub history (if any)..."
  if GIT_TERMINAL_PROMPT=0 git fetch origin "$BR" >/dev/null 2>&1 \
     && git rev-parse --verify -q FETCH_HEAD >/dev/null 2>&1; then
    git update-ref "refs/heads/$BR" FETCH_HEAD >/dev/null 2>&1
    git reset --mixed >/dev/null 2>&1
    echo "[push] GitHub history adopted -- the release will be one clean commit."
  else
    echo "[push] could not fetch GitHub history (offline or empty repo) -- will push as a fresh root."
  fi
fi

# --- 8) git identity: derive from the repo URL (one time) --------------------
if [ -z "$(git config user.email)" ] || [ -z "$(git config user.name)" ]; then
  NICK="$(printf '%s' "$URL" | sed -E 's#^(https?|ssh)://([^@/]*@)?github\.com/##; s#^git@github\.com:##; s#/.*$##; s#\.git$##')"
  case "$NICK" in
    ""|*" "*|*/*) NICK="" ;;
  esac
  if [ -n "$NICK" ]; then
    git config user.name "$NICK"
    git config user.email "$NICK@users.noreply.github.com"
    echo "[push] git identity set: $NICK <${NICK}@users.noreply.github.com>"
  else
    echo "[push] git needs your name once. Run these two commands, then run the script again:"
    echo '[push]   git config --global user.name "YourGitHubNick"'
    echo '[push]   git config --global user.email "nick@users.noreply.github.com"'
    exit 1
  fi
fi

# --- 9) commit ---------------------------------------------------------------
# junk that must never live in git (old runs could have tracked it)
git rm -r --cached --ignore-unmatch --quiet "*.part" "fomo-timer-bot.zip" "github_push.sh.new" >/dev/null 2>&1
git add -A
if git diff --cached --quiet; then
  AHEAD="$(git rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 0)"
  if [ "${AHEAD:-0}" -gt 0 ] 2>/dev/null; then
    echo "[push] nothing new to commit -- but $AHEAD earlier commit(s) are NOT on GitHub yet, pushing them."
  else
    echo "[push] nothing new to commit -- checking GitHub anyway..."
  fi
else
  git commit -m "Release ${VERSION:-unknown} from zip (termux push)" >/dev/null || exit 1
  echo "[push] committed: Release ${VERSION:-unknown}"
fi

# --- 10) push with retries ----------------------------------------------------
TOKEN=""
if [ -f .env ]; then
  TOKEN="$(grep -E '^GITHUB_TOKEN=' .env | head -n 1 | cut -d= -f2- | tr -d ' "' | tr -d "'")"
fi

try_push() {
  if [ "${1:-}" = "force" ]; then
    if [ -n "$TOKEN" ]; then
      GIT_TERMINAL_PROMPT=0 git -c http.version=HTTP/1.1 -c http.postBuffer=52428800 \
        -c "credential.helper=!f() { echo username=x-access-token; echo password=$TOKEN; }; f" \
        push --force origin "$BR" 2>&1
    else
      GIT_TERMINAL_PROMPT=0 git -c http.version=HTTP/1.1 -c http.postBuffer=52428800 \
        push --force origin "$BR" 2>&1
    fi
  else
    if [ -n "$TOKEN" ]; then
      GIT_TERMINAL_PROMPT=0 git -c http.version=HTTP/1.1 -c http.postBuffer=52428800 \
        -c "credential.helper=!f() { echo username=x-access-token; echo password=$TOKEN; }; f" \
        push -u origin "$BR" 2>&1
    else
      GIT_TERMINAL_PROMPT=0 git -c http.version=HTTP/1.1 -c http.postBuffer=52428800 \
        push -u origin "$BR" 2>&1
    fi
  fi
}

show_out() {
  printf '%s\n' "$1" | sed -E 's#(https://)[^/@[:space:]]+@#\1[hidden]@#g' | tail -n 4 | while IFS= read -r L; do
    [ -n "$L" ] && echo "[push]   $L"
  done
}

echo "[push] pushing to: $URL ($BR)"
TRY=0
PUSHED=0
AUTH_FAIL=0
REJECTED=0
REBASED=0
while [ "$TRY" -lt 4 ]; do
  TRY=$((TRY + 1))
  if [ "$TRY" -gt 1 ]; then
    W=$((TRY * 4))
    echo "[push] waiting ${W}s before attempt ${TRY}/4 (mobile TLS resets are common)..."
    sleep "$W"
  fi
  echo "[push] push attempt ${TRY}/4 ..."
  OUT="$(try_push)"
  RC=$?
  show_out "$OUT"
  if [ "$RC" -eq 0 ]; then PUSHED=1; break; fi
  case "$OUT" in
    *Authenticat*|*"could not read Username"*|*"Invalid username"*|*403*|*"Permission to"*|*404*)
      AUTH_FAIL=1
      break ;;
    *"non-fast-forward"*|*"fetch first"*|*"rejected"*)
      REJECTED=1
      if [ "$REBASED" -eq 0 ]; then
        REBASED=1
        echo "[push] GitHub has newer commits -- rebasing our release commit on top..."
        if GIT_TERMINAL_PROMPT=0 git pull --rebase origin "$BR" >/dev/null 2>&1; then
          continue
        fi
        echo "[push] rebase failed -- local history diverged too much."
      fi
      break ;;
  esac
done

if [ "$PUSHED" -eq 1 ]; then
  echo "[push] ============================================"
  echo "[push]   DONE: ${VERSION:-unknown} pushed to GitHub."
  echo "[push]   Personal files are NOT published (.gitignore):"
  echo "[push]   .env, data/, sessions, fomo.txt, logs, the zip."
  echo "[push]   On the bot run: bash start.sh (it pulls updates itself)."
  echo "[push] ============================================"
  exit 0
fi

if [ "$AUTH_FAIL" -eq 1 ]; then
  echo "[push] push FAILED: GitHub rejected the credentials."
  echo "[push] Fix (any one):"
  echo "[push]   1) put GITHUB_TOKEN=ghp_xxx into .env (classic PAT with repo"
  echo "[push]      access), then run this script again;"
  echo "[push]   2) or run:  git config credential.helper store"
  echo "[push]              git pull      (username, then PAT as password -- once)"
  exit 1
fi

if [ "$REJECTED" -eq 1 ] && [ -t 0 ]; then
  echo "[push] GitHub history differs from this folder."
  printf "[push] Overwrite GitHub with these local files? Type y and Enter: "
  ANS=""
  read -r ANS || ANS=""
  case "$ANS" in
    y|Y)
      OUT="$(try_push force)"
      RC=$?
      show_out "$OUT"
      if [ "$RC" -eq 0 ]; then
        echo "[push] DONE (forced): ${VERSION:-unknown} pushed."
        exit 0
      fi ;;
  esac
fi

echo "[push] push FAILED after $TRY attempt(s)."
echo "[push] Most likely the network to GitHub is flaky right now:"
echo "[push] 'TLS connect error ... unexpected eof while reading' means the"
echo "[push] connection is being reset mid-way (common on mobile ISPs)."
echo "[push] Wait 5-10 minutes and run this script again -- it will push"
echo "[push] the same commit. Turning a VPN on for one minute also helps."
exit 1
