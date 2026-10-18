"""Загрузка промптов из prompts/ с подстановкой плейсхолдеров.

Промпты лежат в текстовых файлах, чтобы стиль канала правился без правки кода.
Если файл отсутствует или пуст — берётся встроенный дефолт, чтобы пайплайн
не падал из-за пропавшего файла.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "prompts"
FORMATS_DIR = PROMPTS_DIR / "formats"

# Встроенные дефолты — используются, если файла нет.
DEFAULT_DRAFT_SYSTEM = (
    "Ты — редактор русскоязычного Telegram-канала о кино. "
    "Отвечай одним JSON-объектом без markdown. "
    "Поля headline и text пиши только на русском."
)
DEFAULT_DRAFT_USER = (
    "Формат: {format}\n"
    "Спецификация формата:\n{format_spec}\n"
    "Источник: {source}\n"
    "Заголовок: {title}\n"
    "Описание: {snippet}\n"
    "Верни JSON: headline, text, question, film, director, cast, release, studio, wiki, "
    "fact_source, news, quote, trailer, video, collage, production, interview, opinion, "
    "poll_options, video_url, infographic, image_candidates, announcement, review, "
    "comparison, riddle."
)
DEFAULT_JUDGE_SYSTEM = (
    "Ты — строгий фактчекер и редактор Telegram-канала о кино. "
    "Работай только с материалом ниже. Верни строго JSON без markdown."
)
DEFAULT_JUDGE_USER = (
    "Источник: {source}\n"
    "Заголовок: {title}\n"
    "Описание: {snippet}\n"
    "Формат: {format}\n"
    "Спецификация формата:\n{format_spec}\n\n"
    "Черновик: {draft}\n\n"
    "Верни JSON: verdict, reason, headline, text, key_moment, question, hashtags, "
    "film, director, cast, release, studio, wiki, drop_fields."
)


def load_prompt(name: str, default: str) -> str:
    """Читает prompts/<name>.txt. Нет файла или пусто — возвращает default."""
    path = PROMPTS_DIR / f"{name}.txt"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.debug("промпт %s не найден, берём дефолт", path)
        return default
    except OSError as exc:
        logger.warning("не удалось прочитать промпт %s: %s", path, exc)
        return default
    if not text:
        logger.warning("промпт %s пуст, берём дефолт", path)
        return default
    return text


def render(template: str, **values: object) -> str:
    """Подставляет значения в шаблон.

    Отсутствующие ключи не роняют рендер: неизвестный плейсхолдер остаётся
    как есть, чтобы ошибка была видна в логе, а не в трейсбеке.
    """
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            logger.warning("в промпте нет значения для {%s}", key)
            return "{" + key + "}"

    return template.format_map(_SafeDict(values))


def format_spec(fmt: str) -> str:
    """Читает спецификацию формата из prompts/formats/<fmt>.txt.

    Файла нет или он пуст — громко логируем и отдаём generic fallback,
    чтобы прогон не упал, но дрейст был виден. Реальные spec-файлы
    обязаны покрывать все форматы из config/formats.yaml (проверяется
    тестом-реестром).
    """
    path = FORMATS_DIR / f"{fmt}.txt"
    default = (
        f"Спецификация формата '{fmt}' недоступна. "
        "Следуй общему описанию формата в системном сообщении."
    )
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.error("спецификация формата не найдена: %s", path)
        return default
    except OSError as exc:
        logger.error("не удалось прочитать спецификацию формата %s: %s", path, exc)
        return default
    if not text:
        logger.error("спецификация формата пуста: %s", path)
        return default
    return text


def draft_system() -> str:
    return load_prompt("draft_system", DEFAULT_DRAFT_SYSTEM)


def draft_user(**values: object) -> str:
    return render(load_prompt("draft_user", DEFAULT_DRAFT_USER), **values)


def judge_system() -> str:
    return load_prompt("judge_system", DEFAULT_JUDGE_SYSTEM)


def judge_user(**values: object) -> str:
    return render(load_prompt("judge_user", DEFAULT_JUDGE_USER), **values)