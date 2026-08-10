# Sound — сервис управления обзвоном

Тестовое задание: очередь обзвона, приём событий провайдера телефонии по вебхукам, LLM-разбор звонка со стримингом в веб-интерфейс.

Стек: FastAPI + asyncpg + PostgreSQL 16 (RLS) · React 18 + TypeScript + Vite + Tailwind · Docker Compose.

Решения по неоднозначностям задания — в [`ASSUMPTIONS.md`](ASSUMPTIONS.md). Инструкции для агентов — в [`AGENTS.md`](AGENTS.md). Использование ИИ — в [`AI-USAGE.md`](AI-USAGE.md).

## Контракт запуска

Из корня репозитория:

```bash
cp .env.example .env   # если ещё нет
docker compose up -d
```

| Сервис | Адрес |
|--------|--------|
| API | `http://localhost:8080` |
| Health | `GET /healthz` → `200` |
| PostgreSQL | `localhost:54329`, `postgres:postgres@…/postgres` |
| Web UI | `http://localhost:5173` |

Миграции применяются автоматически при старте API (до открытия порта). Отклонение от этого контракта = несданное задание.

### Dev-моки (CRM + LLM-провайдер)

```bash
docker compose --profile dev up -d
```

Моки слушают `:8090` и не стартуют без профиля `dev` (чтобы не мешать проверочному стенду).

## Переменные окружения

Обязательные по контракту (см. `.env.example`):

| Переменная | Назначение | Dev-заглушка |
|---|---|---|
| `WEBHOOK_SECRET` | HMAC-SHA256 для `X-Signature` вебхуков | `dev-webhook-secret` |
| `JWT_SECRET` | Подпись JWT | `dev-jwt-secret-change-me-32bytes!!` |
| `CRM_URL` | Endpoint CRM для outbox-поллера | `http://mocks:8090/crm` |
| `PROVIDER_URL` | Базовый URL LLM-провайдера (ТЗ 7.1) | `http://mocks:8090` |

Compose также задаёт DSN БД, пароли ролей `app_user` / `app_webhook` и `DEV_TOKEN_ENABLED` (по умолчанию `true`).

## Что реализовано

**Часть 1 — ядро обзвона**
- `POST /rpc/claim_next_contact` — выдача контакта через SQL-функцию (`SKIP LOCKED`, RLS)
- Вебхуки `POST /webhooks/calls` — HMAC по сырому телу, дедуп, sequence/terminal-гарды, буфер неизвестных `call_id`
- `provider-link` / `abort`, терминальный триггер, CRM transactional outbox + фоновый поллер, reaper зависших попыток

**Часть 2 — разбор звонка**
- `POST /api/analyses`, SSE `GET /api/analyses/:id/stream` (`Last-Event-ID`), cancel
- Стрим-консюмер к провайдеру, журнал чанков в БД, partial-результат
- Web UI с `data-state` на контейнере (queued / streaming / reconnecting / done / partial / error / cancelled / idle)

## Сиды

```bash
make seed
```

Создаёт demo org/campaign/контакты и completed-попытку с транскриптом (id печатаются в stdout JSON). В `docker compose up` сиды **не** применяются.

## Dev-токен

При `DEV_TOKEN_ENABLED=true`:

```bash
curl -s -X POST http://localhost:8080/dev/token \
  -H 'content-type: application/json' \
  -d '{"sub":"dev","org_id":"00000000-0000-4000-8000-000000000001","role":"worker"}'
```

Роли: `worker` | `authenticated`.

## Ручные сценарии

1. **Claim** — после `make seed`: `POST /rpc/claim_next_contact` с JWT и `campaign_id` из сида → контакт + `attempt_id` (или `contact: null`, если очередь пуста / кампания не `active`).
2. **Webhook** — `POST /webhooks/calls` с валидной `X-Signature: sha256=…` по сырому телу; невалидная подпись → `401`. События до `provider-link` буферизуются (`200`), после линка применяются по `sequence`.
3. **Analysis stream** — `docker compose --profile dev up -d`, сид, JWT, `POST /api/analyses` с `call_attempt_id` completed-попытки → SSE до `done`; UI на `:5173` показывает `data-state`.

## Нагрузка и SLA

Цели задания: claim ≤ 100 мс p95 на ~2 млн контактов; вебхук ≤ 1 с p99 при 50 rps.

```bash
make load-seed          # COUNT=2000000 по умолчанию
make load-claim         # параллельные воркеры
make load-webhook       # ~50 rps
```

Результаты на машине разработки (после `make load-seed` на 2 млн контактов):

| Метрика | Цель | Факт |
|---------|------|------|
| claim p95 (10 воркеров, 200 запросов) | ≤ 100 мс | **17.3 мс** |
| webhook p99 (50 rps, 60 с, 0 ошибок) | ≤ 1 с | **5.5 мс** |

Цифры зависят от машины; прогоняйте локально после `load-seed`.

## Тесты

```bash
make test
```

Pytest по `apps/api` (ядро claim/webhook/terminal + happy-path анализа). Нужна доступная БД (compose) и при необходимости моки для стрим-теста.

Прочее:

```bash
make codegen   # Pydantic из packages/shared/analysis-result.schema.json
make up / up-dev / down / logs
```

## Структура репозитория

```
apps/api/       FastAPI, миграции, фоновые задачи
apps/web/       React + Vite UI разбора
apps/mocks/     мок LLM-провайдера и CRM (profile: dev)
packages/shared/ analysis-result.schema.json
spec/           OpenAPI (api + provider)
scripts/        seed, load-*
```

## Известные ограничения

- CRM-поллер за тик обрабатывает одну запись outbox (для объёма задания достаточно; при высокой нагрузке — узкое место).
- Resume стрима провайдера сверяет префикс сохранённых чанков: на недетерминированном провайдере возможен `prefix mismatch` с сохранением partial (мок стенда детерминирован).
- Нагрузочные цифры SLA не зафиксированы в CI — проверяются скриптами `make load-*` на конкретной машине.
