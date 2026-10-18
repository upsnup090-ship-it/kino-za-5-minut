"""Инфографика через QuickChart — бесплатный рендер диаграмм.

LLM возвращает объект infographic: {title, facts: [{label, value}]}.
Модуль строит URL с горизонтальной диаграммой и возвращает ссылку на PNG.
Бот в QuickChart не ходит: картинку по URL скачивает Telegram (как и wiki).

Оформление намеренно консервативное:
- JSON-конфиг не может содержать JS-функции (callback/formatter строкой Chart.js
  игнорирует), поэтому значение вшивается прямо в подпись категории:
  "Бюджет — $200 млн", а длина бара — из распарсенного числа;
- только стандартные опции Chart.js v2 и дефолтный рендер QuickChart: без
  параметров version/chartJsUrl/plugins, поведение которых без живого запроса
  не подтверждено;
- светлый фон и тёмный текст: с тёмной темой при неподдержанном параметре
  фона текст стал бы невидимым.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

_QC_BASE = "https://quickchart.io/chart"
_DEFAULT_WIDTH = 900
_DEFAULT_HEIGHT = 500

_COLORS = {
    "bar": "#e2b04a",     # золотой — киноварь афиш
    "text": "#1f2937",    # тёмно-серый текст на белом фоне
    "grid": "#e5e7eb",
}

# Максимум фактов на одном графике: больше — нечитаемо и URL раздувается.
_MAX_FACTS = 5
# Метки укорачиваются до сборки URL (label[:28], value[:20]), поэтому запас
# по длине — предохранитель, а не основное ограничение:percent-encoding
# раздувает кириллицу примерно втрое.
_MAX_URL = 2000


def _chart_config(title: str, facts: list[dict[str, str]]) -> dict[str, Any]:
    """Конфиг Chart.js v2: горизонтальные бары, значения внутри подписей.

    Длины баров — только относительные: подписи несут настоящие значения,
    шкала скрыта. Если величины несопоставимы (разброс больше 50 раз —
    «$200 млн» рядом с «8.5/10» или годом из «осень 2026»), пропорция
    вводила бы в заблуждение, и бары делаются ровными: получается
    карточка-фактура, а не фальшивая диаграмма.
    """
    labels = [f"{f['label'][:28]} — {f['value'][:20]}" for f in facts]
    numeric = [_parse_number(f["value"]) for f in facts]
    spread = max(numeric) / min(numeric) if numeric and min(numeric) > 0 else 1.0
    if spread > 50:
        numeric = [1.0] * len(numeric)

    return {
        "type": "horizontalBar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "data": numeric,
                    "backgroundColor": _COLORS["bar"],
                    "borderWidth": 0,
                    "barPercentage": 0.8,
                }
            ],
        },
        "options": {
            "title": {
                "display": True,
                "text": title[:80],
                "fontColor": _COLORS["text"],
                "fontSize": 18,
                "padding": 20,
            },
            "legend": {"display": False},
            "scales": {
                # Ось значений скрыта: бары показывают только масштаб, цифры
                # уже стоят в подписях категорий.
                "xAxes": [{"display": False, "gridLines": {"display": False}}],
                "yAxes": [
                    {
                        "ticks": {"fontColor": _COLORS["text"], "fontSize": 14},
                        "gridLines": {"display": False},
                    }
                ],
            },
        },
    }


_NUMBER_RE = re.compile(r"(\d+\.?\d*)")


def _parse_number(text: str) -> float:
    """Достаёт число из строки вида '$200 млн', '85%', '7.5/10' для масштаба."""
    cleaned = text.replace(",", ".").replace(" ", "")
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return 1.0
    num = float(match.group(1))
    if re.search(r"млрд|billion", cleaned, re.IGNORECASE):
        num *= 1000
    elif re.search(r"тыс|thousand", cleaned, re.IGNORECASE):
        num /= 1000
    return max(0.01, num)


def build_url(title: str, facts: list[dict[str, str]],
              width: int = _DEFAULT_WIDTH, height: int = _DEFAULT_HEIGHT) -> str:
    """URL QuickChart для инфографики. '' — если собрать нечего или слишком длинно.

    Пустой URL трактуется пайплайном как «графика нет»: цифры уходят в текст.
    """
    facts = facts[:_MAX_FACTS]
    if not facts:
        return ""

    for count in (len(facts), max(2, len(facts) - 1), 2):
        config = json.dumps(
            _chart_config(title, facts[:count]),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        url = (
            f"{_QC_BASE}"
            f"?c={quote(config, safe='')}"
            f"&w={width}&h={height}"
        )
        if len(url) <= _MAX_URL:
            return url

    logger.warning("инфографика: URL длиннее %d символов, уходим в текст", _MAX_URL)
    return ""


def validate(data: Any) -> tuple[str, list[dict[str, str]]]:
    """Проверяет и нормализует infographic-данные от LLM.

    Возвращает (title, facts). Пустой результат — если данных нет или фактов
    меньше двух (один факт — это не график, а нормальный текст).
    """
    if not isinstance(data, dict):
        return "", []
    title = str(data.get("title", "") or "").strip()
    raw_facts = data.get("facts")
    if not isinstance(raw_facts, list) or len(raw_facts) < 2:
        return "", []
    facts: list[dict[str, str]] = []
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "") or "").strip()
        value = str(item.get("value", "") or "").strip()
        if label and value:
            facts.append({"label": label, "value": value})
    if len(facts) < 2:
        return "", []
    return title, facts[:_MAX_FACTS]