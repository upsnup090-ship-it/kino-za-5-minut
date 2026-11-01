#!/bin/bash
# Деплой кода бота: синхронизирует git-копию с боевой /opt/kino/app.
# Живой код на сервере — /opt/kino/app (не git-каталог!). Git-копия живёт
# отдельно и служит только источником обновлений.
#
# Использование (на сервере):
#   sudo -u kino /opt/kino/app/deploy/update.sh            # только rsync
#   sudo -u kino /opt/kino/app/deploy/update.sh --restart  # rsync + restart
#
# Что переносится: код и конфиги (kino/ config/ prompts/ deploy/ docs/
# scripts/ tests/ analysis/, *.py, *.txt, *.yaml, Makefile, mypy.ini).
# Что НЕ трогается никогда: data/, logs/, venv/, .env — боевые данные
# и окружение живут только в /opt/kino/app.

set -euo pipefail

SRC="/opt/kino/project"          # git-копия (git pull сюда)
APP="/opt/kino/app"              # живой код
RESTART=0
[ "${1:-}" = "--restart" ] && RESTART=1

[ -d "$SRC/project" ] || { echo "нет git-копии: $SRC/project"; exit 1; }
[ -d "$APP/kino" ]    || { echo "нет боевого каталога: $APP/kino"; exit 1; }

# rsync содержимого git-подкаталога project/ в app/
# -a: права/владелец/время; --delete: убирать удалённое из git (кроме исключений);
# исключения: данные, окружение, кэши — им в app() жить своей жизнью.
rsync -a --delete \
  --exclude "data/" --exclude "logs/" --exclude "venv/" \
  --exclude ".env" --exclude ".git/" \
  --exclude "__pycache__/" --exclude ".pytest_cache/" --exclude ".mypy_cache/" \
  "$SRC/project/" "$APP/"

echo "rsync: $SRC/project/ → $APP/"

if [ "$RESTART" = "1" ]; then
  sudo systemctl restart kino kino-admin
  sleep 2
  systemctl --no-pager -l status kino kino-admin | grep -E "Active:|Status:" || true
  echo "перезапущено: kino, kino-admin"
else
  echo "готово. рестарт вручную: sudo systemctl restart kino kino-admin"
fi
