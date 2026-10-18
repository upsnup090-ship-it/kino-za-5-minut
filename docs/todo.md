# TO-DO: Форматы постов для реализации

## Готово
1. Текстовый пост — facts/news/when + случайная вариация
2. Картинка + текст — facts/news/when/quote + photo/text
3. Статья — facts/news/interview + article→link
4. Картинка + цитата — quote + photo
5. Видео + текст — video формат → вариация link (YouTube-превью)
6. Опрос/викторина — poll формат → вариация poll
7. Инфографика + текст — infographic формат → QuickChart → photo
8. Коллаж/монтаж — collage формат → gallery (карусель)
9. Закулисье — behind_scenes формат → случайная вариация
10. Интервью/Диалог — interview формат → article

## Форматы к доработке (оставшиеся)
11. Анонс события — нужен формат announcement с датой/платформой
12. Рецензия — формат review с оценкой и вердиктом
13. Сравнительный анализ — формат comparison (два фильма/актёра)
14. Загадка/интерактив — формат riddle с ответом в комментариях
15. Мнение/аналитика — формат opinion с аналитическим разбором

## Что изменилось в коде
- config/formats.yaml — 11 форматов с весами (было 6)
- prompts/draft_system.txt — инструкции для всех 11 форматов + новые JSON-поля
- prompts/draft_user.txt — новые поля в запросе
- kino/infographic.py — новый модуль: QuickChart-рендер
- kino/draft.py — новые salvage-ключи: video_url, poll_options, infographic, image_candidates
- kino/judge.py — новые salvage-ключи + перенос полей через судью
- kino/pipeline.py — _FORMAT_TO_KIND маппинг + инфографика + коллаж

## Мелкие доработки (из старого списка)
- обрезка подписи
- потеря текста в poll
- stale lock
- мёртвые переменные окружения