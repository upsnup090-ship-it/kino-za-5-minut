# Кино за 5 минут — команды разработки и деплоя

PYTHON ?= venv/bin/python
PIP ?= venv/bin/pip

.PHONY: setup setup-dev test test-integration typecheck run run-live serve serve-once draft deploy stop logs clean

## Установка окружения
setup:
	python3 -m venv venv
	$(PIP) install -r requirements.txt
	@echo "Не забудь: cp docs/env.example .env и заполни значения"

## Установка окружения разработки (с mypy)
setup-dev:
	python3 -m venv venv
	$(PIP) install -r requirements-dev.txt
	@echo "Не забудь: cp docs/env.example .env и заполни значения"

## Тесты
test:
	$(PYTHON) -m pytest -q

## Интеграционные тесты
test-integration:
	$(PYTHON) -m pytest tests/test_pipeline_integration.py tests/test_formats_integration.py -v

## Статическая проверка типов (конфиг — mypy.ini)
typecheck:
	$(PYTHON) -m mypy kino scripts tests

## Dry-run одного поста (не публикует, LLM вызывается)
run:
	$(PYTHON) -m kino run

## Боевой запуск одного поста
run-live:
	$(PYTHON) -m kino run --publish

## Режим 24/7 (foreground, для systemd или ручного запуска)
serve:
	$(PYTHON) -m kino serve

## Один проход serve (отладка)
serve-once:
	$(PYTHON) -m kino serve --once

## Черновик по теме
draft:
	$(PYTHON) -m kino draft $(TOPIC)

## Установка systemd-юнитов на VPS (требует root)
deploy:
	sudo cp deploy/kino.service deploy/kino-admin.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable --now kino kino-admin

## Обновление кода на сервере (git pull в боевой каталог + рестарт)
update:
	git pull origin main
	sudo -u kino ./deploy/deploy.sh --restart

## Только pull + проверка, без рестарта
update-code:
	sudo -u kino ./deploy/deploy.sh

## Статус и здоровье обоих сервисов
health:
	@systemctl --no-pager -l status kino kino-admin | grep -E "●|Active:|Status:" || true
	@echo "--- последние события ---"
	@journalctl -u kino -n 10 --no-pager -o cat | tail -10

## Остановка сервисов
stop:
	sudo systemctl stop kino kino-admin

## Логи сервиса (реальное время)
logs:
	journalctl -u kino -f

## Очистка окружения
clean:
	rm -rf venv __pycache__ .pytest_cache