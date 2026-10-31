# Кино за 5 минут — команды разработки и деплоя

PYTHON ?= venv/bin/python
PIP ?= venv/bin/pip

.PHONY: setup test typecheck run run-live serve serve-once draft deploy install clean test-integration

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

## Установка systemd-юнита на VPS (требует root)
deploy:
	sudo cp deploy/kino.service /etc/systemd/system/kino.service
	sudo systemctl daemon-reload
	sudo systemctl enable --now kino

## Остановка сервиса
stop:
	sudo systemctl stop kino

## Логи сервиса
logs:
	journalctl -u kino -f

## Очистка окружения
clean:
	rm -rf venv __pycache__ .pytest_cache

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

## Установка systemd-юнита на VPS (требует root)
deploy:
	sudo cp deploy/kino.service /etc/systemd/system/kino.service
	sudo systemctl daemon-reload
	sudo systemctl enable --now kino

## Остановка сервиса
stop:
	sudo systemctl stop kino

## Логи сервиса
logs:
	journalctl -u kino -f

## Очистка окружения
clean:
	rm -rf venv __pycache__ .pytest_cache