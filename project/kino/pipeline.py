"""Связывает шаги пайплайна: collect → choose → draft → judge → variation → caption → publish."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kino.caption import build_caption as build_caption_text
from kino.choose import Choice, choose
from kino.collect import CollectResult, collect
from kino.config import Config
from kino.deadline import Deadline, check
from kino.draft import DraftError, _human_date, draft
from kino.franchise import config_path, detect, load_franchises
from kino.horizon import HorizonResult, collect_horizon
from kino.image import ImageResult  # noqa: F401 — реэкспорт для _store_reserve
from kino.judge import judge_with_analysis
from kino.publish import publish
from kino.variation import (  # noqa: F401 — реэкспорт для тестов и скриптов
    FORMAT_TO_KIND as _FORMAT_TO_KIND,
    VARIATIONS as _VARIATIONS,
    _gallery_images,
    _pick_image,
    _publish_article,
    _valid_url,
    pick_variation,
    resolve_variation,
)
from kino.state import (
    State,
    StateLock,
    load_state,
    record_choice,
    record_franchises,
    save_state,
)
from kino.stats_collector import collect_format_stats, collect_judge_performance_stats

logger = logging.getLogger(__name__)

#: Корень проекта — для относительных путей конфигов (словарь франшиз).
ROOT = Path(__file__).resolve().parents[1]

@dataclass
class RunResult:
    caption: str
    image_url: str
    format: str
    published: bool
    dry_run: bool
    verdict: str = "accept"
    #: Вариация, которой пост реально ушёл (после всех фолбэков), и та, что была
    #: выбрана изначально. По логу и результату должно быть видно одно и то же.
    kind: str = ""
    requested_kind: str = ""
    #: Прогон не успел завершить пост из-за исчерпания бюджета времени.
    aborted: bool = False
    abort_reason: str = ""
    #: Ссылка материала, из которого собран пост. Нужна журналу прогонов
    #: (`runs.link`) и админке: «что было в 21:20» должно отвечать без
    #: чтения state.json и логов.
    link: str = ""
    #: Оценка судьи 1–10. None — судья не вернул оценку, прогон прерван или
    #: отбит до судейства. Ноль здесь был бы враньём: он тянет среднее вниз.
    quality_score: float | None = None
    #: Классы материала и число допущенных форматов (маршрутизация, Фаза 5).
    #: Нужны dry-run выводу: «класс материала и допущенные форматы» (§7).
    kinds: tuple[str, ...] = ()
    allowed_count: int = 0


def _reject_run(cfg: Config, choice: Choice, state: State, reason: str, *, dry_run: bool) -> None:
    """Обрабатывает отклонённый судьёй черновик: не публикует, помечает ссылку.

    Ссылку записываем в state как использованную, иначе следующий прогон
    выберет ту же новость, снова упрётся в reject — и так по кругу. В dry-run
    state не трогаем: это гарантия режима, и reject не должен её нарушать.
    """
    logger.warning("судья отклонил черновик, публикация отменена: %s", reason or "без причины")
    if dry_run:
        logger.info("dry-run: state не изменён (reject)")
    else:
        record_choice(
            state,
            choice.item.link,
            _signature_of(choice),
            cfg.settings.state_max_links,
            cfg.settings.state_max_signatures,
        )
        try:
            save_state(
                cfg.state_path,
                state,
                cfg.settings.state_max_links,
                cfg.settings.state_max_signatures,
            )
        except OSError as exc:
            logger.warning("не удалось сохранить state после reject: %s", exc)
    try:
        from kino.publish import notify_admin

        notify_admin(cfg, f"судья отклонил новость ({reason or 'без причины'}): {choice.item.title[:120]}")
    except Exception:  # noqa: BLE001 — уведомление не должно ронять прогон
        pass


def _notify_low_quality(
    cfg: Config,
    choice: Choice,
    final_data: dict,
    *,
    dry_run: bool,
) -> None:
    """Точечное уведомление админу при низкой оценке качества.

    Срабатывает только когда судья реально оценил пост ниже порога (5/10).
    В dry-run не шлём — это гарантия режима. Ошибки доставки не роняют прогон.
    """
    if dry_run:
        return
    analysis = final_data.get("analysis") or {}
    judge_feedback = analysis.get("judge_feedback") or {}
    score = judge_feedback.get("quality_score")
    if not isinstance(score, int) or score >= 5:
        return
    recommendations = analysis.get("recommendations") or []
    first = recommendations[0] if recommendations else "рекомендаций нет"
    try:
        from kino.publish import notify_admin

        notify_admin(
            cfg,
            f"низкое качество {score}/10: {choice.item.title[:100]} — {first}",
        )
    except Exception:  # noqa: BLE001 — уведомление не должно ронять прогон
        pass


def _quality_of(final_data: dict) -> float | None:
    """Оценка судьи 1–10 из `final_data`, или None.

    Судья кладёт оценку в `quality`, анализатор — в `analysis.judge_feedback
    .quality_score`. Порядок проверки: сначала прямой `quality` (его пишет
    `judge()`), потом анализатор. Мусор и выход за 1..10 — None: в журнале
    прогонов оценка должна быть либо честной, либо отсутствовать.
    """
    for raw in (
        final_data.get("quality"),
        (final_data.get("analysis") or {}).get("judge_feedback", {}).get("quality_score"),
    ):
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if 1.0 <= value <= 10.0:
            return value
    return None


def _post_hash(caption: str, image_url: str, kind: str, link_url: str) -> str:
    """Тот же хэш, что считает publish._hash_post_content.

    Нужен в pipeline, чтобы записать хэш в state после успешной отправки.
    Импортируем из publish, а не дублируем формулу — иначе два места
    разъедутся и дедупликация перестанет совпадать.
    """
    from kino.publish import _hash_post_content

    return _hash_post_content(caption, image_url, kind, link_url)


def record_format_stat(cfg: Config, choice: Choice) -> None:
    """Дописывает строку в CSV статистики форматов. Ошибки записи не роняют пайплайн.

    Колонки (§7, Фаза 5): `timestamp, format, source, quality_score, kinds,
    allowed_count`. `kinds` — классы материала (через `|`), `allowed_count` —
    сколько форматов было допущено. По этим двум видно мёртвые форматы и
    долю фолбэка за неделю, не читая логов.

    Старый CSV (4 колонки) мигрируется на месте: заголовок дополняется
    `kinds,allowed_count`, старые строки — пустыми полями. Иначе строки
    разной ширины били бы CSV (ровно та ошибка, из-за которой
    `stats_collector.py` вынесен в отдельный файл).
    """
    if not cfg.stats_path:
        return
    try:
        cfg.stats_path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not cfg.stats_path.exists()
        if not new_file:
            _migrate_stats_header(cfg.stats_path)
        with cfg.stats_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(
                    ["timestamp", "format", "source", "quality_score", "kinds", "allowed_count"]
                )
            writer.writerow(
                [
                    datetime.now(timezone.utc).isoformat(),
                    choice.format,
                    choice.item.source,
                    choice.quality_score,
                    "|".join(getattr(choice, "kinds", ())),
                    getattr(choice, "allowed_count", 0),
                ]
            )
    except OSError as exc:
        logger.warning("не удалось записать статистику формата: %s", exc)


def _migrate_stats_header(path: Path) -> None:
    """Дополняет старый 4-колоночный заголовок до 6 колонок.

    Миграция на месте: читаем, дописываем заголовок и пустые поля к старым
    строкам, пишем обратно. Если заголовок уже 6 колонок — ничего не делаем.
    Пустой файл (есть, но без строк) — оставляем как есть: `record_format_stat`
    допишет строку, но заголовок так и не появится; это редкий край, и
    переписывать пустой файл ради него не стоит.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if not lines:
        return
    header = next(csv.reader([lines[0]]))
    if len(header) >= 6:
        return
    if header[:4] != ["timestamp", "format", "source", "quality_score"]:
        # Не наш старый формат — не трогаем, чтобы не испортить чужой файл.
        return
    # Дополняем заголовок и каждую строку до 6 полей.
    out = [",".join(header + ["kinds", "allowed_count"])]
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = next(csv.reader([line]))
        fields += [""] * (6 - len(fields))
        out.append(",".join(fields))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def build_caption(draft_data: dict, image_url: str, cfg: Config, kind: str = "",
                  include_facts: bool = False, fmt: str = "",
                  pub_date: str = "") -> str:
    """Собирает подпись из черновика (единственный стиль, см. kino.caption).

    kind и include_facts влияют на состав блоков: опрос не дублирует вопрос,
    инфографика без картинки отдаёт цифры текстом. fmt — формат контента:
    длинные форматы (LONG_FORMATS) не режутся редакторским капом.
    pub_date — дата материала, по ней caption.py сверяет год в fact_source.
    """
    return build_caption_text(draft_data, image_url, cfg, kind=kind,
                              include_facts=include_facts, fmt=fmt,
                              pub_date=pub_date)


def run(cfg: Config, dry_run: bool = True, *, deadline: Deadline | None = None) -> RunResult:
    """Полный прогон одного поста.

    Бюджеты времени: сбор живёт в собственном дедлайне (ретраи источников
    из-за лимитов вроде Reddit-429 не крадут время у LLM-фазы), а черновик →
    судья → публикация получают свежий полный (`deadline_seconds`). При
    исчерпании прогон завершается с `aborted=True`, а публикация готового
    поста не отменяется — она важнее добивки картинки. Явно переданный
    deadline управляет всем прогоном единым бюджетом (тесты, serve).
    """
    explicit_deadline = deadline is not None
    if deadline is None:
        deadline = Deadline(cfg.settings.deadline_seconds)

    def _abort(reason: str) -> RunResult:
        logger.warning("прогон прерван: %s", reason)
        return RunResult(
            caption="",
            image_url="",
            format="",
            published=False,
            dry_run=dry_run,
            aborted=True,
            abort_reason=reason,
        )

    lock = StateLock(cfg.state_path.with_suffix(".lock"))
    if not lock.acquire():
        raise RuntimeError("другой запуск уже выполняется (lock занят)")

    try:
        state = load_state(cfg.state_path)
        used_links = set(state.used_links)
        used_sigs = state.used_sigs

        if not check(deadline, step="сбор"):
            return _abort("бюджет исчерпан до сбора")
        # Сбору — собственный бюджет: ретраи Reddit из-за лимитов на IP
        # (боевой прогон 18.10) не должны съедать время черновика и судьи.
        collect_deadline = (
            deadline if explicit_deadline else Deadline(cfg.settings.deadline_seconds)
        )
        # Оба источника дают «совместимый по смыслу» результат: у CollectResult
        # и HorizonResult общий контракт items/broken/total/fresh_count, но
        # формально это разные датаклассы — аннотируем объединением типов.
        collected: CollectResult | HorizonResult
        if cfg.source_mode == "horizon":
            collected = collect_horizon(
                cfg.horizon_dir,
                cfg.horizon_summaries_dir,
                run=cfg.horizon_run,
                hours=cfg.horizon_hours,
                timeout=collect_deadline.slice(cfg.horizon_timeout),
            )
        else:
            collected = collect(cfg, deadline=collect_deadline)
        if not collected.items:
            return _abort(f"сбор не дал записей (broken={len(collected.broken)})")
        if not explicit_deadline:
            # LLM-фазе (черновик → судья → публикация) — свежий полный бюджет,
            # каким бы долгим ни был сбор.
            deadline = Deadline(cfg.settings.deadline_seconds)
        try:
            choice: Choice = choose(
                collected.items,
                cfg,
                used_links,
                used_sigs,
                franchise_history=[tuple(x) for x in state.franchise_history],  # type: ignore[misc]
            )
        except RuntimeError as exc:
            # Сбор есть, но ни одна запись не киношная — штатный пропуск
            # поста (как DraftError): служба живёт, админ узнаёт причину.
            logger.error("тема не выбрана, пост пропущен: %s", exc)
            if not dry_run:
                from kino.publish import notify_admin

                notify_admin(cfg, f"пост не опубликован: тема не выбрана ({exc})")
            return _abort(f"тема не выбрана: {exc}")
        logger.info("тема выбрана: %s | формат: %s",
                    choice.item.title[:80], choice.format)

        record_format_stat(cfg, choice)

        if not check(deadline, step="черновик"):
            return _abort("бюджет исчерпан до черновика")
        try:
            draft_data = draft(choice, cfg, deadline=deadline)
        except DraftError as exc:
            # LLM недоступен/ошибся — не креш под cron и serve, а штатный
            # пропуск поста: служба живёт, админ узнаёт о причине.
            logger.error("черновик не сгенерирован, пост пропущен: %s", exc)
            if not dry_run:
                from kino.publish import notify_admin

                notify_admin(cfg, f"пост не опубликован: черновик не сгенерирован ({exc})")
            return _abort(f"черновик не сгенерирован: {exc}")
        if not check(deadline, step="судья"):
            return _abort("бюджет исчерпан до судьи")
        # Заменяем обычный judge на версию с анализом
        start_time = datetime.now()
        final_data = judge_with_analysis(choice, draft_data, cfg, deadline=deadline)
        end_time = datetime.now()
        judge_time = (end_time - start_time).total_seconds()
        
        # Собираем статистику качества (отдельный файл, не format_stats.csv).
        # cfg.stats_path is None — штатное состояние тестов и скриптов:
        # CSV не пишется вовсе (п.38), иначе тесты мусорят в боевой logs/.
        stats_dir = cfg.stats_path.parent if cfg.stats_path else None
        if stats_dir is not None and "analysis" in final_data:
            analysis = final_data["analysis"]
            draft_metrics = analysis.get("draft_metrics", {})
            judge_feedback = analysis.get("judge_feedback", {})

            collect_format_stats(
                choice.format,
                choice.item.source,
                judge_time,
                judge_feedback.get("quality_score", 0),
                judge_feedback.get("is_sad", False),
                draft_metrics,
                judge_feedback,
                overall_quality=analysis.get("overall_quality"),
                recommendations_count=len(analysis.get("recommendations", [])),
                stats_dir=stats_dir,
            )

            # Собираем статистику производительности судьи
            collect_judge_performance_stats(
                judge_time,
                judge_feedback.get("quality_score", 0),
                judge_feedback.get("reasoning_length", 0),
                0,  # prompt_tokens - не имеем доступа здесь
                0,  # completion_tokens - не имеем доступа здесь
                stats_dir=stats_dir,
            )

        verdict = str(final_data.get("verdict", "accept"))
        if verdict == "reject":
            reason = str(final_data.get("reason", "") or "").strip()
            _reject_run(cfg, choice, state, reason, dry_run=dry_run)
            return RunResult(
                caption="",
                image_url="",
                format=choice.format,
                published=False,
                dry_run=dry_run,
                verdict=verdict,
                link=choice.item.link,
                kinds=getattr(choice, "kinds", ()),
                allowed_count=getattr(choice, "allowed_count", 0),
            )

        # Точечное уведомление о низком качестве: оценка судьи есть и ниже
        # порога. Не спам — срабатывает редко, по делу. Админ видит оценку и
        # первую рекомендацию, чтобы понять, что с текстом не так.
        _notify_low_quality(cfg, choice, final_data, dry_run=dry_run)

        wiki_title = str(final_data.get("wiki", "")).strip()
        plan = resolve_variation(cfg, choice, final_data, wiki_title, deadline)
        kind = plan.kind
        requested_kind = plan.requested_kind
        image = plan.image
        link_url = plan.link_url
        extra_images = plan.extra_images
        poll_q = plan.poll_question
        poll_opts = plan.poll_options
        include_facts = plan.include_facts
        published = False

        # Подпись строится после всех превращений kind: лимит выбирает
        # build_caption (1024 для фото/карусели, 4096 для текста/ссылки/опроса),
        # а в опросе вопрос не дублируется — для этого сюда и приходит kind.
        caption = build_caption(
            final_data,
            image.url,
            cfg,
            kind=kind,
            include_facts=include_facts,
            fmt=choice.format,
            # Дата материала: по ней caption.py сверяет год в fact_source.
            # Тот же источник, что у промптов черновика и судьи.
            pub_date=_human_date(choice.item, cfg.settings.timezone),
        )

        if not dry_run:
            post_hash = _post_hash(caption, image.url, kind, link_url)
            published = publish(
                cfg,
                caption,
                image.url if kind != "text" else "",
                dry_run=False,
                kind=kind,
                link_url=link_url,
                extra_images=extra_images if kind == "gallery" else None,
                poll_question=poll_q if kind == "poll" else "",
                poll_options=poll_opts if kind == "poll" else None,
                seen_hashes=set(state.recent_post_hashes),
            )
            if published:
                record_choice(
                    state,
                    choice.item.link,
                    _signature_of(choice),
                    cfg.settings.state_max_links,
                    cfg.settings.state_max_signatures,
                )
                # Хэш поста — второй рубеж дедупликации: ссылка могла ещё не
                # попасть в used_links, но пост уже вышел в канал.
                if post_hash not in state.recent_post_hashes:
                    state.recent_post_hashes.append(post_hash)
                state.recent_post_hashes = state.recent_post_hashes[
                    -cfg.settings.state_max_signatures:
                ]
                # Франшизный кулдаун: запоминаем, про что вышел пост. Только
                # при реальной публикации — dry-run и reject не тратят
                # кулдаун, иначе тема «сгорала» бы без поста.
                _record_post_franchises(cfg, state, choice)
                # Резерв: пост ушёл, но готовый черновик сохраняем как
                # страховку от пустого слота. Только в режиме слотов —
                # в режиме windows резерв не используется.
                if cfg.settings.schedule_mode == "slots":
                    _store_reserve(cfg, choice, final_data, caption, image.url, kind)
                save_state(
                    cfg.state_path,
                    state,
                    cfg.settings.state_max_links,
                    cfg.settings.state_max_signatures,
                )

        return RunResult(
            caption=caption,
            image_url=image.url,
            format=choice.format,
            published=published,
            dry_run=dry_run,
            verdict=verdict,
            kind=kind,
            requested_kind=requested_kind,
            link=choice.item.link,
            quality_score=_quality_of(final_data),
            kinds=getattr(choice, "kinds", ()),
            allowed_count=getattr(choice, "allowed_count", 0),
        )
    finally:
        lock.release()


def _store_reserve(
    cfg: Config,
    choice: Choice,
    draft_data: dict[str, Any],
    caption: str,
    image_url: str,
    kind: str,
) -> None:
    """Кладёт готовый пост в резерв — страховку от пустого слота.

    Второй черновик не генерируется: в резерв идёт только то, что уже
    собрано и проверено судьёй. Сбой записи не роняет прогон — пост уже
    вышел.
    """
    try:
        from kino.reserve import store

        store(
            cfg,
            link=choice.item.link,
            title=choice.item.title,
            source=choice.item.source,
            format=choice.format,
            draft_data=draft_data,
            caption=caption,
            image_url=image_url if kind != "text" else "",
            kind=kind,
        )
    except Exception as exc:  # noqa: BLE001 — пост уже вышел, резерв вторичен
        logger.warning("резерв: не сохранён (%s)", exc)


def _record_post_franchises(cfg: Config, state: State, choice: Choice) -> None:
    """Пишет франшизы опубликованного поста в историю state.

    Ошибка словаря не должна ронять уже вышедший пост: публикация состоялась,
    кулдаун — вспомогательное правило.
    """
    try:
        franchises = load_franchises(config_path(cfg.settings.franchises_path, ROOT))
        if not franchises:
            return
        found = detect(f"{choice.item.title} {choice.item.snippet}", franchises)
        if not found:
            return
        record_franchises(
            state,
            [f.key for f in found],
            datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        logger.info("франшизы поста: %s", ", ".join(str(f) for f in found))
    except Exception as exc:  # noqa: BLE001 — пост уже вышел, правило вторично
        logger.warning("франшизы: не записаны (%s)", exc)


def _signature_of(choice: Choice) -> list[str]:
    from kino.choose import _signature

    return _signature(choice.item.title)