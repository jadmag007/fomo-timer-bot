#!/data/data/com.termux/files/usr/bin/sh
# github_push.sh -- release from phone (Termux). Windows github_push.bat does
# the same on PC; this script is its Termux twin.
#
# Flow: download fomo-timer-bot.zip to Downloads (browser) -> run
#   bash github_push.sh
# The script finds the zip, unpacks it over the bot folder (.env / data/ /
# .git are NOT in the zip, they survive), commits and pushes to GitHub.
#
# ONE-TIME bootstrap (when github_push.sh is not in the bot folder yet):
#   cd ~/fomo-timer-bot
#   cp ~/storage/downloads/fomo-timer-bot.zip .
#   python -m zipfile -e fomo-timer-bot.zip .
#   bash github_push.sh

set -u
cd "$(dirname "$0")" || exit 1

ZIP_NAME=fomo-timer-bot.zip
BOT_DIR="$(pwd)"

say() { echo "[push] $*"; }

# --- 1) locate the zip in Downloads (Termux storage or raw /sdcard path) ---
SRC=""
for P in "$HOME/storage/downloads/$ZIP_NAME" \
         "/sdcard/Download/$ZIP_NAME" \
         "/sdcard/Downloads/$ZIP_NAME"; do
  [ -f "$P" ] && { SRC="$P"; break; }
done
if [ -z "$SRC" ]; then
  say "zip not found: $ZIP_NAME"
  say "Download it to Downloads first."
  say "If Downloads is not visible: run 'termux-setup-storage' once."
  exit 1
fi
say "found: $SRC"

# --- 2) unpack over the bot folder ---
if command -v unzip >/dev/null 2>&1; then
  unzip -oq "$SRC" || { say "unzip failed"; exit 1; }
else
  say "unzip not found -- using python zipfile"
  python -m zipfile -e "$SRC" . || { say "zipfile extract failed"; exit 1; }
fi
say "unpacked into $BOT_DIR"
rm -f "$ZIP_NAME"

VERSION="$(python -c 'import config; print(config.APP_VERSION)' 2>/dev/null || echo unknown)"
say "release version: $VERSION"

# --- 3) commit ---
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  :
else
  say "this folder is not a git repo (no .git) -- cannot push."
  say "See README.md / TERMUX.md: how to move the repo to a new phone."
  exit 1
fi
BR="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
git add -A || exit 1
if git diff --cached --quiet; then
  say "nothing new to commit (already pushed)"
else
  git commit -m "Release $VERSION from zip (termux push)" || exit 1
fi

# --- 4) push (GITHUB_TOKEN from .env if present) ---
TOKEN=""
if [ -f .env ]; then
  TOKEN="$(grep -E '^GITHUB_TOKEN=' .env | head -n 1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
fi
URL="$(git remote get-url origin 2>/dev/null || echo '')"
PUSH_URL="$URL"
if [ -n "$TOKEN" ] && [ -n "$URL" ]; then
  PUSH_URL="$(echo "$URL" | sed -E 's#https://[^/]*@?#https://'"$TOKEN"'@#')"
fi
say "pushing to: $(echo "$URL" | sed -E 's#https://[^/@]+@#https://#') ($BR)"
if git push "$PUSH_URL" "$BR"; then
  say "DONE: $VERSION pushed to GitHub. On the bot run: bash start.sh (it pulls updates itself)."
else
  say "push FAILED. If Termux asks for credentials:"
  say "  git config credential.helper store"
  say "  git pull    (enter username, then PAT as password -- once)"
  say "or put GITHUB_TOKEN=<your PAT> into .env and run this script again."
  exit 1
fi
