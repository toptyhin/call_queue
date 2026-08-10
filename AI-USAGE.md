# AI-USAGE.md

Как использовались ИИ-инструменты при выполнении задания: чем генерировали, что переписывали, как проверяли. Правила ведения этого файла — в `AGENTS.md` → «Ведение AI-USAGE.md».

## 1. Инструменты

| Инструмент / модель | Где применялся |
|---|---|
| Cursor + Kimi K3 (агентский режим) | Ранний дизайн решений, `ASSUMPTIONS.md` / `AGENTS.md`, каркас репозитория |
| Cursor agents (параллельные треки) | Реализация ядра API, вебхуков, анализов/SSE, веб-UI, моков, тестов и документации разными агентами в одной сессии |
| Cursor + кодоген (`make codegen` / datamodel-codegen) | Скелет Pydantic `analysis_result.py` из JSON Schema |

## 2. Принято без правок

- Happy-path чанки мок-провайдера (`apps/mocks`) — детерминированный стрим полей для стенда.
- Части веб-scaffolding (Vite/React layout, базовая разметка страницы разбора) — с минимальной подгонкой под `data-state` и API-клиент.
- Сгенерированный скелет `apps/api/app/generated/analysis_result.py` — без ручной правки модели (источник — schema + codegen).

## 3. Переписано после генерации

- **Webhook apply pipeline** (`webhook_apply.py` + router) — единый конвейер: advisory lock → дедуп → матч/fallback-link → sequence/terminal-гарды → UPDATE; первая генерация смешивала ответственность и обходила буфер.
- **Claim SQL** — фильтры, `SKIP LOCKED`, inactive campaign → пустой результат; HTTP-слой только мапит `P0002` в 404.
- **RLS policies** — FORCE RLS, `webhook_events` через linked attempt, `crm_outbox` deny для `app_user`, `app_webhook` BYPASSRLS.
- **Stream consumer prefix-skip** — при reconnect пропускаются первые K сохранённых чанков с проверкой префикса; mismatch → error, partial сохраняется.
- **Middleware** — чистый ASGI (correlation-id / request-id), не `BaseHTTPMiddleware` (проблемы со стримингом/телом).
- **Pytest** — отдельный asyncpg pool на event loop (`pool-per-loop` в conftest), иначе гонки/закрытые loop между тестами.

## 4. Написано / решено вручную (в диалоге с агентом)

- **Все продуктовые решения в `ASSUMPTIONS.md`** принимались интерактивно: агент предлагал варианты, решения утверждались человеком. Ключевые развилки: привязка `provider_call_id`, буфер вебхуков вместо `404`, RLS, transactional outbox, SSE через журнал чанков.
- **Смена стека** Node/Fastify → FastAPI — решение человека.
- **`claim_next_contact` SQL**, терминальный триггер (`0003_terminal_trigger.sql`), sequence/terminal-гарды — тонкие места, писались/правились вручную поверх черновиков агента.
- `ASSUMPTIONS.md`, `AGENTS.md` — сгенерированы агентом, проверены человеком.

## 5. Как проверялось

- `make test` — 9 pytest-тестов ядра в `test_core.py` (healthz, claim/lock, foreign campaign 404, webhook signature/dedup/buffer/link, terminal+outbox, sequence/terminal guards, abort/provider-link) плюс тесты анализов (`test_analyses.py`).
- `docker compose up -d` → `GET /healthz` → `200`.
- E2E разбора против моков: create → SSE → `done`; число вызовов мок-провайдера = 1 (генерация не перезапускается при reconnect клиента).
- Web: атрибут `data-state` на контейнере присутствует и отражает приоритет состояний UI.
- Нагрузка на 2 млн контактов: claim p95 ≈ 17 мс (цель ≤ 100); webhook 50 rps / 60 с — p99 ≈ 5.5 мс, 0 ошибок (цель ≤ 1 с).

## 6. Известные риски

- **Prefix mismatch на resume**: при недетерминированном стендовом провайдере (не наш мок) reconnect может завершиться `error` с сохранённым partial.
- **Нагрузочные цифры** (`make load-*`) зависят от машины; SLA из ТЗ не гарантированы «из коробки» на слабом хосте.
- **CRM poller** — single-row polling (`LIMIT 1` за тик); при всплеске outbox доставка растянется по времени, записи не дропаются.
