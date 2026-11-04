#!/bin/bash
# Деплой Kino: git pull в боевой каталог + рестарт трёх юнитов.
#
# Живой код на сервере — /opt/kino/project (git-копия). Юниты смотрят туда же:
#   kino-dispatcher  → python -m pro_code.main   (WD /opt/kino/project)
#   kino             → python -m kino serve      (WD /opt/kino/project/project_auto)
#   kino-admin       → python -m kino admin      (WD /opt/kino/project/project_auto)
#
# Использование (на сервере):
#   sudo -u kino ./deploy/deploy.sh              # pull + проверка, без рестарта
#   sudo -u kino ./deploy/deploy.sh --restart    # pull + проверка + рестарт
#
# Идемпотентно: повторный запуск на актуальном коде ничего не меняет.
# Секретов не читает и не печатает: .env не трогается вообще.

set -euo pipefail

REPO="/opt/kino/project"
UNITS=(kino-dispatcher kino kino-admin)
RESTART=0
[ "${1:-}" = "--restart" ] && RESTART=1

say() { printf '%s\n' "$*"; }
die() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

# --- 1. Проверка окружения ---------------------------------------------------
[ -d "$REPO/.git" ] || die "нет git-копии: $REPO/.git"
cd "$REPO"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || die "ветка $BRANCH, ожидалась main"

if [ -n "$(git status --porcelain)" ]; then
  say "ВНИМАНИЕ: в рабочем дереве есть локальные изменения:"
  git status --short
  die "деплой поверх грязного дерева запрещён — сначала разберитесь с изменениями"
fi

BEFORE="$(git rev-parse --short HEAD)"
say "до: HEAD=$BEFORE"

# --- 2. Обновление -----------------------------------------------------------
git pull --ff-only origin main || die "git pull не прошёл (не fast-forward?)"

AFTER="$(git rev-parse --short HEAD)"
say "после: HEAD=$AFTER"

if [ "$BEFORE" = "$AFTER" ]; then
  say "код уже актуален, обновлять нечего"
else
  say "обновлено: $BEFORE → $AFTER"
  git --no-pager log --oneline "$BEFORE..$AFTER"
fi

# --- 3. Проверка целостности кода -------------------------------------------
# Маркер фикса NameError в run_dispatcher: без него диспетчер падает на старте.
grep -q "len(cfg.clients)" pro_code/dispatcher.py \
  || die "pro_code/dispatcher.py не содержит фикс len(cfg.clients) — код неполный"

# Диспетчер импортирует project_ruch — каталог обязан быть на месте.
[ -d project_ruch ] || die "нет каталога project_ruch (импорт RuchDb упадёт)"

# Пакет kino живёт в project_auto — юниты kino/kino-admin стартуют оттуда.
[ -d project_auto/kino ] || die "нет каталога project_auto/kino"

say "проверка кода: OK"

# --- 4. Рестарт --------------------------------------------------------------
if [ "$RESTART" = "1" ]; then
  sudo systemctl restart "${UNITS[@]}"
  sleep 3
  say "--- статус ---"
  systemctl --no-pager -l status "${UNITS[@]}" 2>/dev/null \
    | grep -E "●|Active:|Status:" || true

  # Диспетчер обязан быть active: иначе NameError/ConfigError на старте.
  for u in "${UNITS[@]}"; do
    state="$(systemctl is-active "$u" 2>/dev/null || true)"
    if [ "$state" != "active" ]; then
      say "ВНИМАНИЕ: $u в состоянии '$state'"
      say "лог: sudo journalctl -u $u -n 50 --no-pager"
    fi
  done
  say "перезапущено: ${UNITS[*]}"
else
  say "готово. рестарт вручную: sudo systemctl restart ${UNITS[*]}"
fi