"""БД расписания: слоты, резерв, журнал прогонов (SQLite).

Отдельная база `data/kino.db`, а не `data/state.json`: state.json — про
контент (история ссылок и сигнатур), kino.db — про расписание, резерв и
журнал прогонов. Разделение осознанное и зафиксировано в
`docs/schedule-slots-design.md` (раздел 2), переезжать не должно.

Модуль — тонкий слой над `sqlite3`: схема, чтение/запись слотов, резерв и
записи `runs`. Планировщик (шаг 8) решает, *когда* срабатывает слот; здесь
только хранение. Вся логика чистых функций (окна, пауза) осталась в
`kino/schedule.py` и не тронута.

Соединение не живёт глобально: каждый вызов открывает своё, потому что
`serve` тикает раз в минуту, а sqlite3-соединение в тредах/форках ведёт себя
непредсказуемо. Стоимость открытия файла на тик — микросекунды.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: Сетка владельца: 13 постов в неделю. 0 = понедельник (как datetime.weekday()).
DEFAULT_SLOTS: tuple[tuple[int, str], ...] = (
    (0, "11:05"),
    (0, "20:50"),
    (1, "21:20"),
    (2, "11:05"),
    (2, "20:50"),
    (3, "21:20"),
    (4, "11:05"),
    (4, "20:50"),
    (4, "23:40"),
    (5, "16:40"),
    (5, "22:00"),
    (6, "12:00"),
    (6, "19:50"),
)

#: Человеческие имена дней для логов и админки (индекс = weekday).
WEEKDAY_NAMES: tuple[str, ...] = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

#: Допустимые исходы прогона. Набор зафиксирован в спецификации; лишнее
#: значение в `runs.outcome` — ошибка кода, а не пользователя.
OUTCOMES: tuple[str, ...] = ("published", "reserve", "skipped", "rejected", "failed")

#: Хранение журнала прогонов, дней (schedule.retention_days по умолчанию).
DEFAULT_RETENTION_DAYS = 90

#: Ключ в meta: сетка уже засеяна. Нужен, чтобы seed не воскрешал удалённое.
_SEED_KEY = "seeded_at"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_slots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday     INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    time        TEXT    NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (weekday, time)
);

CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reserve (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    link        TEXT    NOT NULL UNIQUE,
    title       TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT '',
    format      TEXT    NOT NULL DEFAULT '',
    draft_json  TEXT    NOT NULL DEFAULT '',
    caption     TEXT    NOT NULL DEFAULT '',
    image_url   TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    used_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_reserve_expires ON reserve (expires_at);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id     INTEGER,
    slot_at     TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    outcome     TEXT    NOT NULL,
    reason      TEXT    NOT NULL DEFAULT '',
    link        TEXT,
    format      TEXT,
    kind        TEXT,
    -- Ручной прогон (кнопка, `kino run --publish`) отличается от сеточного:
    -- в журнале «что было в 21:20» и «что я нажал руками» — разные события,
    -- и путать их в отчёте нельзя.
    is_manual   INTEGER NOT NULL DEFAULT 0 CHECK (is_manual IN (0, 1)),
    manual_reason TEXT NOT NULL DEFAULT '',
    -- Длина подписи и оценка судьи: экран «Прогоны» рендерится из одной
    -- таблицы, без чтения CSV статистики и логов.
    caption_len   INTEGER,
    quality_score REAL,
    FOREIGN KEY (slot_id) REFERENCES schedule_slots (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON runs (started_at);
CREATE INDEX IF NOT EXISTS idx_runs_slot ON runs (slot_id, slot_at);
"""

#: Колонки, добавленные после первого релиза схемы: (имя, SQL-тип с умолчанием).
#: `CREATE TABLE IF NOT EXISTS` на существующей БД их не создаст, поэтому
#: `ensure_schema` досоздаёт их через `ALTER TABLE`. Порядок важен только для
#: читаемости; SQLite добавляет колонки в конец.
_RUNS_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("is_manual", "INTEGER NOT NULL DEFAULT 0"),
    ("manual_reason", "TEXT NOT NULL DEFAULT ''"),
    ("caption_len", "INTEGER"),
    ("quality_score", "REAL"),
)


class ScheduleDbError(Exception):
    """Ошибка БД расписания: негодный путь, битая схема, мусор во входе."""


@dataclass(frozen=True)
class Slot:
    """Слот публикации: день недели + время, с отметкой включённости."""

    id: int
    weekday: int
    time: str
    enabled: bool

    @property
    def weekday_name(self) -> str:
        return WEEKDAY_NAMES[self.weekday]

    def __str__(self) -> str:
        mark = "" if self.enabled else " (выкл)"
        return f"{self.weekday_name} {self.time}{mark}"


@dataclass(frozen=True)
class ReserveItem:
    """Пост в резерве: уже собранный и проверенный судьёй, но не опубликованный."""

    id: int
    link: str
    title: str
    source: str
    format: str
    draft_json: str
    caption: str
    image_url: str
    kind: str
    created_at: str
    expires_at: str
    used_at: str | None

    @property
    def is_expired(self) -> bool:
        """Просрочен ли пост (по UTC-времени, как и все ISO-метки в БД)."""
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        return expires <= datetime.now(expires.tzinfo)


@dataclass(frozen=True)
class RunRecord:
    """Запись журнала прогонов — то, что админка покажет вместо чтения логов."""

    id: int
    slot_id: int | None
    slot_at: str
    started_at: str
    finished_at: str | None
    outcome: str
    reason: str
    link: str | None
    format: str | None
    kind: str | None
    #: Прогон запущен руками (кнопка админки, `kino run --publish`), а не слотом.
    is_manual: bool = False
    #: Почему запущен ручной прогон: «кнопка: финальный пост», «ручной запуск».
    manual_reason: str = ""
    #: Длина подписи в символах — для экрана прогонов без чтения CSV.
    caption_len: int | None = None
    #: Оценка судьи 1–10. None — судья не вернул оценку или прогон не дошёл.
    quality_score: float | None = None


# --- низкий уровень ----------------------------------------------------------


def _now_iso() -> str:
    """Текущий момент в ISO с таймзоной.

    Все метки в БД — aware ISO: наивное время в SQLite переживает перевод
    часов молча, и «слот 3 часа назад» становится «слот сейчас».
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_time(raw: str) -> str:
    """`'9:05'` / `' 09:05 '` -> `'09:05'`. Мусор — ScheduleDbError.

    Валидация здесь, а не только в админке: слот с временем `'25:00'` в БД
    планировщик молча не найдёт никогда, и день окажется пустым без причины.
    """
    if not isinstance(raw, str):
        raise ScheduleDbError(f"время слота должно быть строкой, получено {type(raw).__name__}")
    text = raw.strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ScheduleDbError(f'время слота {raw!r}: ожидается "HH:MM"')
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ScheduleDbError(f"время слота {raw!r}: часы 00-23, минуты 00-59")
    return f"{hour:02d}:{minute:02d}"


def normalize_weekday(raw: int | str) -> int:
    """День недели: 0 = понедельник. Принимает число 0..6 или `'пн'`…`'вс'`."""
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in WEEKDAY_NAMES:
            return WEEKDAY_NAMES.index(key)
        if key.isdigit():
            return normalize_weekday(int(key))
        raise ScheduleDbError(
            f"день недели {raw!r}: ожидается 0-6 или одно из {', '.join(WEEKDAY_NAMES)}"
        )
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ScheduleDbError(f"день недели {raw!r}: ожидается целое 0-6")
    if not (0 <= raw <= 6):
        raise ScheduleDbError(f"день недели {raw}: допустимо 0-6 (0 = понедельник)")
    return raw


class ScheduleDb:
    """Обёртка над соединением с `data/kino.db`.

    Использование:

        db = ScheduleDb(Path("data/kino.db"))
        db.ensure_schema()
        db.seed_defaults()

    Схема создаётся явно (`ensure_schema`) или лениво при первом запросе —
    импорт модуля никогда не пишет на диск.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._schema_ready = False

    # --- соединение ---

    def _connect(self) -> sqlite3.Connection:
        """Новое соединение. Каталог создаётся, если его нет."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10.0)
        except (OSError, sqlite3.Error) as exc:
            raise ScheduleDbError(f"не открыть БД расписания {self.path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        # Внешние ключи в SQLite выключены по умолчанию — включаем на соединении,
        # иначе ON DELETE SET NULL в runs не работает.
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def ensure_schema(self) -> None:
        """Создаёт таблицы, если их нет, и досоздаёт новые колонки `runs`.

        Идемпотентно. Миграция нужна потому, что `CREATE TABLE IF NOT EXISTS`
        на уже существующей таблице ничего не делает: боевая БД на сервере
        создана до появления `is_manual` и остальных, и без `ALTER TABLE`
        запись прогона упала бы на «no such column».
        """
        if self._schema_ready:
            return
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                self._migrate_runs(conn)
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не создать схему в {self.path}: {exc}") from exc
        self._schema_ready = True
        logger.debug("БД расписания готова: %s", self.path)

    @staticmethod
    def _migrate_runs(conn: sqlite3.Connection) -> None:
        """Досоздаёт недостающие колонки `runs`. Ничего не удаляет."""
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        for name, sql_type in _RUNS_MIGRATIONS:
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {sql_type}")
            logger.info("БД расписания: добавлена колонка runs.%s", name)

    def _conn(self) -> sqlite3.Connection:
        self.ensure_schema()
        return self._connect()

    # --- слоты ---

    def list_slots(self, *, enabled_only: bool = False) -> list[Slot]:
        """Все слоты, отсортированные по дню и времени."""
        sql = "SELECT id, weekday, time, enabled FROM schedule_slots"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY weekday, time"
        try:
            with self._conn() as conn:
                rows = conn.execute(sql).fetchall()
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не прочитать слоты: {exc}") from exc
        return [
            Slot(id=row["id"], weekday=row["weekday"], time=row["time"], enabled=bool(row["enabled"]))
            for row in rows
        ]

    def get_slot(self, weekday: int | str, time: str) -> Slot | None:
        """Слот по дню и времени, или None."""
        day = normalize_weekday(weekday)
        stamp = normalize_time(time)
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT id, weekday, time, enabled FROM schedule_slots "
                    "WHERE weekday = ? AND time = ?",
                    (day, stamp),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не прочитать слот {day} {stamp}: {exc}") from exc
        if row is None:
            return None
        return Slot(
            id=row["id"],
            weekday=row["weekday"],
            time=row["time"],
            enabled=bool(row["enabled"]),
        )

    def add_slot(self, weekday: int | str, time: str, *, enabled: bool = True) -> Slot:
        """Добавляет слот. Дубль в тот же день и минуту — ScheduleDbError."""
        day = normalize_weekday(weekday)
        stamp = normalize_time(time)
        now = _now_iso()
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "INSERT INTO schedule_slots (weekday, time, enabled, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (day, stamp, 1 if enabled else 0, now, now),
                )
                slot_id = int(cur.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            raise ScheduleDbError(
                f"слот {WEEKDAY_NAMES[day]} {stamp} уже существует"
            ) from exc
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не добавить слот: {exc}") from exc
        logger.info("слот добавлен: %s %s", WEEKDAY_NAMES[day], stamp)
        return Slot(id=slot_id, weekday=day, time=stamp, enabled=enabled)

    def set_enabled(self, weekday: int | str, time: str, enabled: bool) -> Slot:
        """Включает/выключает слот. Отсутствующий слот — ScheduleDbError."""
        slot = self.get_slot(weekday, time)
        if slot is None:
            raise ScheduleDbError(
                f"слота {WEEKDAY_NAMES[normalize_weekday(weekday)]} {normalize_time(time)} нет"
            )
        try:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE schedule_slots SET enabled = ?, updated_at = ? WHERE id = ?",
                    (1 if enabled else 0, _now_iso(), slot.id),
                )
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не изменить слот {slot}: {exc}") from exc
        return Slot(id=slot.id, weekday=slot.weekday, time=slot.time, enabled=enabled)

    def delete_slot(self, weekday: int | str, time: str) -> bool:
        """Удаляет слот. True — удалён, False — такого не было."""
        day = normalize_weekday(weekday)
        stamp = normalize_time(time)
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "DELETE FROM schedule_slots WHERE weekday = ? AND time = ?",
                    (day, stamp),
                )
                deleted = cur.rowcount > 0
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не удалить слот: {exc}") from exc
        if deleted:
            logger.info("слот удалён: %s %s", WEEKDAY_NAMES[day], stamp)
        return deleted

    def clear_weekday(self, weekday: int | str) -> int:
        """Убирает все слоты дня. Возвращает число удалённых."""
        day = normalize_weekday(weekday)
        try:
            with self._conn() as conn:
                cur = conn.execute("DELETE FROM schedule_slots WHERE weekday = ?", (day,))
                count = cur.rowcount
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не очистить день {day}: {exc}") from exc
        return count

    def count_slots(self) -> int:
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM schedule_slots").fetchone()
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не посчитать слоты: {exc}") from exc
        return int(row["n"])

    def seed_defaults(
        self, slots: Iterable[tuple[int, str]] = DEFAULT_SLOTS, *, force: bool = False
    ) -> int:
        """Заполняет сетку владельца. Возвращает число реально добавленных.

        Первый seed пишет всю сетку и ставит отметку в `meta`. Повторный seed
        ничего не делает — и это принципиально: `INSERT OR IGNORE` сам по себе
        вернул бы удалённый владельцем слот (его строки уже нет, ограничение
        не срабатывает). Решение из спецификации, раздел 2: удалённый слот не
        восстанавливается сам.

        `force=True` — осознанный повтор (например, после `kino schedule clear`),
        снова добавляет недостающие слоты сетки.
        """
        now = _now_iso()
        rows = [(normalize_weekday(day), normalize_time(stamp), now, now) for day, stamp in slots]
        try:
            with self._conn() as conn:
                seeded = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (_SEED_KEY,)
                ).fetchone()
                if seeded is not None and not force:
                    logger.debug("seed пропущен: сетка уже засеяна %s", seeded["value"])
                    return 0
                before = conn.execute("SELECT COUNT(*) AS n FROM schedule_slots").fetchone()["n"]
                conn.executemany(
                    "INSERT OR IGNORE INTO schedule_slots (weekday, time, enabled, created_at, updated_at) "
                    "VALUES (?, ?, 1, ?, ?)",
                    rows,
                )
                after = conn.execute("SELECT COUNT(*) AS n FROM schedule_slots").fetchone()["n"]
                conn.execute(
                    "INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at",
                    (_SEED_KEY, now, now),
                )
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не заполнить слоты: {exc}") from exc
        added = int(after) - int(before)
        logger.info("seed слотов: добавлено %d, всего %d", added, after)
        return added

    # --- резерв ---

    def add_reserve(
        self,
        *,
        link: str,
        title: str = "",
        source: str = "",
        format: str = "",
        draft_json: str = "",
        caption: str = "",
        image_url: str = "",
        kind: str = "",
        ttl_hours: int = 48,
        max_items: int = 5,
    ) -> int | None:
        """Кладёт пост в резерв. None — не влез (лимит) или такая ссылка уже есть."""
        if not link:
            raise ScheduleDbError("резерв: пустая ссылка")
        if ttl_hours <= 0:
            raise ScheduleDbError(f"резерв: ttl_hours={ttl_hours}, нужно > 0")
        if max_items <= 0:
            raise ScheduleDbError(f"резерв: max_items={max_items}, нужно > 0")
        now = datetime.now().astimezone()
        expires = now + timedelta(hours=ttl_hours)
        try:
            with self._conn() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM reserve WHERE used_at IS NULL"
                ).fetchone()["n"]
                if int(count) >= max_items:
                    logger.info("резерв полон (%d), пост не добавлен: %s", count, link)
                    return None
                cur = conn.execute(
                    "INSERT OR IGNORE INTO reserve "
                    "(link, title, source, format, draft_json, caption, image_url, kind, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        link,
                        title,
                        source,
                        format,
                        draft_json,
                        caption,
                        image_url,
                        kind,
                        now.isoformat(timespec="seconds"),
                        expires.isoformat(timespec="seconds"),
                    ),
                )
                if cur.rowcount == 0:
                    logger.debug("резерв: ссылка уже есть, пропуск: %s", link)
                    return None
                return int(cur.lastrowid or 0)
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не добавить в резерв: {exc}") from exc

    def list_reserve(self, *, only_available: bool = False) -> list[ReserveItem]:
        """Посты резерва: по умолчанию все, включая использованные и просроченные."""
        sql = "SELECT * FROM reserve"
        if only_available:
            sql += " WHERE used_at IS NULL AND expires_at > ?"
        sql += " ORDER BY created_at, id"
        params: tuple[Any, ...] = (_now_iso(),) if only_available else ()
        try:
            with self._conn() as conn:
                rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не прочитать резерв: {exc}") from exc
        return [_reserve_from_row(row) for row in rows]

    def take_reserve(self) -> ReserveItem | None:
        """Берёт самый старый непросроченный пост и помечает использованным.

        None — резерв пуст или всё просрочено. Взятие атомарно: `UPDATE ... WHERE
        used_at IS NULL` отсекает двойную выдачу, если два процесса дёрнут
        резерв одновременно.
        """
        now = _now_iso()
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM reserve WHERE used_at IS NULL AND expires_at > ? "
                    "ORDER BY created_at, id LIMIT 1",
                    (now,),
                ).fetchone()
                if row is None:
                    return None
                cur = conn.execute(
                    "UPDATE reserve SET used_at = ? WHERE id = ? AND used_at IS NULL",
                    (now, row["id"]),
                )
                if cur.rowcount == 0:  # гонка: кто-то успел раньше
                    return None
                item = _reserve_from_row(row)
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не взять из резерва: {exc}") from exc
        logger.info("резерв: взят пост %s (%s)", item.link, item.title[:60])
        return ReserveItem(**{**item.__dict__, "used_at": now})

    def purge_reserve(self) -> int:
        """Удаляет использованные и просроченные посты. Возвращает число."""
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "DELETE FROM reserve WHERE used_at IS NOT NULL OR expires_at <= ?",
                    (_now_iso(),),
                )
                return cur.rowcount
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не почистить резерв: {exc}") from exc

    # --- журнал прогонов ---

    def start_run(
        self,
        *,
        slot_id: int | None,
        slot_at: str,
        is_manual: bool = False,
        manual_reason: str = "",
    ) -> int:
        """Открывает запись прогона. `outcome` заполняется в `finish_run`.

        Схема требует NOT NULL на outcome, поэтому стартовая запись пишется
        как `failed` с причиной «не завершён» — если процесс упадёт посреди
        прогона, в журнале останется честная запись, а не пустая дыра.

        `is_manual` отделяет ручной запуск от сеточного: в журнале это разные
        события, и «почему в 14:30 был пост» должно отвечаться без догадок.
        У ручного прогона `slot_id` обычно None, а `slot_at` — момент запуска.
        """
        started = _now_iso()
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "INSERT INTO runs (slot_id, slot_at, started_at, outcome, reason, "
                    "is_manual, manual_reason) "
                    "VALUES (?, ?, ?, 'failed', 'прогон не завершён', ?, ?)",
                    (slot_id, slot_at, started, 1 if is_manual else 0, manual_reason),
                )
                return int(cur.lastrowid or 0)
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не открыть запись прогона: {exc}") from exc

    def finish_run(
        self,
        run_id: int,
        *,
        outcome: str,
        reason: str = "",
        link: str | None = None,
        format: str | None = None,
        kind: str | None = None,
        caption_len: int | None = None,
        quality_score: float | None = None,
    ) -> None:
        """Закрывает запись прогона исходом.

        `caption_len` и `quality_score` необязательны: у `skipped` подписи нет,
        у прогона без судьи нет оценки — None честнее нуля, ноль в отчёте
        выглядел бы как «оценка 0» и тянул среднее вниз.
        """
        if outcome not in OUTCOMES:
            raise ScheduleDbError(
                f"неизвестный исход прогона {outcome!r}, допустимые: {', '.join(OUTCOMES)}"
            )
        try:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE runs SET finished_at = ?, outcome = ?, reason = ?, link = ?, "
                    "format = ?, kind = ?, caption_len = ?, quality_score = ? WHERE id = ?",
                    (
                        _now_iso(), outcome, reason, link, format, kind,
                        caption_len, quality_score, run_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не закрыть запись прогона {run_id}: {exc}") from exc

    def slot_done(self, slot_id: int, slot_at: str) -> bool:
        """Отработан ли слот на эту дату.

        Отработанным считается прогон с исходом, отличным от «не завершён»
        (то есть любой закрытый). Незакрытая запись не блокирует слот: иначе
        падение процесса навсегда съело бы этот слот до конца недели.
        """
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM runs "
                    "WHERE slot_id = ? AND slot_at = ? AND outcome IN "
                    "('published', 'reserve', 'skipped', 'rejected')",
                    (slot_id, slot_at),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не проверить слот {slot_id}: {exc}") from exc
        return int(row["n"]) > 0

    def recent_runs(self, limit: int = 20) -> list[RunRecord]:
        """Последние записи журнала — для админки («что было в 21:20»)."""
        if limit <= 0:
            raise ScheduleDbError(f"limit={limit}, нужно > 0")
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не прочитать журнал прогонов: {exc}") from exc
        return [
            RunRecord(
                id=row["id"],
                slot_id=row["slot_id"],
                slot_at=row["slot_at"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                outcome=row["outcome"],
                reason=row["reason"],
                link=row["link"],
                format=row["format"],
                kind=row["kind"],
                is_manual=bool(row["is_manual"]),
                manual_reason=row["manual_reason"] or "",
                caption_len=row["caption_len"],
                quality_score=row["quality_score"],
            )
            for row in rows
        ]

    def purge_runs(self, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
        """Удаляет записи журнала старше `retention_days`. Возвращает число."""
        if retention_days <= 0:
            raise ScheduleDbError(f"retention_days={retention_days}, нужно > 0")
        cutoff = (datetime.now().astimezone() - timedelta(days=retention_days)).isoformat(
            timespec="seconds"
        )
        try:
            with self._conn() as conn:
                cur = conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
                return cur.rowcount
        except sqlite3.Error as exc:
            raise ScheduleDbError(f"не почистить журнал прогонов: {exc}") from exc


def _reserve_from_row(row: sqlite3.Row) -> ReserveItem:
    return ReserveItem(
        id=row["id"],
        link=row["link"],
        title=row["title"],
        source=row["source"],
        format=row["format"],
        draft_json=row["draft_json"],
        caption=row["caption"],
        image_url=row["image_url"],
        kind=row["kind"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        used_at=row["used_at"],
    )


def format_week(slots: list[Slot]) -> str:
    """Сетка одной строкой на день — для лога при старте и для админки."""
    if not slots:
        return "(пусто)"
    lines: list[str] = []
    for day in range(7):
        stamps = [s.time + ("" if s.enabled else "(выкл)") for s in slots if s.weekday == day]
        lines.append(f"{WEEKDAY_NAMES[day]}: {', '.join(stamps) if stamps else '—'}")
    return "\n".join(lines)