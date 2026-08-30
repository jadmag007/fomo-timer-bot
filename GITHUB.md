# GitHub: публикация и автообновления (личная инструкция)

> ⚠️ **Этот файл личный**: он добавлен в `.gitignore`, поэтому на GitHub
> НЕ публикуется и остаётся только в вашей папке (и в zip от разработчика).
> Публиковать его нельзя.

## Что делает github_push.bat (всё автоматом)

1. Сам обновляется, если zip принёс его новую версию (тогда запусти ещё раз).
2. Распаковывает `fomo-timer-bot.zip` поверх папки — `.env`, `data/`, сессии
   (`userbot.session`), `fomo.txt` и сам батник не трогаются.
3. Проверяет git; если его нет — подскажет однострочник для установки
   (`winget install --id Git.Git -e`).
4. Первый запуск: сам делает `git init -b main`.
5. Спросит адрес репозитория (HTTPS-ссылка со страницы репозитория, кнопка
   «<> Code») — сохранит его в `github_repo.txt` и больше не спросит.
6. Один раз спросит твой ник на GitHub — запишет в `user.name`/`user.email`.
7. `git add -A` → коммит с версией из `config.py` → `git push -u origin main`.
8. Если пуш застрял на авторизации — просто запусти батник ещё раз и в
   открывшемся окне браузера нажми **Authorize** (Windows запомнит вход).

## Первый раз (если репозитория ещё нет)

1. Зайди на github.com под своим ником (jadmag007).
2. Правый верхний угол → «+» → **New repository**.
3. Имя: `fomo-timer-bot`. Публичный или приватный — на твой выбор (код бота
   секретов не содержит, личные файлы всё равно защищены `.gitignore`).
4. **Не добавляй** README/gitignore/license на сайте (оставь пустым) → Create.
5. Скопируй предложенную HTTPS-ссылку вида
   `https://github.com/jadmag007/fomo-timer-bot.git`.
6. В папке `F:\FOMO\fomo-timer-bot` запусти `github_push.bat` и вставь ссылку,
   когда он попросит.

## Обычное обновление после моего релиза

1. Новый `fomo-timer-bot.zip` положи в `F:\FOMO\fomo-timer-bot`.
2. Запусти `github_push.bat` — он сам распакует zip и запушит.
3. На телефоне (Termux):
   ```bash
   cd ~/fomo-timer-bot && git pull && bash install.sh
   ```
   (`bash install.sh` нужен только если менялся установщик; для обычных
   обновлений достаточно `git pull` и `bash start.sh`.)

## Что защищает .gitignore (на GitHub не уходит)

`.env`, `userbot.session`, `fomo.txt`, `token_updates/`, `data/`, `logs/`,
`fomo-timer-bot.zip`, сам `github_push.bat`, этот `GITHUB.md`,
`github_repo.txt`.

## Если что-то пошло не так

| Симптом | Лечение |
|---------|---------|
| Пуш просит логин/пароль, ничего не выходит | Запусти батник ещё раз — в открывшемся окне браузера нажми Authorize. Окно не открылось: `git remote set-url origin https://github.com/jadmag007/fomo-timer-bot.git` и снова `github_push.bat` |
| `rejected` / remote не пустой (создал репо с README на сайте) | Батник сам предложит перезалить поверх (ответь Y) — либо вручную: `git push -u origin main --force` |
| Пушится не в тот репозиторий | `git remote set-url origin https://github.com/jadmag007/fomo-timer-bot.git` |
| `error: Your local changes ... would be overwritten by merge` при git pull | Локальные правки служебных файлов блокируют обновление. На телефоне: `bash install.sh` сам разберётся (или `git stash && git pull`). На ПК: `git stash`, затем снова `github_push.bat` |
| На телефоне `git pull` даёт `409 Conflict` в логе бота | Где-то крутится второй экземпляр с тем же токеном: останови бота на ПК (Ctrl+C) перед стартом на телефоне — и наоборот |
| На телефоне `./install.sh`: Permission denied | Git с Windows не хранит exec-бит: запускай `bash install.sh` / `bash start.sh` |
| Хочешь перенести данные (.env + data/) через git | Пошагово расписано в TERMUX.md (раздел про перенос через свой приватный репозиторий) |
