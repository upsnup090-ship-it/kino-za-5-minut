# TO-DO: Форматы постов для реализации

## Готово — все 15 форматов
1. facts — интересный факт → случайная вариация
2. news — оперативная новость → случайная вариация
3. when — новость с датой → случайная вариация
4. poll — опрос → poll
5. quote — цитата → случайная вариация
6. trailer — трейлер → link
7. video — видео → link (YouTube-превью)
8. infographic — инфографика → QuickChart → photo
9. collage — коллаж → gallery (карусель)
10. behind_scenes — закулисье → случайная вариация
11. interview — интервью/диалог → article
12. announcement — анонс события → случайная вариация
13. review — рецензия → article
14. comparison — сравнение → gallery (2 картинки)
15. riddle — загадка → случайная вариация
16. opinion — мнение/аналитика → article

## Что изменилось в этом PR
- config/formats.yaml — 16 форматов с весами
- prompts/draft_system.txt — полные инструкции для всех 16 форматов
- prompts/draft_user.txt — все поля в запросе
- kino/infographic.py — QuickChart-рендер
- kino/draft.py, kino/judge.py — salvage-ключи для всех новых полей
- kino/pipeline.py — _FORMAT_TO_KIND (9 маппингов), инфографика, коллаж, сравнение

## Свободные форматы (7)
facts, news, when, quote, behind_scenes, announcement, riddle
→ случайный выбор вариации через post_variations

## Форматы с фиксированной вариацией (9)
poll→poll, trailer→link, video→link, infographic→photo,
collage→gallery, interview→article, review→article,
comparison→gallery, opinion→article

## Мелкие доработки
- обрезка подписи
- потеря текста в poll
- stale lock
- мёртвые переменные окружения