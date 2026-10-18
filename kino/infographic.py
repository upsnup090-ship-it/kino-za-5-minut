"""Инфографика через QuickChart — бесплатный рендер диаграмм.

LLM возвращает объект infographic: {title, facts: [{label, value}]}.
Модуль строит URL QuickChart с горизонтальной条形-dиаграммой и возвращает
URL готовой PNG-картинки. Никаких ключей не нужно — QuickChart бесплатен
для некоммерческого использования.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

# QuickChart — бесплатный API рендера диаграмм (Chart.js под капотом).
_QC_BASE = "https://quickchart.io/chart"
# Стандартный размер для Telegram-превью.
_DEFAULT_WIDTH = 800
_DEFAULT_HEIGHT = 420

# Цветовая схема в стиле кино: тёмный фон, золотой/белый текст.
_COLORS = {
    "bg": "#1a1a2e",        # тёмно-синий фон
    "bar": "#e2b04a",       # золотой бар
    "text": "#ffffff",      # белый текст
    "label": "#cccccc",     # серый для подписей
}


def _escape_js(text: str) -> str:
    """Экранирует строку для вставки в JS-код диаграммы."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _build_chart_config(title: str, facts: list[dict[str, str]]) -> dict[str, Any]:
    """Собирает конфиг горизонтальной条形-dиаграммы для QuickChart.

    facts: [{"label": "Бюджет", "value": "$200 млн"}, ...]
    """
    labels = [f["label"] for f in facts]
    values_raw = [f["value"] for f in facts]

    # Парсим числа из value для масштаба: "$200 млн" → 200, "85%" → 85
    numeric: list[float] = []
    for v in values_raw:
        num = _parse_number(v)
        numeric.append(num if num > 0 else 1.0)

    chart_config = {
        "type": "horizontalBar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "data": numeric,
                    "backgroundColor": _COLORS["bar"],
                    "borderColor": _COLORS["bar"],
                    "borderWidth": 0,
                }
            ],
        },
        "options": {
            "title": {
                "display": True,
                "text": title,
                "fontColor": _COLORS["text"],
                "fontSize": 18,
                "fontFamily": "Arial, sans-serif",
                "padding": 20,
            },
            "legend": {"display": False},
            "scales": {
                "xAxes": [
                    {
                        "ticks": {
                            "fontColor": _COLORS["label"],
                            "fontSize": 12,
                            "callback": (
                                "function(v){"
                                + "var orig=[" + ",".join(json.dumps(v) for v in values_raw) + "];"
                                + "return orig[this.index]||v;"
                                + "}"
                            ),
                        },
                        "gridLines": {"color": "rgba(255,255,255,0.1)"},
                    }
                ],
                "yAxes": [
                    {
                        "ticks": {
                            "fontColor": _COLORS["text"],
                            "fontSize": 13,
                            "mirror": False,
                            "padding": 10,
                        },
                        "gridLines": {"display": False},
                    }
                ],
            },
            "plugins": {
                "datalabels": {
                    "display": True,
                    "color": _COLORS["text"],
                    "font": {"size": 13, "weight": "bold"},
                    "anchor": "end",
                    "align": "start",
                    "offset": 4,
                    "formatter": (
                        "function(v){"
                        + "var orig=[" + ",".join(json.dumps(v) for v in values_raw) + "];"
                        + "return orig[this.$context.dataIndex]||v;"
                        + "}"
                    ),
                }
            },
            "layout": {"padding": {"left": 10, "right": 40, "top": 10, "bottom": 20}},
            "backgroundColor": _COLORS["bg"],
        },
    }
    return chart_config


def _parse_number(text: str) -> float:
    """Достаёт число из строки вида '$200 млн', '85%', '7.5/10'."""
    import re

    text = text.replace(",", ".").replace(" ", "")
    # Ищем число: целое или с плавающей точкой
    m = re.search(r"(\d+\.?\d*)", text)
    if not m:
        return 1.0
    num = float(m.group(1))
    # Множители: млн, млрд, тыс
    if re.search(r"млрд|billion|B", text, re.IGNORECASE):
        num *= 1000
    if re.search(r"млн|million|M", text, re.IGNORECASE):
        pass  # оставляем как есть для масштаба
    if re.search(r"тыс|thousand|K", text, re.IGNORECASE):
        num /= 1000
    return max(0.01, num)


def build_url(title: str, facts: list[dict[str, str]], width: int = _DEFAULT_WIDTH,
              height: int = _DEFAULT_HEIGHT) -> str:
    """Собирает URL QuickChart для инфографики.

    Возвращает готовую ссылку на PNG-картинку.
    """
    if not facts or len(facts) < 1:
        return ""
    chart_config = _build_chart_config(title, facts)
    config_json = json.dumps(chart_config, ensure_ascii=False, separators=(",", ":"))
    # QuickChart принимает c=<json> в query; библиотека chartjs-plugin-datalabels
    # нужна для подписей значений на барах.
    plugins = "chartjs-plugin-datalabels"
    return (
        f"{_QC_BASE}"
        f"?c={quote(config_json, safe='')}"
        f"&w={width}&h={height}"
        f"&devicePixelRatio=2.0"
        f"&backgroundColor={_COLORS['bg'].lstrip('#')}"
        f"&chartJsUrl=https://cdn.jsdelivr.net/npm/chart.js@2.9.4"
        f"&plugins={quote(plugins)}"
    )


def validate(data: dict | None) -> tuple[str, list[dict[str, str]]]:
    """Проверяет и нормализует infographic-данные от LLM.

    Возвращает (title, facts). Если данные негодные — оба пустые.
    """
    if not isinstance(data, dict):
        return "", []
    title = str(data.get("title", "") or "").strip()
    raw_facts = data.get("facts")
    if not isinstance(raw_facts, list) or len(raw_facts) < 2:
        return "", []
    facts: list[dict[str, str]] = []
    for f in raw_facts:
        if not isinstance(f, dict):
            continue
        label = str(f.get("label", "") or "").strip()
        value = str(f.get("value", "") or "").strip()
        if label and value:
            facts.append({"label": label, "value": value})
    if len(facts) < 2:
        return "", []
    return title, facts